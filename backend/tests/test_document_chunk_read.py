"""单块读取的真实成员依赖、HTTP 与 SQL 投影契约；不连接数据库或模型。"""

from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api import documents
from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.session import get_session


WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, CHUNK_ID, USER_ID = (uuid4() for _ in range(5))
FIELDS = {"id", "document_id", "chunk_index", "content", "start_char", "end_char"}
CONTENT = "完整段落😀：保留条件与例外。" * 30


def chunk_row(**changes):
    return dict(id=CHUNK_ID, document_id=DOCUMENT_ID, chunk_index=7, content=CONTENT,
                start_char=200, end_char=200 + len(CONTENT)) | changes


def chunk_url(workspace=WORKSPACE_ID, knowledge_base=KNOWLEDGE_BASE_ID,
              document=DOCUMENT_ID, chunk=CHUNK_ID):
    return (f"/api/v1/workspaces/{workspace}/knowledge-bases/{knowledge_base}"
            f"/documents/{document}/chunks/{chunk}")


class MemorySession:
    def __init__(self, row=None, *, member=True):
        self.row, self.member = row, member
        self.membership_queries, self.chunk_queries, self.list_queries = [], [], []
        self.document_queries = []

    def begin(self):
        return nullcontext()

    def scalar(self, statement):
        if "workspace_memberships" in str(statement):
            self.membership_queries.append(statement)
            return USER_ID if self.member else None
        self.document_queries.append(statement)
        return SimpleNamespace(id=DOCUMENT_ID, knowledge_base_id=KNOWLEDGE_BASE_ID)

    def execute(self, statement):
        self.chunk_queries.append(statement)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: self.row))

    def scalars(self, statement):
        self.list_queries.append(statement)
        return [] if self.row is None else [SimpleNamespace(**self.row)]


def make_client(session, *, authenticated=True):
    app = FastAPI()
    app.include_router(documents.router, dependencies=[Depends(require_workspace_member)])
    app.dependency_overrides[get_session] = lambda: session
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=USER_ID)

    def forbidden_provider():
        pytest.fail("读取文本块不得构造模型或访问文件存储")

    app.dependency_overrides[documents.get_embedding_provider] = forbidden_provider
    app.dependency_overrides[documents.get_document_store] = forbidden_provider
    return TestClient(app)


@pytest.fixture(autouse=True)
def forbid_write_or_private_settings(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("读取文本块不得切块、向量化或加载私有配置")

    monkeypatch.setattr(documents, "Settings", forbidden)
    monkeypatch.setattr(documents, "persist_document_chunks", forbidden)
    monkeypatch.setattr(documents, "embed_document_chunks", forbidden)


def test_single_chunk_returns_exact_public_shape_and_complete_unicode_content():
    session = MemorySession(chunk_row(embedding=[1.0] * 1024, private="not-public"))
    with make_client(session) as client:
        response = client.get(chunk_url())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == FIELDS and body["content"] == CONTENT and len(body["content"]) > 300
    assert body["id"] == str(CHUNK_ID) and body["document_id"] == str(DOCUMENT_ID)
    assert body["chunk_index"] == 7 and body["end_char"] - body["start_char"] == len(CONTENT)
    assert len(session.membership_queries) == len(session.chunk_queries) == 1
    assert not session.document_queries and not session.list_queries


def test_single_select_binds_all_four_resource_ids_and_does_not_load_vectors_or_other_chunks():
    session = MemorySession(chunk_row())
    with make_client(session) as client:
        assert client.get(chunk_url()).status_code == 200
    statement = session.chunk_queries[0]
    assert set(statement.selected_columns.keys()) == FIELDS
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "JOIN documents ON chunks.document_id = documents.id" in sql
    assert "JOIN knowledge_bases ON documents.knowledge_base_id = knowledge_bases.id" in sql
    for condition in ("chunks.id =", "chunks.document_id =", "documents.knowledge_base_id =",
                      "knowledge_bases.workspace_id ="):
        assert condition in sql
    assert set(compiled.params.values()) == {WORKSPACE_ID, KNOWLEDGE_BASE_ID, DOCUMENT_ID, CHUNK_ID}
    assert "embedding" not in sql and "ORDER BY" not in sql and "LIMIT" not in sql
    assert "documents.status" not in sql  # Parsed document chunks remain readable like the existing list.
    membership = session.membership_queries[0].compile(dialect=postgresql.dialect())
    assert set(membership.params.values()) == {WORKSPACE_ID, USER_ID}


def test_anonymous_user_is_rejected_before_any_membership_or_chunk_read():
    session = MemorySession(chunk_row())
    with make_client(session, authenticated=False) as client:
        response = client.get(chunk_url())
    assert response.status_code == 401
    assert not session.membership_queries and not session.chunk_queries and not session.document_queries


def test_nonmember_is_hidden_before_any_chunk_or_document_query():
    session = MemorySession(chunk_row(), member=False)
    with make_client(session) as client:
        response = client.get(chunk_url())
    assert response.status_code == 404 and response.json() == {"detail": "工作区不存在"}
    assert len(session.membership_queries) == 1
    assert not session.chunk_queries and not session.document_queries and not session.list_queries


@pytest.mark.parametrize("changed", [None, "workspace", "knowledge_base", "document", "chunk"])
def test_missing_or_out_of_scope_chunk_has_the_same_safe_not_found_response(changed):
    identifiers = dict(workspace=WORKSPACE_ID, knowledge_base=KNOWLEDGE_BASE_ID,
                       document=DOCUMENT_ID, chunk=CHUNK_ID)
    if changed is not None:
        identifiers[changed] = uuid4()
    session = MemorySession()
    with make_client(session) as client:
        response = client.get(chunk_url(**identifiers))
    assert response.status_code == 404 and response.json() == {"detail": "文本块不存在"}
    assert "content" not in response.json()
    assert len(session.chunk_queries) == 1
    compiled = session.chunk_queries[0].compile(dialect=postgresql.dialect())
    assert set(compiled.params.values()) == set(identifiers.values())


@pytest.mark.parametrize("invalid", ["workspace", "knowledge_base", "document", "chunk"])
def test_invalid_resource_uuid_is_rejected_without_business_sql(invalid):
    identifiers = dict(workspace=WORKSPACE_ID, knowledge_base=KNOWLEDGE_BASE_ID,
                       document=DOCUMENT_ID, chunk=CHUNK_ID)
    identifiers[invalid] = "not-a-uuid"
    session = MemorySession(chunk_row())
    with make_client(session) as client:
        response = client.get(chunk_url(**identifiers))
    assert response.status_code == 422 and not session.chunk_queries and not session.document_queries


def test_existing_chunk_list_still_returns_an_array_without_using_the_single_chunk_query():
    session = MemorySession(chunk_row())
    list_url = chunk_url().rsplit("/", 1)[0]
    with make_client(session) as client:
        response = client.get(list_url)
    assert response.status_code == 200 and isinstance(response.json(), list)
    assert len(response.json()) == 1 and set(response.json()[0]) == FIELDS
    assert response.json()[0]["id"] == str(CHUNK_ID)
    assert len(session.document_queries) == len(session.list_queries) == 1
    assert not session.chunk_queries


def test_existing_empty_chunk_list_is_still_an_empty_array():
    session = MemorySession()
    with make_client(session) as client:
        response = client.get(chunk_url().rsplit("/", 1)[0])
    assert response.status_code == 200 and response.json() == []
    assert len(session.document_queries) == len(session.list_queries) == 1
    assert not session.chunk_queries
