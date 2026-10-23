"""文档文件名筛选与分页的 HTTP/SQL 契约；不连接数据库、文件或模型。"""

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.dialects import postgresql

from app.api import documents
from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.session import get_session


WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, USER_ID = (uuid4() for _ in range(4))
PUBLIC_FIELDS = {
    "id", "knowledge_base_id", "original_filename", "media_type", "byte_size", "sha256",
    "status", "error_code", "created_at", "updated_at",
}


def document_row():
    return SimpleNamespace(
        id=DOCUMENT_ID, knowledge_base_id=KNOWLEDGE_BASE_ID, original_filename="研发资料.md",
        media_type="text/markdown", byte_size=10, sha256="a" * 64, status="parsed",
        error_code=None, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        storage_key="fixture-private-path", content="fixture-private-content",
    )


def documents_url(workspace=WORKSPACE_ID, knowledge_base=KNOWLEDGE_BASE_ID):
    return f"/api/v1/workspaces/{workspace}/knowledge-bases/{knowledge_base}/documents"


class MemorySession:
    def __init__(self, rows=(), *, member=True, parent=True):
        self.rows, self.member, self.parent = rows, member, parent
        self.membership_queries, self.parent_queries, self.list_queries = [], [], []

    def begin(self):
        return nullcontext()

    def scalar(self, statement):
        if "workspace_memberships" in str(statement):
            self.membership_queries.append(statement)
            return USER_ID if self.member else None
        self.parent_queries.append(statement)
        return KNOWLEDGE_BASE_ID if self.parent else None

    def scalars(self, statement):
        self.list_queries.append(statement)
        return self.rows


def make_client(session, *, authenticated=True):
    application = FastAPI()
    application.include_router(documents.router, dependencies=[Depends(require_workspace_member)])
    application.dependency_overrides[get_session] = lambda: session
    if authenticated:
        application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=USER_ID)
    return TestClient(application)


@pytest.fixture(autouse=True)
def forbid_private_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("文档列表不得加载私有配置、文件或模型")

    for name in ("Settings", "LocalDocumentStore", "OpenAICompatibleEmbeddingProvider",
                 "persist_document_chunks", "embed_document_chunks", "ingest_document"):
        monkeypatch.setattr(documents, name, forbidden)


def test_old_document_list_keeps_array_public_fields_default_page_and_order():
    session = MemorySession([document_row()])
    with make_client(session) as client:
        response = client.get(documents_url())
    assert response.status_code == 200
    assert isinstance(response.json(), list) and set(response.json()[0]) == PUBLIC_FIELDS
    assert "fixture-private" not in response.text
    compiled = session.list_queries[0].compile(dialect=postgresql.dialect())
    sql, params = str(compiled), compiled.params
    assert "ORDER BY documents.created_at, documents.id" in sql
    assert "LIMIT" in sql and "OFFSET" in sql and "ILIKE" not in sql
    assert set(params.values()) == {WORKSPACE_ID, KNOWLEDGE_BASE_ID, 20, 0}


def test_filename_is_trimmed_literal_bound_and_combined_with_scope_status_and_page():
    session = MemorySession()
    with make_client(session) as client:
        response = client.get(documents_url(), params={
            "filename_query": "  预算%_/REPORT  ", "status": "failed", "limit": 7, "offset": 21,
        })
    assert response.status_code == 200 and response.json() == []
    compiled = session.list_queries[0].compile(dialect=postgresql.dialect())
    sql, params = str(compiled), compiled.params
    assert "JOIN knowledge_bases ON documents.knowledge_base_id = knowledge_bases.id" in sql
    assert "documents.knowledge_base_id =" in sql and "knowledge_bases.workspace_id =" in sql
    assert "documents.status =" in sql and "documents.original_filename ILIKE" in sql
    assert "ESCAPE '/'" in sql and "预算" not in sql
    assert set(params.values()) == {WORKSPACE_ID, KNOWLEDGE_BASE_ID, "failed", "预算/%/_//REPORT", 7, 21}
    assert sql.index("ILIKE") < sql.index("ORDER BY") < sql.index("LIMIT")


@pytest.mark.parametrize("query", ["", "   ", "x" * 256, "😀" * 256, "资料\x00.txt"])
def test_explicit_empty_overlong_or_null_filename_is_rejected_without_business_query(query):
    session = MemorySession()
    with make_client(session) as client:
        response = client.get(documents_url(), params={"filename_query": query})
    assert response.status_code == 422
    assert not session.parent_queries and not session.list_queries


@pytest.mark.parametrize("query", ["文", "😀" * 255, "  " + "文" * 255 + "  "])
def test_filename_limits_count_unicode_code_points_after_trimming(query):
    session = MemorySession()
    with make_client(session) as client:
        response = client.get(documents_url(), params={"filename_query": query})
    assert response.status_code == 200
    compiled = session.list_queries[0].compile(dialect=postgresql.dialect())
    assert query.strip() in compiled.params.values()


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 2**63}])
def test_filename_filter_preserves_existing_pagination_validation(params):
    session = MemorySession()
    with make_client(session) as client:
        response = client.get(documents_url(), params={"filename_query": "资料"} | params)
    assert response.status_code == 422 and not session.parent_queries and not session.list_queries


def test_anonymous_and_nonmember_cannot_query_filtered_documents():
    for authenticated, member, status in ((False, True, 401), (True, False, 404)):
        session = MemorySession([document_row()], member=member)
        with make_client(session, authenticated=authenticated) as client:
            response = client.get(documents_url(), params={"filename_query": "资料"})
        assert response.status_code == status
        assert not session.parent_queries and not session.list_queries


def test_missing_or_wrong_parent_keeps_safe_404_instead_of_empty_filtered_page():
    session = MemorySession(parent=False)
    with make_client(session) as client:
        response = client.get(documents_url(), params={"filename_query": "资料", "offset": 20})
    assert response.status_code == 404 and response.json() == {"detail": "知识库不存在"}
    assert len(session.parent_queries) == 1 and not session.list_queries
    compiled = session.parent_queries[0].compile(dialect=postgresql.dialect())
    assert set(compiled.params.values()) == {WORKSPACE_ID, KNOWLEDGE_BASE_ID}
