"""为既有业务测试创建真实成员关系和带签名的测试请求。"""

from collections.abc import Iterable
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.auth.security import issue_access_token
from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.models import User, WorkspaceMembership


def authenticate_as(client: TestClient, user_id: UUID) -> None:
    """使用当前测试应用自己的签名密钥，供跨应用恢复用例重新签发令牌。"""
    token = issue_access_token(user_id, client.app.state.jwt_secret_key)
    client.headers["Authorization"] = f"Bearer {token}"
    client.app.state.test_user_id = user_id


def create_authenticated_user(
    client: TestClient, engine: Engine, *, workspace_ids: Iterable[UUID] = ()
) -> UUID:
    user_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            User(
                id=user_id,
                username=f"test_{user_id.hex[:20]}",
                password_hash="test-only-placeholder",
            )
        )
        for workspace_id in workspace_ids:
            session.add(
                WorkspaceMembership(
                    workspace_id=workspace_id, user_id=user_id, role="owner"
                )
            )
    authenticate_as(client, user_id)
    return user_id


def copy_auth(source: TestClient, target: TestClient) -> None:
    authenticate_as(target, source.app.state.test_user_id)


def isolate_request_validation(app: FastAPI) -> None:
    """仅供无需数据库的参数/提供方测试，让待测分支先于身份存储运行。"""
    app.dependency_overrides[get_current_user] = lambda: User(
        id=uuid4(), username="test_validator", password_hash="test-only-placeholder"
    )
    app.dependency_overrides[require_workspace_member] = lambda: None
