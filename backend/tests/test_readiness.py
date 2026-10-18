"""公开存活/数据库就绪的 HTTP 契约；不读取本机配置或连接数据库。"""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health


@pytest.fixture
def client():
    # 避免 app.main 在导入时构造 Settings；只验证实际系统路由。
    application = FastAPI()
    application.include_router(health.router)
    with TestClient(application) as test_client:
        yield test_client


def test_health_stays_available_without_probing_database(client, monkeypatch):
    probe = Mock(side_effect=AssertionError("/health 不得访问数据库"))
    monkeypatch.setattr(health, "database_is_ready", probe)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    probe.assert_not_called()
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("ready,status_code,payload", [
    (True, 200, {"status": "ready", "database": "ok"}),
    (False, 503, {"status": "not_ready", "database": "unavailable"}),
])
def test_ready_is_anonymous_fixed_and_uncacheable(
    client, monkeypatch, ready, status_code, payload,
):
    probe = Mock(return_value=ready)
    monkeypatch.setattr(health, "database_is_ready", probe)

    response = client.get("/ready")

    assert "authorization" not in response.request.headers
    assert "cookie" not in response.request.headers
    assert response.status_code == status_code
    # 精确正文同时限制公开字段，不暴露连接地址、异常或内部诊断。
    assert response.json() == payload
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    probe.assert_called_once_with()


def test_ready_checks_each_request_without_reissuing_existing_cookie(client, monkeypatch):
    probe = Mock(side_effect=[False, True])
    monkeypatch.setattr(health, "database_is_ready", probe)
    client.cookies.set("eaap_session", "opaque-existing-browser-cookie")

    unavailable = client.get("/ready")
    ready = client.get("/ready")

    assert [unavailable.status_code, ready.status_code] == [503, 200]
    assert probe.call_count == 2
    for response in (unavailable, ready):
        assert response.headers["cache-control"] == "no-store"
        # 本测试只验证该路由不签发/续期 Cookie，不模拟 JWT 到期认证。
        assert "set-cookie" not in response.headers
        assert "opaque-existing-browser-cookie" not in response.text
    assert client.cookies.get("eaap_session") == "opaque-existing-browser-cookie"
