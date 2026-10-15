"""真实 HTTP 请求验证用户身份与工作空间成员边界。"""

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app

# 将所有模型注册到 Base.metadata，测试模式必须与生产模型同构。
from app.db import models  # noqa: F401


TEST_PASSWORD = "local-test-password-1234"


def test_local_blank_jwt_secret_uses_ephemeral_key_and_production_rejects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EAAP_JWT_SECRET_KEY", "")
    monkeypatch.setenv("EAAP_APP_ENV", "local")
    first = create_app()
    second = create_app()
    assert len(first.state.jwt_secret_key) >= 32
    assert first.state.jwt_secret_key != second.state.jwt_secret_key

    monkeypatch.setenv("EAAP_WEB_ORIGIN", "https://web.example.test")
    monkeypatch.setenv("EAAP_APP_ENV", "production")
    with pytest.raises(RuntimeError, match="生产环境必须配置"):
        create_app()


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置独立 EAAP_TEST_DATABASE_URL 后运行身份集成测试")
    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("身份测试不能使用日常应用数据库")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("身份集成测试需要独立 PostgreSQL 数据库")

    monkeypatch.setenv("EAAP_JWT_SECRET_KEY", "test-only-jwt-secret-" + uuid4().hex)
    engine = create_engine(database_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    schema = f"eaap_auth_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped_engine)

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        app = create_app()
        app.state.test_engine = scoped_engine
        app.dependency_overrides[get_session] = test_session
        with TestClient(app) as client:
            yield client
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def _register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 201
    account = response.json()
    assert account["username"] == username
    assert UUID(account["id"])
    return account


def _login(client: TestClient, username: str, password: str = TEST_PASSWORD) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    return body["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_workspace(client: TestClient, token: str, name: str) -> str:
    response = client.post(
        "/api/v1/workspaces", json={"name": name}, headers=_headers(token)
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_register_login_me_and_wrong_password(auth_client: TestClient) -> None:
    account = _register(auth_client, "alice")
    assert auth_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    ).status_code == 401
    assert auth_client.post(
        "/api/v1/auth/login",
        json={"username": "unknown", "password": TEST_PASSWORD},
    ).status_code == 401

    token = _login(auth_client, "alice")
    me = auth_client.get("/api/v1/auth/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["id"] == account["id"]
    assert me.json()["username"] == "alice"
    assert "password" not in me.json()


def test_anonymous_workspace_entries_and_all_nested_routers_require_identity(
    auth_client: TestClient,
) -> None:
    assert auth_client.get("/api/v1/workspaces").status_code == 401
    assert auth_client.post(
        "/api/v1/workspaces", json={"name": "未经登录的工作区"}
    ).status_code == 401
    assert auth_client.get(f"/api/v1/workspaces/{uuid4()}").status_code == 401
    assert auth_client.post(
        f"/api/v1/workspaces/{uuid4()}/members", json={"username": "alice"}
    ).status_code == 401

    nested_paths = {
        path: operations
        for path, operations in auth_client.app.openapi()["paths"].items()
        if path.startswith("/api/v1/workspaces/{workspace_id}/knowledge-bases")
    }
    assert nested_paths
    placeholder = str(uuid4())
    for path, operations in nested_paths.items():
        request_path = path.format_map({
            "workspace_id": placeholder,
            "knowledge_base_id": placeholder,
            "document_id": placeholder,
            "conversation_id": placeholder,
            "workflow_id": placeholder,
        })
        for method in operations:
            assert auth_client.request(method.upper(), request_path).status_code == 401


def test_missing_forged_tampered_and_expired_credentials_are_rejected(
    auth_client: TestClient,
) -> None:
    account = _register(auth_client, "alice")
    token = _login(auth_client, "alice")
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    assert auth_client.get(
        "/api/v1/auth/me", headers={"X-User-Id": account["id"]}
    ).status_code == 401
    assert auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}
    ).status_code == 401

    parts = token.split(".")
    assert len(parts) == 3 and parts[2]
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    assert auth_client.get(
        "/api/v1/auth/me", headers=_headers(".".join(parts))
    ).status_code == 401

    from app.auth.security import issue_access_token

    expired_token = issue_access_token(
        UUID(account["id"]), auth_client.app.state.jwt_secret_key,
        now=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    assert auth_client.get(
        "/api/v1/auth/me", headers=_headers(expired_token)
    ).status_code == 401


def test_foreign_workspace_url_is_not_an_access_grant(auth_client: TestClient) -> None:
    _register(auth_client, "alice")
    _register(auth_client, "bob")
    alice = _login(auth_client, "alice")
    bob = _login(auth_client, "bob")
    workspace_a = _create_workspace(auth_client, alice, "A 公司")
    workspace_b = _create_workspace(auth_client, bob, "B 公司")
    base_url = f"/api/v1/workspaces/{workspace_b}/knowledge-bases"
    created = auth_client.post(
        base_url, json={"name": "B 内部资料"}, headers=_headers(bob)
    )
    assert created.status_code == 201
    base_b = created.json()["id"]

    assert auth_client.get(base_url).status_code == 401
    assert auth_client.get(base_url, headers=_headers(alice)).status_code == 404
    assert auth_client.get(
        f"{base_url}/{base_b}", headers=_headers(alice)
    ).status_code == 404
    assert auth_client.post(
        base_url, json={"name": "越权新增"}, headers=_headers(alice)
    ).status_code == 404
    assert auth_client.get(
        f"/api/v1/workspaces/{workspace_b}/knowledge-bases/{base_b}/documents",
        headers=_headers(alice),
    ).status_code == 404
    assert auth_client.get(
        f"/api/v1/workspaces/{workspace_b}/knowledge-bases/{base_b}/answer-preferences",
        headers=_headers(alice),
    ).status_code == 404

    protected_calls = [
        ("POST", f"{base_url}/{base_b}/search", {"query": "B 公司合同"}),
        ("POST", f"{base_url}/{base_b}/answers", {"question": "B 公司合同"}),
        ("POST", f"{base_url}/{base_b}/agent-runs", {
            "question": "B 公司合同", "search_backend": "mcp",
        }),
        ("POST", f"{base_url}/{base_b}/conversations", None),
        ("POST", f"{base_url}/{base_b}/answer-workflows", {
            "request_id": str(uuid4()), "question": "B 公司合同",
        }),
    ]
    for method, path, payload in protected_calls:
        response = auth_client.request(method, path, json=payload, headers=_headers(alice))
        assert response.status_code == 404, (method, path, response.status_code)

    from app.db.models import AnswerWorkflow, Conversation

    with Session(auth_client.app.state.test_engine) as session:
        assert session.query(Conversation).count() == 0
        assert session.query(AnswerWorkflow).count() == 0

    own_a = auth_client.get(
        f"/api/v1/workspaces/{workspace_a}", headers=_headers(alice)
    )
    assert own_a.status_code == 200
    own_b = auth_client.get(base_url, headers=_headers(bob))
    assert own_b.status_code == 200
    assert [base["id"] for base in own_b.json()] == [base_b]


def test_owner_can_invite_existing_user_but_member_cannot(
    auth_client: TestClient,
) -> None:
    _register(auth_client, "owner")
    _register(auth_client, "member")
    _register(auth_client, "third")
    owner = _login(auth_client, "owner")
    member = _login(auth_client, "member")
    third = _login(auth_client, "third")
    workspace_id = _create_workspace(auth_client, owner, "合同资料")
    workspace_url = f"/api/v1/workspaces/{workspace_id}"
    members_url = f"{workspace_url}/members"

    assert auth_client.get(workspace_url, headers=_headers(member)).status_code == 404
    grant = auth_client.post(
        members_url, json={"username": "member"}, headers=_headers(owner)
    )
    assert grant.status_code == 201
    assert grant.json()["workspace_id"] == workspace_id
    assert grant.json()["username"] == "member"
    assert grant.json()["role"] == "member"
    assert auth_client.get(workspace_url, headers=_headers(member)).status_code == 200
    _create_workspace(auth_client, member, "成员自己的空间")
    assert auth_client.post(
        members_url, json={"username": "third"}, headers=_headers(member)
    ).status_code == 404
    assert auth_client.get(workspace_url, headers=_headers(third)).status_code == 404
    assert auth_client.post(
        members_url, json={"username": "member"}, headers=_headers(owner)
    ).status_code == 409
    assert auth_client.post(
        members_url, json={"username": "unknown"}, headers=_headers(owner)
    ).status_code == 404


def test_workspace_list_filters_membership_before_pagination(
    auth_client: TestClient,
) -> None:
    _register(auth_client, "alice")
    _register(auth_client, "bob")
    alice = _login(auth_client, "alice")
    bob = _login(auth_client, "bob")
    owned_a: list[str] = []
    owned_b: list[str] = []
    for index in range(6):
        owned_a.append(_create_workspace(auth_client, alice, f"A {index}"))
        owned_b.append(_create_workspace(auth_client, bob, f"B {index}"))

    all_a = auth_client.get(
        "/api/v1/workspaces", params={"limit": 100}, headers=_headers(alice)
    )
    all_b = auth_client.get(
        "/api/v1/workspaces", params={"limit": 100}, headers=_headers(bob)
    )
    assert all_a.status_code == all_b.status_code == 200
    ids_a = [workspace["id"] for workspace in all_a.json()]
    ids_b = [workspace["id"] for workspace in all_b.json()]
    assert len(ids_a) == len(ids_b) == 6
    assert set(ids_a) == set(owned_a)
    assert set(ids_b) == set(owned_b)

    page_a = auth_client.get(
        "/api/v1/workspaces", params={"limit": 2, "offset": 3},
        headers=_headers(alice),
    )
    assert page_a.status_code == 200
    assert [workspace["id"] for workspace in page_a.json()] == ids_a[3:5]
    assert auth_client.get(
        f"/api/v1/workspaces/{owned_b[0]}", headers=_headers(alice)
    ).status_code == 404


def test_legacy_workspace_requires_explicit_owner_assignment(
    auth_client: TestClient,
) -> None:
    from app.auth.assign_owner import OwnerAssignmentConflictError, assign_owner
    from app.db.models import Workspace, WorkspaceMembership

    alice_account = _register(auth_client, "alice")
    _register(auth_client, "bob")
    alice = _login(auth_client, "alice")
    bob = _login(auth_client, "bob")
    legacy_id = uuid4()
    with Session(auth_client.app.state.test_engine) as session, session.begin():
        session.add(Workspace(id=legacy_id, name="迁移前旧工作空间"))

    url = f"/api/v1/workspaces/{legacy_id}"
    assert auth_client.get(url, headers=_headers(alice)).status_code == 404
    assert auth_client.get(url, headers=_headers(bob)).status_code == 404

    with Session(auth_client.app.state.test_engine) as session:
        assigned = assign_owner(session, legacy_id, "alice")
        assert assigned.role == "owner"
        assert assigned.user_id == UUID(alice_account["id"])
    assert auth_client.get(url, headers=_headers(alice)).status_code == 200
    assert auth_client.get(url, headers=_headers(bob)).status_code == 404

    with Session(auth_client.app.state.test_engine) as session:
        with pytest.raises(OwnerAssignmentConflictError):
            assign_owner(session, legacy_id, "bob")
    with Session(auth_client.app.state.test_engine) as session:
        memberships = session.query(WorkspaceMembership).filter_by(workspace_id=legacy_id).all()
        assert len(memberships) == 1
        assert memberships[0].user_id == UUID(alice_account["id"])


def test_web_cookie_restores_fixed_expiry_and_preserves_real_membership_boundaries(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.auth import security

    origin = "http://127.0.0.1:5173"
    auth_client.app.state.web_origin = origin
    web_headers = {"Origin": origin, "X-EAAP-Web": "1"}
    alice = _register(auth_client, "alice")
    _register(auth_client, "bob")
    signed_in = auth_client.post(
        "/api/v1/auth/web/login",
        json={"username": "alice", "password": TEST_PASSWORD}, headers=web_headers,
    )
    assert signed_in.status_code == 200
    receipt = signed_in.json()
    assert receipt["user"]["id"] == alice["id"]
    assert "access_token" not in receipt
    expires_at = receipt["expires_at"]

    own = auth_client.post("/api/v1/workspaces", json={"name": "网页空间"}, headers=web_headers)
    assert own.status_code == 201
    assert auth_client.post("/api/v1/workspaces", json={"name": "无来源写入"}).status_code == 403
    # Cookie 存在也不能掩盖显式错误认证头；CLI 则仍可单独使用 Bob 的 Bearer。
    assert auth_client.get("/api/v1/auth/me", headers={"Authorization": "Basic bad"}).status_code == 401
    bob_token = _login(auth_client, "bob")
    bob_space = _create_workspace(auth_client, bob_token, "Bob 的空间")
    assert auth_client.get(f"/api/v1/workspaces/{bob_space}").status_code == 404
    assert auth_client.get(f"/api/v1/workspaces/{own.json()['id']}", headers=_headers(bob_token)).status_code == 404
    listed = auth_client.get("/api/v1/workspaces").json()
    assert [row["id"] for row in listed] == [own.json()["id"]]

    monkeypatch.setattr(security, "_timestamp", lambda now=None: expires_at - 600)
    restored = auth_client.get("/api/v1/auth/web/session")
    assert restored.status_code == 200
    assert restored.json()["expires_at"] == expires_at
    assert restored.json()["user"]["id"] == alice["id"]
    assert "set-cookie" not in restored.headers
    monkeypatch.setattr(security, "_timestamp", lambda now=None: expires_at)
    assert auth_client.get("/api/v1/auth/web/session").status_code == 401
    assert auth_client.get("/api/v1/workspaces").status_code == 401
    assert auth_client.post("/api/v1/auth/web/logout", headers=web_headers).status_code == 204
    assert auth_client.get("/api/v1/auth/web/session").status_code == 401
