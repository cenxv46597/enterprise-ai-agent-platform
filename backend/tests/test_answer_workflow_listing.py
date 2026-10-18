"""历史列表的轻量契约、SQL 与 HTTP 替身验证；不连接数据库或模型。"""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.api.answer_workflows import router
from app.api.answers import get_generation_provider
from app.api.documents import get_embedding_provider
from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.session import get_session
from app.workflows import service
from app.workflows.contracts import WorkflowConflictError, WorkflowListItem, WorkflowNotFoundError


WORKSPACE_ID = uuid4()
KNOWLEDGE_BASE_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
URL = (f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases/"
       f"{KNOWLEDGE_BASE_ID}/answer-workflows")
SUMMARY_FIELDS = {
    "workflow_id", "workspace_id", "knowledge_base_id", "question", "strategy",
    "max_context_characters", "status", "next_step", "version", "attempt_count",
    "error_code", "retryable", "created_at", "updated_at",
}
SELECTED_COLUMNS = SUMMARY_FIELDS - {"workspace_id", "retryable"}


def row(**changes):
    return dict(
        workflow_id=uuid4(), knowledge_base_id=KNOWLEDGE_BASE_ID, question="如何恢复服务？",
        strategy="vector", max_context_characters=12000, status="pending", next_step="retrieve",
        version=0, attempt_count=0, error_code=None, created_at=NOW, updated_at=NOW,
    ) | changes


def summary(**changes):
    return row() | dict(workspace_id=WORKSPACE_ID, retryable=False) | changes


class FakeSession:
    def __init__(self, rows=(), scalar_results=(KNOWLEDGE_BASE_ID,)):
        self.rows = rows
        self.scalar_results = iter(scalar_results)
        self.scalar_statements = []
        self.list_statements = []
        self.error = None

    def begin(self):
        return nullcontext()

    def scalar(self, statement):
        self.scalar_statements.append(statement)
        return next(self.scalar_results)

    def execute(self, statement):
        self.list_statements.append(statement)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(mappings=lambda: iter(self.rows))


def make_client(session, *, authenticated=True):
    # 直接挂载实际路由和实际 workspace_guard；不导入会读取本机配置的 app.main。
    application = FastAPI()
    application.include_router(router, dependencies=[Depends(require_workspace_member)])
    application.dependency_overrides[get_session] = lambda: session
    if authenticated:
        application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=USER_ID)

    def no_provider():
        pytest.fail("历史列表不得构造 embedding/generation provider")

    application.dependency_overrides[get_embedding_provider] = no_provider
    application.dependency_overrides[get_generation_provider] = no_provider
    return TestClient(application)


@pytest.fixture(autouse=True)
def forbid_models_and_settings(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("历史列表不得检索、生成或读取本机配置")

    monkeypatch.setattr(service, "search_knowledge_base", forbidden)
    monkeypatch.setattr(service, "answer_from_hits", forbidden)
    monkeypatch.setattr(service, "Settings", forbidden)


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_list_contract_contains_only_public_summary_fields(strategy):
    item = WorkflowListItem(**summary(strategy=strategy))
    assert set(item.model_dump()) == SUMMARY_FIELDS
    assert item.strategy == strategy
    assert WorkflowListItem.model_validate_json(item.model_dump_json()) == item


@pytest.mark.parametrize("state", [
    dict(status="pending", next_step="retrieve", version=0, attempt_count=0),
    dict(status="running", next_step="retrieve", version=1, attempt_count=1),
    dict(status="running", next_step="generate", version=2, attempt_count=1),
    dict(status="completed", next_step="done", version=3, attempt_count=1),
    dict(status="failed", next_step="generate", version=3, attempt_count=1,
         error_code="generation_timeout", retryable=True),
    dict(status="failed", next_step="retrieve", version=3, attempt_count=1,
         error_code="evidence_changed", retryable=False),
    dict(status="failed", next_step="generate", version=11, attempt_count=5,
         error_code="generation_error", retryable=False),
])
def test_summary_validates_light_state_without_checkpoint_result_or_lease(state):
    item = WorkflowListItem(**summary(**state))
    assert "checkpoint" not in item.model_dump() and "result" not in item.model_dump()


@pytest.mark.parametrize("changes", [
    {"checkpoint": None}, {"result": None}, {"active_request_id": uuid4()},
    {"creation_request_id": uuid4()}, {"creation_digest": "private"}, {"lease_expires_at": NOW},
    {"status": "unknown"}, {"strategy": "keyword"}, {"next_step": "execute"},
    {"status": "completed"}, {"status": "failed"}, {"status": "running"},
    {"next_step": "done"}, {"error_code": "generation_error"}, {"retryable": True},
    {"attempt_count": 1}, {"version": -1}, {"version": 33}, {"version": True},
    {"attempt_count": 6}, {"attempt_count": "0"}, {"max_context_characters": 12001},
    {"question": ""}, {"question": "问" * 2001}, {"created_at": NOW.replace(tzinfo=None)},
    {"updated_at": "2026-10-18T10:00:00Z"},
    {"question": " \n\t"}, {"version": 1},
    {"status": "running", "attempt_count": 1, "version": 0},
    {"status": "completed", "next_step": "done", "attempt_count": 1, "version": 0},
    {"status": "failed", "attempt_count": 1, "version": 0,
     "error_code": "generation_error", "retryable": True},
    {"updated_at": NOW - timedelta(microseconds=1)},
])
def test_summary_rejects_invalid_or_internal_fields(changes):
    with pytest.raises(ValidationError):
        WorkflowListItem(**summary(**changes))


def test_service_selects_only_summary_columns_and_filters_orders_pages_in_sql():
    session = FakeSession([row()])
    result = service.list_workflows(session, WORKSPACE_ID, KNOWLEDGE_BASE_ID, 10, 7)
    assert len(result) == 1 and result[0].workspace_id == WORKSPACE_ID
    assert len(session.scalar_statements) == len(session.list_statements) == 1
    scope = session.scalar_statements[0].compile(dialect=postgresql.dialect())
    assert WORKSPACE_ID in scope.params.values() and KNOWLEDGE_BASE_ID in scope.params.values()
    statement = session.list_statements[0]
    assert set(statement.selected_columns.keys()) == SELECTED_COLUMNS
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "WHERE answer_workflows.knowledge_base_id =" in sql
    assert "ORDER BY answer_workflows.created_at DESC, answer_workflows.id DESC" in sql
    assert "LIMIT" in sql and "OFFSET" in sql
    assert KNOWLEDGE_BASE_ID in compiled.params.values()
    assert 10 in compiled.params.values() and 7 in compiled.params.values()
    for forbidden in ("checkpoint", "result", "creation_", "active_request", "lease_",
                      "answer_workflow_attempts", "user_id"):
        assert forbidden not in sql


def test_empty_service_list_still_checks_knowledge_base_parent():
    session = FakeSession()
    assert service.list_workflows(session, WORKSPACE_ID, KNOWLEDGE_BASE_ID) == []
    assert len(session.scalar_statements) == len(session.list_statements) == 1


def test_wrong_workspace_knowledge_base_is_not_found_before_listing():
    session = FakeSession(scalar_results=(None,))
    with pytest.raises(WorkflowNotFoundError):
        service.list_workflows(session, WORKSPACE_ID, KNOWLEDGE_BASE_ID)
    assert not session.list_statements


@pytest.mark.parametrize("attempt_count,retryable", [(1, True), (5, False)])
def test_service_computes_retryable_without_loading_checkpoint(attempt_count, retryable):
    session = FakeSession([row(status="failed", next_step="generate", version=11,
                               attempt_count=attempt_count, error_code="generation_error")])
    result = service.list_workflows(session, WORKSPACE_ID, KNOWLEDGE_BASE_ID)
    assert result[0].retryable is retryable


def test_invalid_selected_state_is_not_published_as_a_healthy_summary():
    session = FakeSession([row(status="completed", next_step="retrieve")])
    with pytest.raises(WorkflowConflictError):
        service.list_workflows(session, WORKSPACE_ID, KNOWLEDGE_BASE_ID)


@pytest.mark.parametrize("changes", [
    {"question": " \n\t"}, {"version": 1},
    {"status": "running", "attempt_count": 1, "version": 0},
    {"updated_at": NOW - timedelta(microseconds=1)},
])
def test_http_corrupted_summary_fields_produce_fixed_conflict(changes):
    session = FakeSession([row(**changes)], scalar_results=(USER_ID, KNOWLEDGE_BASE_ID))
    with make_client(session) as client:
        response = client.get(URL)
    assert response.status_code == 409
    assert response.json() == {"detail": "工作流保存状态无效，请重新读取"}


def test_http_default_list_uses_real_membership_and_parent_scope_without_providers():
    session = FakeSession([row(strategy="hybrid")], scalar_results=(USER_ID, KNOWLEDGE_BASE_ID))
    with make_client(session) as client:
        response = client.get(URL)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1 and set(items[0]) == SUMMARY_FIELDS
    assert items[0]["workspace_id"] == str(WORKSPACE_ID)
    assert items[0]["strategy"] == "hybrid"
    assert len(session.scalar_statements) == 2
    membership = str(session.scalar_statements[0])
    assert "workspace_memberships" in membership and "user_id" in membership
    compiled = session.list_statements[0].compile(dialect=postgresql.dialect())
    assert 20 in compiled.params.values() and 0 in compiled.params.values()


def test_http_pagination_boundaries_and_member_shared_tasks():
    session = FakeSession([row()], scalar_results=(USER_ID, KNOWLEDGE_BASE_ID))
    with make_client(session) as client:
        response = client.get(URL, params={"limit": 100, "offset": 3})
    assert response.status_code == 200
    sql = str(session.list_statements[0])
    assert "owner" not in sql and "user_id" not in sql
    params = session.list_statements[0].compile().params
    assert 100 in params.values() and 3 in params.values()


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": -1}, {"limit": "a"}, {"limit": "1.5"},
    {"offset": -1}, {"offset": "bad"}, {"offset": "0.5"}, {"offset": 2**63},
])
def test_http_rejects_invalid_pagination_before_business_query(params):
    session = FakeSession(scalar_results=(USER_ID,))
    with make_client(session) as client:
        response = client.get(URL, params=params)
    assert response.status_code == 422
    assert not session.list_statements


def test_http_no_login_is_unauthorized_without_reading_any_task():
    session = FakeSession()
    with make_client(session, authenticated=False) as client:
        response = client.get(URL)
    assert response.status_code == 401
    assert not session.scalar_statements and not session.list_statements


def test_http_nonmember_is_hidden_before_knowledge_base_or_task_read():
    session = FakeSession(scalar_results=(None,))
    with make_client(session) as client:
        response = client.get(URL)
    assert response.status_code == 404
    assert len(session.scalar_statements) == 1 and not session.list_statements


def test_http_wrong_knowledge_base_parent_is_hidden():
    session = FakeSession(scalar_results=(USER_ID, None))
    with make_client(session) as client:
        response = client.get(URL)
    assert response.status_code == 404 and not session.list_statements


@pytest.mark.parametrize("failure,status", [(SQLAlchemyError("private-db"), 503),
                                           (None, 409)])
def test_http_errors_stay_fixed_without_leaking_saved_or_database_details(failure, status):
    session = FakeSession([row(status="completed")], scalar_results=(USER_ID, KNOWLEDGE_BASE_ID))
    session.error = failure
    with make_client(session) as client:
        response = client.get(URL)
    assert response.status_code == status
    assert "private-db" not in response.text and "如何恢复服务" not in response.text


def test_http_missing_query_returns_empty_list_only_after_access_checks():
    session = FakeSession(scalar_results=(USER_ID, KNOWLEDGE_BASE_ID))
    with make_client(session) as client:
        response = client.get(URL)
    assert response.status_code == 200 and response.json() == []
    assert len(session.scalar_statements) == 2 and len(session.list_statements) == 1
