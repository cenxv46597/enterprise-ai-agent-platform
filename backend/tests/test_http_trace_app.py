"""Actual application composition; run with Settings env-file loading disabled.

The test wrapper must disable Settings.model_config['env_file'] before pytest.
This fixture isolates EAAP_ environment keys without displaying their values.
Authentication and validation use real routes with a forbidden-SQL session.
"""

import importlib
import json
import logging
import os
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app.api import health
from app.core.config import Settings
from app.db import session as database_session


ORIGIN = "http://127.0.0.1:5173"
TRACE_HEADER = "X-Trace-Id"


class NoDatabaseSession:
    def __getattr__(self, name):
        raise AssertionError("HTTP composition tests must never perform SQL")


class RecordCollector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def application(monkeypatch):
    # Fail before importing main rather than accidentally consulting local secrets.
    assert Settings.model_config.get("env_file") is None, "Run the no-env-file pytest wrapper"
    for name in tuple(os.environ):
        if name.startswith("EAAP_"):
            monkeypatch.delenv(name)
    main = importlib.import_module("app.main")
    controlled = Settings(
        _env_file=None,
        app_env="test",
        jwt_secret_key="controlled-http-trace-test-signing-key",
        web_origin=ORIGIN,
    )
    monkeypatch.setattr(main, "Settings", lambda: controlled)

    def forbid_engine():
        raise AssertionError("HTTP composition tests must never construct an engine")

    monkeypatch.setattr(database_session, "get_engine", forbid_engine)
    app = main.create_app()
    app.dependency_overrides[database_session.get_session] = lambda: NoDatabaseSession()
    collector = RecordCollector()
    logger = logging.getLogger("eaap.http")
    logger.addHandler(collector)
    try:
        yield SimpleNamespace(app=app, records=collector.records)
    finally:
        logger.removeHandler(collector)


def assert_correlated(application, response, *, status, method, route, outcome="completed"):
    assert response.status_code == status
    identifiers = response.headers.get_list(TRACE_HEADER)
    assert len(identifiers) == 1
    identifier = UUID(identifiers[0])
    assert identifier.version == 4
    assert len(application.records) == 1
    value = json.loads(application.records[0].getMessage())
    assert value["service"] == "eaap-api" and value["environment"] == "test"
    assert {key: value[key] for key in ("trace_id", "method", "route", "status_code", "outcome")} == {
        "trace_id": str(identifier), "method": method, "route": route,
        "status_code": status, "outcome": outcome,
    }
    assert "set-cookie" not in response.headers


def test_actual_health_retains_process_contract_and_has_trace(application):
    with TestClient(application.app) as client:
        response = client.get("/health")

    assert response.json() == {"status": "ok"}
    assert "cache-control" not in response.headers
    assert_correlated(application, response, status=200, method="GET", route="/health")


@pytest.mark.parametrize("available,status,body", [
    (True, 200, {"status": "ready", "database": "ok"}),
    (False, 503, {"status": "not_ready", "database": "unavailable"}),
])
def test_actual_ready_retains_safe_body_cache_and_trace(application, monkeypatch, available, status, body):
    monkeypatch.setattr(health, "database_is_ready", lambda: available)
    with TestClient(application.app) as client:
        response = client.get("/ready")

    assert response.json() == body
    assert response.headers["cache-control"] == "no-store"
    assert_correlated(application, response, status=status, method="GET", route="/ready")


def test_actual_auth_rejects_missing_credentials_without_sql_and_has_trace(application):
    with TestClient(application.app) as client:
        response = client.get("/api/v1/auth/me")

    assert response.json() == {"detail": "需要有效的登录凭证"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["www-authenticate"] == "Bearer"
    assert_correlated(application, response, status=401, method="GET", route="/api/v1/auth/me")


@pytest.mark.parametrize("action", ["login", "logout"])
def test_actual_web_routes_reject_missing_origin_without_cookie_or_sql(application, action):
    path = f"/api/v1/auth/web/{action}"
    with TestClient(application.app) as client:
        response = client.post(path, json={"username": "demo_a", "password": "controlled-password"})

    assert response.json() == {"detail": "网页请求来源无效"}
    assert response.headers["cache-control"] == "no-store"
    assert_correlated(application, response, status=403, method="POST", route=path)


def test_actual_web_validation_stays_generic_and_correlated(application):
    with TestClient(application.app) as client:
        response = client.post(
            "/api/v1/auth/web/login",
            headers={"Origin": ORIGIN, "X-EAAP-Web": "1"},
            json={"username": "demo_a", "password": "", "extra": "BODY_SENTINEL"},
        )

    assert response.json() == {"detail": "网页认证参数无效"}
    assert response.headers["cache-control"] == "no-store"
    assert_correlated(application, response, status=422, method="POST", route="/api/v1/auth/web/login")
    assert "BODY_SENTINEL" not in response.text
    assert "BODY_SENTINEL" not in application.records[0].getMessage()


def test_actual_middleware_stack_preserves_safe_500_and_single_trace(application):
    @application.app.get("/api/v1/controlled-failure")
    async def controlled_failure():
        raise RuntimeError("EXCEPTION_SENTINEL")

    with TestClient(application.app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/controlled-failure")

    assert response.text == "Internal Server Error"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert_correlated(application, response, status=500, method="GET", route="/api/v1/controlled-failure", outcome="error")
    assert "EXCEPTION_SENTINEL" not in application.records[0].getMessage()
