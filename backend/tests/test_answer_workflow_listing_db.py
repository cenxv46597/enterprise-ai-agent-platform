"""真实 PostgreSQL 历史列表集成验证；仅虚构数据，不执行工作流或调用模型。

显式测试连接必须指向本机 eaap_test；只创建并清理本次随机 schema。
认证身份使用测试替身，成员关系、资源范围、SQL 和持久化均走真实数据库。
不导入 app.main、不加载 Settings 或本机 .env，也不使用日常账号数据。
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import answer_workflows as workflow_api
from app.api.answer_workflows import router
from app.api.answers import get_generation_provider
from app.api.documents import get_embedding_provider
from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.base import Base
from app.db.models import (
    AnswerWorkflow, AnswerWorkflowAttempt, KnowledgeBase, User, Workspace, WorkspaceMembership,
)
from app.db.session import get_session
from app.workflows import service


NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
TABLES = [model.__table__ for model in (
    User, Workspace, WorkspaceMembership, KnowledgeBase, AnswerWorkflow, AnswerWorkflowAttempt,
)]
SUMMARY_FIELDS = {
    "workflow_id", "workspace_id", "knowledge_base_id", "question", "strategy",
    "max_context_characters", "status", "next_step", "version", "attempt_count",
    "error_code", "retryable", "created_at", "updated_at",
}


def _safe_test_url(value: str, application_value: str | None) -> URL:
    """先检查连接目标，不在失败信息中回显任何连接字符串。"""
    try:
        target = make_url(value)
        application = make_url(application_value) if application_value else None
        if (target.drivername not in {"postgresql", "postgresql+psycopg"}
                or target.database != "eaap_test"
                or target.host not in {"127.0.0.1", "localhost", "::1"}
                or target.query):
            raise ValueError
        if (application is not None and application.database == target.database
                and application.host in {"127.0.0.1", "localhost", "::1"}
                and (application.port or 5432) == (target.port or 5432)):
            raise ValueError
        # 使用项目声明的驱动，禁止 URL query 覆盖 host/dbname/options 等目标。
        return target.set(drivername="postgresql+psycopg")
    except (ArgumentError, TypeError, ValueError):
        pytest.fail("仅允许无 query 参数的本机独立 eaap_test，且不能与日常库同目标", pytrace=False)


@dataclass
class ListingContext:
    client: TestClient
    engine: Engine
    workspace_id: UUID
    knowledge_base_id: UUID
    other_base_id: UUID
    other_workspace_id: UUID
    other_workspace_base_id: UUID
    owner_id: UUID
    member_id: UUID
    outsider_id: UUID

    def url(self, workflow_id: UUID | None = None, *, workspace_id: UUID | None = None,
            knowledge_base_id: UUID | None = None) -> str:
        base = (f"/api/v1/workspaces/{workspace_id or self.workspace_id}/knowledge-bases/"
                f"{knowledge_base_id or self.knowledge_base_id}/answer-workflows")
        return base if workflow_id is None else f"{base}/{workflow_id}"

    def as_user(self, user_id: UUID) -> None:
        # JWT/Cookie 另有专项测试；此处只替换身份来源，不替换成员权限查询。
        self.client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

    def insert(self, *, identifier: UUID | None = None, knowledge_base_id: UUID | None = None,
               created_at: datetime = NOW, updated_at: datetime | None = None,
               **changes) -> UUID:
        identifier = identifier or uuid4()
        values = dict(
            id=identifier, knowledge_base_id=knowledge_base_id or self.knowledge_base_id,
            creation_request_id=uuid4(), creation_digest="a" * 64,
            question=f"虚构列表问题-{identifier}", strategy="vector", max_context_characters=12000,
            status="pending", next_step="retrieve", version=0, attempt_count=0,
            checkpoint=None, result=None, error_code=None,
            active_request_id=None, lease_expires_at=None,
            created_at=created_at, updated_at=updated_at or created_at,
        ) | changes
        with Session(self.engine) as session, session.begin():
            session.add(AnswerWorkflow(**values))
        return identifier

    def rows(self) -> list[dict]:
        with Session(self.engine) as session:
            return [dict(row) for row in session.execute(
                select(AnswerWorkflow.__table__).order_by(AnswerWorkflow.id)
            ).mappings()]

    def checkpoint(self) -> dict:
        return dict(schema_version=1, workspace_id=str(self.workspace_id),
                    knowledge_base_id=str(self.knowledge_base_id), strategy="vector", hits=[])


@pytest.fixture
def listing_db_context(monkeypatch: pytest.MonkeyPatch) -> Iterator[ListingContext]:
    database_value = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_value:
        pytest.skip("设置显式 EAAP_TEST_DATABASE_URL 后运行 PostgreSQL 历史列表集成验证")
    database_url = _safe_test_url(database_value, os.environ.get("EAAP_DATABASE_URL"))

    def forbidden(*args, **kwargs):
        pytest.fail("只读列表集成验证不得执行工作流、检索或构造模型提供方")

    # 创建仅使用固定测试配置；绝不让 Settings() 加载本机凭据或模型设置。
    monkeypatch.setattr(service, "Settings", lambda: SimpleNamespace(generation_max_context_characters=12000))
    monkeypatch.setattr(service, "search_knowledge_base", forbidden)
    monkeypatch.setattr(service, "answer_from_hits", forbidden)
    monkeypatch.setattr(service, "execute_workflow", forbidden)
    monkeypatch.setattr(workflow_api, "execute_workflow", forbidden)

    engine = create_engine(database_url, poolclass=NullPool, hide_parameters=True, connect_args={
        "connect_timeout": 5,
        "options": "-c statement_timeout=15000 -c lock_timeout=10000",
    })
    schema = f"eaap_test_{uuid4().hex}"
    created = False
    try:
        try:
            with engine.begin() as connection:
                # 在任何 DDL 前再确认服务器实际数据库，避免目标被外部连接配置改变。
                if connection.scalar(select(func.current_database())) != "eaap_test":
                    pytest.fail("服务器实际数据库不是独立 eaap_test", pytrace=False)
                connection.execute(CreateSchema(schema))
            created = True
        except SQLAlchemyError:
            pytest.fail("独立测试数据库不可连接或不能创建测试 schema；连接信息不回显", pytrace=False)

        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        # 仅当前范围所需六表；没有 chunks/pgvector，也不在日常库运行迁移。
        Base.metadata.create_all(scoped_engine, tables=TABLES)
        workspace_id, other_workspace_id = uuid4(), uuid4()
        knowledge_base_id, other_base_id, other_workspace_base_id = uuid4(), uuid4(), uuid4()
        owner_id, member_id, outsider_id = uuid4(), uuid4(), uuid4()
        with Session(scoped_engine) as session, session.begin():
            session.add_all([
                Workspace(id=workspace_id, name="列表测试空间"),
                Workspace(id=other_workspace_id, name="另一个列表测试空间"),
                User(id=owner_id, username="listing_owner", password_hash="test-only-placeholder"),
                User(id=member_id, username="listing_member", password_hash="test-only-placeholder"),
                User(id=outsider_id, username="listing_outsider", password_hash="test-only-placeholder"),
            ])
            session.flush()
            session.add_all([
                KnowledgeBase(id=knowledge_base_id, workspace_id=workspace_id, name="列表测试库"),
                KnowledgeBase(id=other_base_id, workspace_id=workspace_id, name="同空间另一库"),
                KnowledgeBase(id=other_workspace_base_id, workspace_id=other_workspace_id, name="跨空间库"),
                WorkspaceMembership(workspace_id=workspace_id, user_id=owner_id, role="owner"),
                WorkspaceMembership(workspace_id=workspace_id, user_id=member_id, role="member"),
                WorkspaceMembership(workspace_id=other_workspace_id, user_id=owner_id, role="owner"),
            ])

        def session_provider() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        application = FastAPI()
        application.include_router(router, dependencies=[Depends(require_workspace_member)])
        application.dependency_overrides[get_session] = session_provider
        application.dependency_overrides[get_embedding_provider] = forbidden
        application.dependency_overrides[get_generation_provider] = forbidden
        with TestClient(application) as client:
            context = ListingContext(client, scoped_engine, workspace_id, knowledge_base_id,
                                     other_base_id, other_workspace_id, other_workspace_base_id,
                                     owner_id, member_id, outsider_id)
            context.as_user(owner_id)
            yield context
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            engine.dispose()


def test_db_empty_knowledge_base_returns_empty_list(listing_db_context: ListingContext):
    context = listing_db_context
    for status in (None, "pending", "running", "failed", "completed"):
        params = {} if status is None else {"status": status}
        response = context.client.get(context.url(), params=params)
        assert response.status_code == 200 and response.json() == []


def test_db_creation_time_then_uuid_desc_is_stable(listing_db_context: ListingContext):
    context = listing_db_context
    old_id = context.insert(identifier=UUID(int=999), created_at=NOW - timedelta(days=1))
    low_id = context.insert(identifier=UUID(int=1))
    high_id = context.insert(identifier=UUID(int=2))
    expected = [str(high_id), str(low_id), str(old_id)]
    for _ in range(2):
        response = context.client.get(context.url())
        assert response.status_code == 200
        assert [item["workflow_id"] for item in response.json()] == expected


def test_db_recent_update_does_not_promote_old_creation(listing_db_context: ListingContext):
    context = listing_db_context
    old_id = context.insert(created_at=NOW - timedelta(days=30), updated_at=NOW + timedelta(days=1),
                            status="failed", version=2, attempt_count=1, error_code="embedding_timeout")
    new_id = context.insert()
    response = context.client.get(context.url(), params={"limit": 1})
    assert response.status_code == 200 and response.json()[0]["workflow_id"] == str(new_id)
    assert str(old_id) not in response.text


def test_db_scope_filter_precedes_limit(listing_db_context: ListingContext):
    context = listing_db_context
    allowed_id = context.insert(created_at=NOW - timedelta(days=1))
    context.insert(knowledge_base_id=context.other_base_id)
    context.insert(knowledge_base_id=context.other_workspace_base_id)
    response = context.client.get(context.url(), params={"limit": 1})
    assert response.status_code == 200
    assert [item["workflow_id"] for item in response.json()] == [str(allowed_id)]
    failed = dict(status="failed", version=2, attempt_count=1, error_code="embedding_timeout")
    target_id = context.insert(created_at=NOW - timedelta(days=2), **failed)
    context.insert(knowledge_base_id=context.other_base_id, **failed)
    context.insert(knowledge_base_id=context.other_workspace_base_id, **failed)
    # More than one unfiltered page is newer than the target: filtering a fetched
    # twenty-row page in Python would incorrectly hide this matching record.
    for index in range(21):
        context.insert(created_at=NOW + timedelta(seconds=index + 1))
    before = context.rows()
    unfiltered = context.client.get(context.url())
    filtered = context.client.get(context.url(), params={"status": "failed"})
    assert unfiltered.status_code == filtered.status_code == 200
    assert len(unfiltered.json()) == 20 and str(target_id) not in unfiltered.text
    assert [item["workflow_id"] for item in filtered.json()] == [str(target_id)]
    assert all(item["status"] == "failed" for item in filtered.json())
    for status in ("unknown", "all", "", "Failed", "FAILED", "failed ", "failed,completed"):
        rejected = context.client.get(context.url(), params={"status": status})
        assert rejected.status_code == 422
    assert context.rows() == before


def test_db_pagination_uses_stable_offset_and_empty_tail(listing_db_context: ListingContext):
    context = listing_db_context
    identifiers = [context.insert(identifier=UUID(int=value)) for value in range(1, 8)]
    first = context.client.get(context.url(), params={"limit": 3, "offset": 0})
    second = context.client.get(context.url(), params={"limit": 3, "offset": 3})
    tail = context.client.get(context.url(), params={"limit": 3, "offset": 6})
    empty = context.client.get(context.url(), params={"limit": 3, "offset": 7})
    assert all(response.status_code == 200 for response in (first, second, tail, empty))
    combined = first.json() + second.json() + tail.json()
    assert [item["workflow_id"] for item in combined] == [str(value) for value in reversed(identifiers)]
    assert empty.json() == []
    by_status = {"pending": identifiers}
    for group, status in enumerate(("running", "failed", "completed"), start=1):
        state = dict(status=status, version=3, attempt_count=1)
        if status == "running":
            state.update(active_request_id=uuid4(), lease_expires_at=NOW + timedelta(hours=1))
        elif status == "failed":
            state.update(error_code="generation_timeout")
        else:
            state.update(next_step="done", checkpoint=context.checkpoint(),
                         result=dict(answer="虚构筛选用空弃答。", abstained=True, citations=[], reranking=None))
        by_status[status] = [context.insert(identifier=UUID(int=group * 100 + value), **state)
                             for value in range(1, 8)]
    before = context.rows()
    unfiltered = context.client.get(context.url(), params={"limit": 100})
    expected_all = sorted((item for group in by_status.values() for item in group), reverse=True)
    assert unfiltered.status_code == 200
    assert [item["workflow_id"] for item in unfiltered.json()] == [str(value) for value in expected_all]
    assert {item["status"] for item in unfiltered.json()} == set(by_status)
    for status, matching in by_status.items():
        # All four groups share creation timestamps; UUID remains the tie breaker.
        pages = [context.client.get(context.url(), params={"status": status, "limit": 3, "offset": offset})
                 for offset in (0, 3, 6, 7)]
        assert all(page.status_code == 200 for page in pages)
        assert [len(page.json()) for page in pages] == [3, 3, 1, 0]
        items = pages[0].json() + pages[1].json() + pages[2].json()
        assert [item["workflow_id"] for item in items] == [str(value) for value in reversed(matching)]
        assert all(item["status"] == status for item in items)
    assert context.rows() == before


def test_db_default_page_is_latest_twenty_creation_records(listing_db_context: ListingContext):
    context = listing_db_context
    identifiers = [context.insert(identifier=UUID(int=value)) for value in range(1, 23)]
    response = context.client.get(context.url())
    assert response.status_code == 200
    assert [item["workflow_id"] for item in response.json()] == [str(value) for value in reversed(identifiers[2:])]


def test_db_owner_and_member_share_same_knowledge_base_list(listing_db_context: ListingContext):
    context = listing_db_context
    identifier = context.insert()
    for params in ({}, {"status": "pending"}):
        context.as_user(context.owner_id)
        owner_response = context.client.get(context.url(), params=params)
        context.as_user(context.member_id)
        member_response = context.client.get(context.url(), params=params)
        assert owner_response.status_code == member_response.status_code == 200
        assert owner_response.json() == member_response.json()
        assert member_response.json()[0]["workflow_id"] == str(identifier)


def test_db_nonmember_cannot_list_or_read_detail(listing_db_context: ListingContext):
    context = listing_db_context
    identifier = context.insert()
    context.as_user(context.outsider_id)
    for url, params in ((context.url(), {}), (context.url(), {"status": "failed"}), (context.url(identifier), {})):
        response = context.client.get(url, params=params)
        assert response.status_code == 404 and response.json() == {"detail": "工作区不存在"}


def test_db_wrong_workspace_parent_cannot_expose_another_knowledge_base(listing_db_context: ListingContext):
    context = listing_db_context
    # owner 同时是两个空间成员，确保拒绝来自 KB 父级核对而不是成员失败。
    context.insert(knowledge_base_id=context.other_workspace_base_id)
    for params in ({}, {"status": "failed"}):
        response = context.client.get(context.url(knowledge_base_id=context.other_workspace_base_id), params=params)
        assert response.status_code == 404
        assert response.json() == {"detail": "知识库或工作流不存在"}


def test_db_missing_knowledge_base_is_not_an_empty_existing_list(listing_db_context: ListingContext):
    context = listing_db_context
    missing_url = context.url(knowledge_base_id=uuid4())
    for params in ({}, {"status": "failed"}):
        response = context.client.get(missing_url, params=params)
        assert response.status_code == 404


def test_db_workflow_detail_cannot_cross_knowledge_base(listing_db_context: ListingContext):
    context = listing_db_context
    identifier = context.insert(knowledge_base_id=context.other_base_id)
    response = context.client.get(context.url(identifier))
    assert response.status_code == 404


def test_db_completed_summary_omits_saved_bodies_and_reads_change_nothing(listing_db_context: ListingContext):
    context = listing_db_context
    # 手工插入合法的虚构空弃答，不能用作实际检索/模型生成成功证据。
    saved_result = dict(answer="虚构空弃答：没有可用资料。", abstained=True, citations=[], reranking=None)
    identifier = context.insert(status="completed", next_step="done", version=3, attempt_count=1,
                                checkpoint=context.checkpoint(), result=saved_result)
    before = context.rows()
    listing = context.client.get(context.url(), params={"status": "completed"})
    detail = context.client.get(context.url(identifier))
    repeated = context.client.get(context.url(), params={"status": "completed"})
    assert listing.status_code == detail.status_code == repeated.status_code == 200
    assert set(listing.json()[0]) == SUMMARY_FIELDS
    for hidden in ("checkpoint", "result", "creation_request_id", "creation_digest",
                   "active_request_id", "lease_expires_at"):
        assert hidden not in listing.json()[0]
    assert "虚构空弃答" not in listing.text
    assert detail.json()["checkpoint"] == context.checkpoint()
    assert detail.json()["result"] == saved_result
    assert context.rows() == before
    with Session(context.engine) as session:
        assert session.scalars(select(AnswerWorkflowAttempt)).all() == []


def test_db_create_pending_persists_across_requests_without_execution(listing_db_context: ListingContext):
    context = listing_db_context
    request_id = uuid4()
    created = context.client.post(context.url(), json={
        "request_id": str(request_id), "question": "  虚构任务只保存不执行  ", "strategy": "vector",
    })
    assert created.status_code == 201
    identifier = UUID(created.json()["workflow_id"])
    context.as_user(context.member_id)
    listing = context.client.get(context.url())
    detail = context.client.get(context.url(identifier))
    assert listing.status_code == detail.status_code == 200
    assert listing.json()[0]["workflow_id"] == str(identifier)
    assert detail.json() == created.json()
    assert detail.json()["question"] == "虚构任务只保存不执行"
    assert detail.json()["status"] == "pending" and detail.json()["next_step"] == "retrieve"
    assert detail.json()["version"] == detail.json()["attempt_count"] == 0
    assert detail.json()["checkpoint"] is None and detail.json()["result"] is None
    with Session(context.engine) as session:
        row = session.get(AnswerWorkflow, identifier)
        assert row is not None and row.creation_request_id == request_id
        assert session.scalars(select(AnswerWorkflowAttempt)).all() == []


def test_db_later_detail_observes_new_state_after_old_list_snapshot(listing_db_context: ListingContext):
    context = listing_db_context
    identifier = context.insert(status="failed", next_step="generate", version=3, attempt_count=1,
                                checkpoint=context.checkpoint(), error_code="generation_timeout")
    initial = context.client.get(context.url(), params={"status": "failed"})
    assert initial.status_code == 200 and initial.json()[0]["status"] == "failed"
    with Session(context.engine) as session, session.begin():
        row = session.get(AnswerWorkflow, identifier)
        assert row is not None
        row.status, row.next_step, row.version, row.attempt_count = "completed", "done", 5, 2
        row.error_code = None
        row.result = dict(answer="虚构后续空弃答。", abstained=True, citations=[], reranking=None)
        row.updated_at = NOW + timedelta(minutes=1)
    detail = context.client.get(context.url(identifier))
    assert detail.status_code == 200 and detail.json()["status"] == "completed"
    assert detail.json()["version"] == 5 and detail.json()["attempt_count"] == 2
    failed = context.client.get(context.url(), params={"status": "failed"})
    completed = context.client.get(context.url(), params={"status": "completed"})
    assert failed.status_code == completed.status_code == 200 and failed.json() == []
    assert [item["workflow_id"] for item in completed.json()] == [str(identifier)]
    assert initial.json()[0]["status"] == "failed"  # 已返回的旧响应不会自动更新。
