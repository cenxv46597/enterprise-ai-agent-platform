"""在独立 PostgreSQL 模式中验证工作空间和知识库接口。"""

import os
from collections.abc import Iterator
from datetime import datetime, timezone
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


def test_list_knowledge_bases_filters_before_pagination(client: TestClient) -> None:
    first = client.post("/api/v1/workspaces", json={"name": "First"}).json()["id"]
    second = client.post("/api/v1/workspaces", json={"name": "Second"}).json()["id"]
    expected = []
    for index in range(3):
        for workspace_id in (first, second):
            response = client.post(
                f"/api/v1/workspaces/{workspace_id}/knowledge-bases",
                json={"name": f"Docs {index}"},
            )
            assert response.status_code == 201
            if workspace_id == first:
                expected.append(response.json())

    path = f"/api/v1/workspaces/{first}/knowledge-bases"
    response = client.get(path, params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    assert response.json() == expected[1:2]
    beyond_page = client.get(path, params={"offset": 3})
    assert beyond_page.status_code == 200
    assert beyond_page.json() == []


def test_list_knowledge_bases_empty_workspace_and_missing_parent(client: TestClient) -> None:
    workspace_id = client.post("/api/v1/workspaces", json={"name": "Empty"}).json()["id"]
    response = client.get(f"/api/v1/workspaces/{workspace_id}/knowledge-bases")
    assert response.status_code == 200
    assert response.json() == []

    response = client.get(
        f"/api/v1/workspaces/{uuid4()}/knowledge-bases", params={"offset": 100}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "工作区不存在"}


def test_list_knowledge_bases_defaults_and_tied_timestamp_order(client: TestClient) -> None:
    workspace_id = client.post("/api/v1/workspaces", json={"name": "Paged"}).json()["id"]
    base_ids = [UUID(int=index) for index in range(1, 22)]
    created_at = datetime(2026, 10, 13, tzinfo=timezone.utc)
    # 倒序写入同时间记录，验证排序不依赖插入顺序；只使用 fixture 的独立测试模式。
    for session in client.app.dependency_overrides[get_session]():
        session.add_all(
            models.KnowledgeBase(
                id=base_id,
                workspace_id=UUID(workspace_id),
                name=f"Docs {base_id.int}",
                created_at=created_at,
            )
            for base_id in reversed(base_ids)
        )
        session.commit()

    path = f"/api/v1/workspaces/{workspace_id}/knowledge-bases"
    first_page = client.get(path)
    assert first_page.status_code == 200
    assert len(first_page.json()) == 20
    assert [item["id"] for item in first_page.json()] == [
        str(value) for value in base_ids[:20]
    ]
    assert all(
        set(item) == {"id", "workspace_id", "name", "created_at"}
        for item in first_page.json()
    )
    second_page = client.get(path, params={"offset": 20})
    assert second_page.status_code == 200
    assert [item["id"] for item in second_page.json()] == [str(base_ids[20])]
    maximum_page = client.get(path, params={"limit": 100})
    assert maximum_page.status_code == 200
    assert len(maximum_page.json()) == 21


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"limit": "-1"},
        {"limit": "1.5"},
        {"limit": "many"},
        {"offset": "-1"},
        {"offset": "1.5"},
        {"offset": "many"},
        {"offset": str(2**63)},
    ],
)
def test_list_knowledge_bases_rejects_invalid_pagination_without_database(
    params: dict[str, str],
) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as test_client:
        response = test_client.get(
            f"/api/v1/workspaces/{uuid4()}/knowledge-bases", params=params
        )
    assert response.status_code == 422


def test_list_knowledge_bases_rejects_invalid_workspace_id_without_database() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/workspaces/not-a-uuid/knowledge-bases")
    assert response.status_code == 422


def test_list_workspaces_returns_empty_collection(client: TestClient) -> None:
    response = client.get("/api/v1/workspaces")
    assert response.status_code == 200
    assert response.json() == []


def test_list_workspaces_defaults_and_tied_timestamp_order(client: TestClient) -> None:
    workspace_ids = [UUID(int=index) for index in range(1, 22)]
    created_at = datetime(2026, 10, 13, tzinfo=timezone.utc)
    # 根列表属于本地单用户发现接口；同时间倒序写入验证 id 的稳定排序。
    for session in client.app.dependency_overrides[get_session]():
        session.add_all(
            models.Workspace(
                id=workspace_id,
                name=f"Workspace {workspace_id.int}",
                created_at=created_at,
            )
            for workspace_id in reversed(workspace_ids)
        )
        session.commit()

    path = "/api/v1/workspaces"
    first_page = client.get(path)
    assert first_page.status_code == 200
    assert len(first_page.json()) == 20
    assert [item["id"] for item in first_page.json()] == [
        str(value) for value in workspace_ids[:20]
    ]
    assert all(set(item) == {"id", "name", "created_at"} for item in first_page.json())
    assert first_page.json()[0]["name"] == "Workspace 1"
    assert datetime.fromisoformat(first_page.json()[0]["created_at"]).tzinfo is not None

    second_page = client.get(path, params={"offset": 20})
    assert second_page.status_code == 200
    assert [item["id"] for item in second_page.json()] == [str(workspace_ids[20])]
    single_item_page = client.get(path, params={"limit": 1, "offset": 1})
    assert single_item_page.status_code == 200
    assert [item["id"] for item in single_item_page.json()] == [str(workspace_ids[1])]
    maximum_page = client.get(path, params={"limit": 100})
    assert maximum_page.status_code == 200
    assert len(maximum_page.json()) == 21
    for offset in (21, 2**63 - 1):
        beyond_page = client.get(path, params={"offset": offset})
        assert beyond_page.status_code == 200
        assert beyond_page.json() == []


def test_list_workspaces_orders_by_creation_time_before_id(client: TestClient) -> None:
    workspace_ids = [UUID(int=index) for index in (1, 2, 3)]
    newer = datetime(2026, 10, 13, tzinfo=timezone.utc)
    older = datetime(2026, 10, 12, tzinfo=timezone.utc)
    for session in client.app.dependency_overrides[get_session]():
        session.add_all(
            models.Workspace(
                id=workspace_id,
                name=f"Workspace {workspace_id.int}",
                created_at=older if workspace_id.int == 3 else newer,
            )
            for workspace_id in workspace_ids
        )
        session.commit()

    response = client.get("/api/v1/workspaces")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [
        str(workspace_ids[index]) for index in (2, 0, 1)
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"limit": "-1"},
        {"limit": "1.5"},
        {"limit": "many"},
        {"offset": "-1"},
        {"offset": "1.5"},
        {"offset": "many"},
        {"offset": str(2**63)},
    ],
)
def test_list_workspaces_rejects_invalid_pagination_without_database(
    params: dict[str, str],
) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/workspaces", params=params)
    assert response.status_code == 422
