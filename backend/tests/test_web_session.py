"""Cookie 认证与 CSRF 的纯 HTTP 专项：真实签名、假用户会话，无数据库或模型。"""

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
import os
from time import time
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.api import auth
from app.auth import security, web_session
from app.auth.dependencies import get_current_user
from app.core.config import Settings
from app.db.models import User
from app.db.session import get_session
from app.observability.http import HttpTraceMiddleware, safe_unhandled_error


ORIGIN = "http://127.0.0.1:5173"
HEADERS = {"Origin": ORIGIN, "X-EAAP-Web": "1"}
LOGIN, RESTORE, LOGOUT = (f"/api/v1/auth/web/{action}" for action in ("login", "session", "logout"))
SECRET = "controlled-web-session-signing-key-for-tests"
PASSWORD = "controlled-web-password"
USER_A, USER_B = UUID(int=1), UUID(int=2)
COOKIE = web_session.WEB_SESSION_COOKIE


class MemorySession:
    def __init__(self) -> None:
        self.users = {
            identifier: User(id=identifier, username=username, password_hash="controlled-hash",
                             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
            for identifier, username in ((USER_A, "demo_a"), (USER_B, "demo_b"))
        }
        self.reads = 0

    def begin(self):
        return nullcontext()

    def scalar(self, statement):
        username = statement.compile().params["username_1"]
        return next((user for user in self.users.values() if user.username == username), None)

    def get(self, model, identifier):
        assert model is User
        self.reads += 1
        return self.users.get(identifier)

    def expunge(self, user):
        pass


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    state = SimpleNamespace(now=int(time()), session=MemorySession())
    monkeypatch.setattr(security, "_timestamp", lambda now: int(now.timestamp()) if now is not None else state.now)
    monkeypatch.setattr(web_session, "time", lambda: state.now)
    monkeypatch.setattr(auth, "verify_password", lambda value, hashed: value == PASSWORD and hashed == "controlled-hash")
    app = FastAPI()
    app.state.jwt_secret_key = SECRET
    app.state.web_origin = ORIGIN
    app.state.web_cookie_secure = False
    app.middleware("http")(web_session.private_api_responses)
    app.add_middleware(HttpTraceMiddleware)
    app.add_exception_handler(RequestValidationError, web_session.safe_web_validation)
    app.add_exception_handler(Exception, safe_unhandled_error)
    app.include_router(auth.router)
    app.dependency_overrides[get_session] = lambda: state.session

    @app.api_route("/api/v1/probe", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def protected(user: Annotated[User, Depends(get_current_user)]):
        return {"user_id": str(user.id)}

    state.app = app
    with TestClient(app) as client:
        state.client = client
        yield state


def token(web, user_id=USER_A, *, age=0, secret=SECRET) -> str:
    return security.issue_access_token(user_id, secret, now=datetime.fromtimestamp(web.now - age, timezone.utc))


def sign_in(web):
    response = web.client.post(LOGIN, headers=HEADERS, json={"username": " DEMO_A ", "password": PASSWORD})
    assert response.status_code == 200
    return response


def assert_unauthorized(response):
    assert response.status_code == 401
    assert response.json() == {"detail": "需要有效的登录凭证"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers


@pytest.mark.parametrize("secure", [False, True], ids=["local-http", "production-secure"])
def test_web_login_sets_only_persistent_host_cookie_and_returns_no_jwt(web, secure) -> None:
    web.app.state.web_cookie_secure = secure
    response = sign_in(web)
    body = response.json()
    assert body == {
        "user": {"id": str(USER_A), "username": "demo_a", "created_at": "2026-01-01T00:00:00Z"},
        "expires_at": web.now + 1800,
    }
    cookie = SimpleCookie(response.headers["Set-Cookie"])[COOKIE]
    assert cookie["httponly"] and cookie["samesite"].lower() == "strict"
    assert cookie["path"] == "/" and cookie["domain"] == ""
    assert bool(cookie["secure"]) is secure and int(cookie["max-age"]) == 1800
    assert int(parsedate_to_datetime(cookie["expires"]).timestamp()) == body["expires_at"]
    assert response.headers["Cache-Control"] == "no-store"
    assert cookie.value not in response.text and PASSWORD not in response.text


def test_restore_rechecks_user_and_keeps_original_expiry_without_refreshing_cookie(web) -> None:
    initial = sign_in(web).json()
    saved_cookie = web.client.cookies.get(COOKIE)
    web.now += 120
    with TestClient(web.app) as reopened:
        reopened.cookies.set(COOKIE, saved_cookie)
        restored = reopened.get(RESTORE)
    assert restored.status_code == 200 and restored.json() == initial
    assert "Set-Cookie" not in restored.headers and restored.headers["Cache-Control"] == "no-store"
    assert web.session.reads == 1
    assert web.client.get("/api/v1/probe").json() == {"user_id": str(USER_A)}


@pytest.mark.parametrize("problem", ["missing", "bad-signature", "expired", "user-gone"])
def test_restore_and_protected_get_reject_invalid_cookie_without_exposing_token(web, problem) -> None:
    value = None
    if problem == "bad-signature":
        value = token(web, secret="other-controlled-signing-key-for-tests")
    elif problem == "expired":
        value = token(web, age=1800)
    elif problem == "user-gone":
        value = token(web)
        del web.session.users[USER_A]
    if value is not None:
        web.client.cookies.set(COOKIE, value)
    for path in (RESTORE, "/api/v1/probe"):
        response = web.client.get(path)
        assert_unauthorized(response)
        if value is not None:
            assert value not in response.text


@pytest.mark.parametrize("headers", [
    {}, {"Origin": ORIGIN}, {"X-EAAP-Web": "1"},
    {"Origin": "http://127.0.0.1:8000", "X-EAAP-Web": "1"},
    {"Origin": "null", "X-EAAP-Web": "1"}, {"Origin": ORIGIN, "X-EAAP-Web": "0"},
], ids=["missing", "no-marker", "no-origin", "other-port", "null-origin", "wrong-marker"])
def test_login_and_logout_require_exact_origin_and_explicit_web_header(web, headers) -> None:
    for path in (LOGIN, LOGOUT):
        response = web.client.post(path, headers=headers, json={"username": "demo_a", "password": PASSWORD})
        assert response.status_code == 403 and response.json() == {"detail": "网页请求来源无效"}
        assert response.headers["Cache-Control"] == "no-store" and "Set-Cookie" not in response.headers


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_all_cookie_authenticated_unsafe_methods_apply_csrf_before_user_lookup(web, method) -> None:
    web.client.cookies.set(COOKIE, token(web))
    rejected = web.client.request(method, "/api/v1/probe")
    assert rejected.status_code == 403 and web.session.reads == 0
    accepted = web.client.request(method, "/api/v1/probe", headers=HEADERS)
    assert accepted.status_code == 200 and accepted.json() == {"user_id": str(USER_A)}
    assert accepted.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("authorization", ["Bearer broken", "Basic abc", "", "Bearer"])
def test_invalid_explicit_authorization_never_falls_back_to_valid_cookie(web, authorization) -> None:
    web.client.cookies.set(COOKIE, token(web))
    assert_unauthorized(web.client.get("/api/v1/probe", headers={"Authorization": authorization}))
    assert web.session.reads == 0


def test_bearer_login_and_writes_remain_compatible_and_override_other_cookie_identity(web) -> None:
    web.client.cookies.set(COOKIE, token(web))
    response = web.client.post("/api/v1/auth/login", json={"username": "demo_b", "password": PASSWORD})
    assert response.status_code == 200 and response.json()["token_type"] == "bearer"
    assert "Set-Cookie" not in response.headers
    bearer = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert web.client.post("/api/v1/probe", headers=bearer).json() == {"user_id": str(USER_B)}
    assert web.client.get("/api/v1/auth/me", headers=bearer).json()["id"] == str(USER_B)


def test_web_restore_rejects_bearer_even_with_valid_cookie(web) -> None:
    web.client.cookies.set(COOKIE, token(web))
    for value in ("Bearer " + token(web, USER_B), "Bearer broken", ""):
        assert_unauthorized(web.client.get(RESTORE, headers={"Authorization": value}))
    assert web.session.reads == 0


def test_logout_deletes_same_cookie_path_without_authentication_or_jwt_revocation(web) -> None:
    sign_in(web)
    saved = web.client.cookies.get(COOKIE)
    response = web.client.post(LOGOUT, headers=HEADERS)
    assert response.status_code == 204 and response.content == b""
    cookie = SimpleCookie(response.headers["Set-Cookie"])[COOKIE]
    assert cookie["path"] == "/" and cookie["domain"] == "" and cookie["max-age"] == "0"
    assert cookie["httponly"] and cookie["samesite"].lower() == "strict"
    assert response.headers["Cache-Control"] == "no-store"
    assert_unauthorized(web.client.get(RESTORE))
    assert web.client.get("/api/v1/probe", headers={"Authorization": "Bearer " + saved}).status_code == 200
    assert web.client.post(LOGOUT, headers=HEADERS).status_code == 204


def test_web_login_wrong_password_or_unknown_user_is_safe_and_sets_no_cookie(web) -> None:
    for username, password in (("demo_a", "wrong-value"), ("unknown", PASSWORD)):
        response = web.client.post(LOGIN, headers=HEADERS, json={"username": username, "password": password})
        assert response.status_code == 401 and response.json() == {"detail": "用户名或密码错误"}
        assert response.headers["Cache-Control"] == "no-store" and "Set-Cookie" not in response.headers
        assert password not in response.text


def test_web_validation_does_not_echo_request_password_or_extra_values(web) -> None:
    response = web.client.post(LOGIN, headers=HEADERS, json={"username": "demo_a", "password": "", "extra": PASSWORD})
    assert response.status_code == 422 and response.json() == {"detail": "网页认证参数无效"}
    assert response.headers["Cache-Control"] == "no-store" and PASSWORD not in response.text


@pytest.fixture
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # 只枚举并临时移除配置键，不读取或展示本机值；Settings 禁止读取 .env。
    for name in tuple(os.environ):
        if name.startswith("EAAP_"):
            monkeypatch.delenv(name)


def test_web_origin_requires_an_exact_origin_without_credentials_path_or_query(clean_settings_environment) -> None:
    for origin in ("http://user:pass@example.test", "http://example.test/", "http://example.test?q=1",
                   "ftp://example.test", "http://example.test:0", "null"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, web_origin=origin)


def test_production_requires_explicit_https_origin(clean_settings_environment) -> None:
    assert Settings(_env_file=None).web_origin == ORIGIN
    for options in ({}, {"web_origin": ORIGIN}):
        with pytest.raises(ValidationError, match="HTTPS"):
            Settings(_env_file=None, app_env="production", **options)
    assert Settings(_env_file=None, app_env="production", web_origin="https://web.example.test").web_origin == "https://web.example.test"


def test_invalid_production_origin_diagnostic_hides_other_configuration_inputs(clean_settings_environment) -> None:
    database_url = "postgresql://controlled:controlled-private-password@example.invalid/test"
    secret = "controlled-private-jwt-signing-key-for-tests"
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, app_env="production", web_origin=ORIGIN,
                 database_url=database_url, jwt_secret_key=secret)
    diagnostic = str(failure.value)
    assert "HTTPS" in diagnostic
    assert database_url not in diagnostic and "controlled-private-password" not in diagnostic
    assert secret not in diagnostic
