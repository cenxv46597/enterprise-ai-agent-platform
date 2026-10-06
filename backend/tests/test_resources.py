"""在独立 PostgreSQL 模式中验证工作空间和知识库接口。"""

import os
from collections.abc import Iterator
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import get_session
from app.main import create_app

# 导入模型后，所有表才会注册到 Base.metadata。
from app.db import models  # noqa: F401


@pytest.fixture
def client() -> Iterator[TestClient]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set EAAP_TEST_DATABASE_URL to run PostgreSQL resource tests")

    engine = create_engine(database_url, poolclass=NullPool)
    schema = f"eaap_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    try:
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped_engine)

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_session] = test_session
        with TestClient(app) as test_client:
            yield test_client
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_create_and_get_resources(client: TestClient) -> None:
    workspace_response = client.post("/api/v1/workspaces", json={"name": "  Product  "})
    assert workspace_response.status_code == 201
    workspace = workspace_response.json()
    workspace_id = UUID(workspace["id"])
    assert workspace["name"] == "Product"
    assert datetime.fromisoformat(workspace["created_at"]).tzinfo is not None

    get_workspace = client.get(f"/api/v1/workspaces/{workspace_id}")
    assert get_workspace.status_code == 200
    assert get_workspace.json() == workspace

    knowledge_base_response = client.post(
        f"/api/v1/workspaces/{workspace_id}/knowledge-bases",
        json={"name": "  Handbook  "},
    )
    assert knowledge_base_response.status_code == 201
    knowledge_base = knowledge_base_response.json()
    knowledge_base_id = UUID(knowledge_base["id"])
    assert knowledge_base["workspace_id"] == str(workspace_id)
    assert knowledge_base["name"] == "Handbook"
    assert datetime.fromisoformat(knowledge_base["created_at"]).tzinfo is not None

    get_knowledge_base = client.get(
        f"/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}"
    )
    assert get_knowledge_base.status_code == 200
    assert get_knowledge_base.json() == knowledge_base


def test_knowledge_base_name_conflict_is_scoped_to_workspace(client: TestClient) -> None:
    first_workspace = client.post("/api/v1/workspaces", json={"name": "First"}).json()
    second_workspace = client.post("/api/v1/workspaces", json={"name": "Second"}).json()
    first_id = first_workspace["id"]
    second_id = second_workspace["id"]

    first_base = client.post(
        f"/api/v1/workspaces/{first_id}/knowledge-bases", json={"name": "Docs"}
    )
    assert first_base.status_code == 201

    duplicate = client.post(
        f"/api/v1/workspaces/{first_id}/knowledge-bases", json={"name": " Docs "}
    )
    assert duplicate.status_code == 409

    second_base = client.post(
        f"/api/v1/workspaces/{second_id}/knowledge-bases", json={"name": "Docs"}
    )
    assert second_base.status_code == 201

    wrong_parent = client.get(
        f"/api/v1/workspaces/{second_id}/knowledge-bases/{first_base.json()['id']}"
    )
    assert wrong_parent.status_code == 404


def test_missing_parent_and_invalid_names(client: TestClient) -> None:
    missing_workspace_id = uuid4()
    assert client.get(f"/api/v1/workspaces/{missing_workspace_id}").status_code == 404
    assert (
        client.post(
            f"/api/v1/workspaces/{missing_workspace_id}/knowledge-bases",
            json={"name": "Docs"},
        ).status_code
        == 404
    )

    workspace = client.post("/api/v1/workspaces", json={"name": "Valid"}).json()
    for bad_name in ("   ", "x" * 201):
        assert client.post("/api/v1/workspaces", json={"name": bad_name}).status_code == 422
        assert (
            client.post(
                f"/api/v1/workspaces/{workspace['id']}/knowledge-bases",
                json={"name": bad_name},
            ).status_code
            == 422
        )
