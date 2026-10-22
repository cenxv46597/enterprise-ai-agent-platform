"""Real single-chunk SQL reads in an owned eaap_test schema; no model or file I/O.

Only the dedicated PostgreSQL entry supplies the temporary connection. Identity is
synthetic, while membership checks, resource scoping and SELECTs use PostgreSQL.
"""

from collections.abc import Iterator
from dataclasses import dataclass
import os
import re
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, Text, cast, create_engine, event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import documents as document_api
from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.base import Base
from app.db.models import Chunk, Document, KnowledgeBase, User, Workspace, WorkspaceMembership
from app.db.session import get_session
from test_answer_workflow_listing_db import _safe_test_url


MODELS = (User, Workspace, WorkspaceMembership, KnowledgeBase, Document, Chunk)
TABLES = [model.__table__ for model in MODELS]
PUBLIC_FIELDS = {"id", "document_id", "chunk_index", "content", "start_char", "end_char"}


@dataclass
class ChunkReadContext:
    client: TestClient
    engine: Engine
    workspace_id: UUID
    other_workspace_id: UUID
    knowledge_base_id: UUID
    other_base_id: UUID
    foreign_base_id: UUID
    document_id: UUID
    sibling_document_id: UUID
    other_base_document_id: UUID
    foreign_document_id: UUID
    parsed_document_id: UUID
    chunk_ids: list[UUID]
    foreign_chunk_id: UUID
    parsed_chunk_id: UUID
    expected: dict
    owner_id: UUID
    member_id: UUID
    outsider_id: UUID

    def url(self, chunk_id: UUID | None = None, *, workspace_id: UUID | None = None,
            knowledge_base_id: UUID | None = None, document_id: UUID | None = None) -> str:
        path = (f"/api/v1/workspaces/{workspace_id or self.workspace_id}/knowledge-bases/"
                f"{knowledge_base_id or self.knowledge_base_id}/documents/"
                f"{document_id or self.document_id}/chunks")
        return path if chunk_id is None else f"{path}/{chunk_id}"

    def as_user(self, user_id: UUID) -> None:
        # Replace only authentication identity, never the membership dependency.
        self.client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

    def read(self, url: str):
        statements = []

        def record(_connection, _cursor, statement, _parameters, context, _executemany):
            statements.append((statement, context.compiled.statement if context.compiled else None))

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            response = self.client.get(url)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        # Even transient/rolled-back writes would violate this read endpoint.
        assert statements and all(getattr(compiled, "is_select", False) for _sql, compiled in statements)
        return response, statements

    def snapshot(self) -> dict:
        # pgvector results can be arrays: a textual cast gives an exact stable
        # comparison, including the synthetic vector, without ndarray equality.
        with Session(self.engine) as session:
            return {
                model.__tablename__: session.execute(select(*[
                    cast(column, Text).label(column.key) if column.key == "embedding" else column
                    for column in model.__table__.columns
                ]).order_by(*model.__table__.primary_key.columns)).all()
                for model in MODELS
            }


@pytest.fixture
def chunk_read_db_context(monkeypatch: pytest.MonkeyPatch) -> Iterator[ChunkReadContext]:
    value = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not value:
        pytest.fail("Use the dedicated PostgreSQL entry and its temporary eaap_test identity", pytrace=False)
    # Reuse the existing independent-database URL check without consulting any
    # inherited application connection or reading private settings.
    url = _safe_test_url(value, None)
    if (url.host != "127.0.0.1" or not url.port or not 1 <= url.port <= 65535
            or not url.password or not re.fullmatch(r"eaap_check_[0-9a-f]{32}", url.username or "")):
        pytest.fail("Refusing a target outside the temporary test identity", pytrace=False)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Chunk reads must not load private settings, files or model providers")

    monkeypatch.setattr(document_api, "Settings", forbidden)
    monkeypatch.setattr(document_api, "OpenAICompatibleEmbeddingProvider", forbidden)
    monkeypatch.setattr(document_api, "LocalDocumentStore", forbidden)
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={
        "connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=5000",
    })
    schema = f"eaap_test_{uuid4().hex}"
    created = False
    try:
        try:
            with engine.begin() as connection:
                if (connection.scalar(select(func.current_database())) != "eaap_test"
                        or connection.scalar(select(func.current_user())) != url.username):
                    pytest.fail("Actual database or temporary identity does not match", pytrace=False)
                connection.execute(CreateSchema(schema))
            created = True
        except SQLAlchemyError:
            pytest.fail("Cannot prepare the independent test schema; connection details omitted", pytrace=False)

        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        # public.vector is a prerequisite prepared by the runner; no extension,
        # public table, migration or unrelated schema is changed by this fixture.
        Base.metadata.create_all(scoped_engine, tables=TABLES)
        workspace_id, other_workspace_id = uuid4(), uuid4()
        knowledge_base_id, other_base_id, foreign_base_id = uuid4(), uuid4(), uuid4()
        document_id, sibling_document_id, other_base_document_id = uuid4(), uuid4(), uuid4()
        foreign_document_id, parsed_document_id = uuid4(), uuid4()
        owner_id, member_id, outsider_id = uuid4(), uuid4(), uuid4()
        chunk_ids = [uuid4() for _index in range(25)]
        foreign_chunk_id, parsed_chunk_id = uuid4(), uuid4()
        target_index = 24
        expected = {}

        with Session(scoped_engine) as session, session.begin():
            session.add_all([
                Workspace(id=workspace_id, name="虚构单块测试空间"),
                Workspace(id=other_workspace_id, name="虚构另一空间"),
                User(id=owner_id, username="chunk_read_owner", password_hash="fixture-only-placeholder"),
                User(id=member_id, username="chunk_read_member", password_hash="fixture-only-placeholder"),
                User(id=outsider_id, username="chunk_read_outsider", password_hash="fixture-only-placeholder"),
            ])
            session.flush()
            session.add_all([
                KnowledgeBase(id=knowledge_base_id, workspace_id=workspace_id, name="虚构单块库"),
                KnowledgeBase(id=other_base_id, workspace_id=workspace_id, name="虚构同空间另一库"),
                KnowledgeBase(id=foreign_base_id, workspace_id=other_workspace_id, name="虚构跨空间库"),
                WorkspaceMembership(workspace_id=workspace_id, user_id=owner_id, role="owner"),
                WorkspaceMembership(workspace_id=workspace_id, user_id=member_id, role="member"),
                WorkspaceMembership(workspace_id=other_workspace_id, user_id=owner_id, role="owner"),
            ])
            session.flush()
            for identifier, parent, state in (
                (document_id, knowledge_base_id, "ready"),
                (sibling_document_id, knowledge_base_id, "ready"),
                (other_base_document_id, other_base_id, "ready"),
                (foreign_document_id, foreign_base_id, "ready"),
                (parsed_document_id, knowledge_base_id, "parsed"),
            ):
                session.add(Document(
                    id=identifier, knowledge_base_id=parent,
                    original_filename="同名虚构材料.md", media_type="text/markdown",
                    storage_key=f"fixture-not-a-file/{identifier}", byte_size=1,
                    sha256="a" * 64, status=state,
                    chunk_size=1000, chunk_overlap=0, chunking_version="fixture-v1",
                ))
            session.flush()
            position = 0
            for index, identifier in enumerate(chunk_ids, start=1):
                # Longer than a preview; emoji uses one PostgreSQL/Python
                # character, and repeated wording must not select the first hit.
                content = f"虚构段落{index}。" + "😀仅正式员工可报销；同一句重复。" * 40 + f"结束{index}。"
                end = position + len(content)
                session.add(Chunk(
                    id=identifier, document_id=document_id, chunk_index=index,
                    content=content, start_char=position, end_char=end,
                    embedding=[0.25] + [0.0] * 1023 if index == target_index else None,
                ))
                if index == target_index:
                    expected = dict(id=str(identifier), document_id=str(document_id), chunk_index=index,
                                    content=content, start_char=position, end_char=end)
                position = end
            for identifier, parent in (
                (uuid4(), sibling_document_id), (uuid4(), other_base_document_id),
                (foreign_chunk_id, foreign_document_id), (parsed_chunk_id, parsed_document_id),
            ):
                content = "虚构同名文档的另一块😀。"
                session.add(Chunk(id=identifier, document_id=parent, chunk_index=1,
                                  content=content, start_char=0, end_char=len(content)))

        def session_provider() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        application = FastAPI()
        application.include_router(document_api.router, dependencies=[Depends(require_workspace_member)])
        application.dependency_overrides[get_session] = session_provider
        application.dependency_overrides[document_api.get_embedding_provider] = forbidden
        application.dependency_overrides[document_api.get_document_store] = forbidden
        application.dependency_overrides[document_api.get_max_upload_bytes] = forbidden
        with TestClient(application) as client:
            context = ChunkReadContext(
                client, scoped_engine, workspace_id, other_workspace_id, knowledge_base_id,
                other_base_id, foreign_base_id, document_id, sibling_document_id,
                other_base_document_id, foreign_document_id, parsed_document_id, chunk_ids,
                foreign_chunk_id, parsed_chunk_id, expected, owner_id, member_id, outsider_id,
            )
            context.as_user(owner_id)
            yield context
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    if (connection.scalar(select(func.current_database())) != "eaap_test"
                            or connection.scalar(select(func.current_user())) != url.username):
                        pytest.fail("Cleanup target does not match the temporary database identity", pytrace=False)
                    # Resolve only this generated schema; refuse cleanup if its
                    # ownership no longer belongs to this run's temporary role.
                    owner = connection.scalar(text(
                        "SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON n.nspowner=r.oid "
                        "WHERE n.nspname=:schema"
                    ), {"schema": schema})
                    if owner != url.username:
                        pytest.fail("Test schema ownership changed; cleanup stopped", pytrace=False)
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            engine.dispose()


def test_db_chunk_read_scopes_projection_and_unchanged_data(chunk_read_db_context: ChunkReadContext):
    context = chunk_read_db_context
    before = context.snapshot()
    target_id = context.chunk_ids[23]
    assert context.expected["chunk_index"] == 24 and context.expected["start_char"] > 0
    assert len(context.expected["content"]) > 500

    for user_id in (context.owner_id, context.member_id):
        context.as_user(user_id)
        response, statements = context.read(context.url(target_id))
        assert response.status_code == 200 and response.json() == context.expected
        assert set(response.json()) == PUBLIC_FIELDS
        # One real membership query followed by one scoped projected chunk JOIN,
        # never a document lookup plus the complete chunk collection.
        assert len(statements) == 2
        assert "workspace_memberships" in statements[0][0]
        sql, statement = statements[1]
        assert sql.lower().count(" join ") == 2
        assert "documents" in sql and "knowledge_bases" in sql
        columns = list(statement.selected_columns)
        assert len(columns) == 6 and {column.key for column in columns} == PUBLIC_FIELDS
        assert all(base.table.name == "chunks" for column in columns for base in column.base_columns)
        assert "embedding" not in sql.lower()

    context.as_user(context.owner_id)
    wrong_scope = [
        context.url(target_id, workspace_id=context.other_workspace_id),
        context.url(target_id, knowledge_base_id=context.other_base_id),
        context.url(target_id, document_id=context.sibling_document_id),
        context.url(target_id, document_id=context.other_base_document_id),
        context.url(context.foreign_chunk_id),
        context.url(context.foreign_chunk_id, knowledge_base_id=context.foreign_base_id,
                    document_id=context.foreign_document_id),
        context.url(target_id, knowledge_base_id=uuid4()),
        context.url(target_id, document_id=uuid4()),
        context.url(uuid4()),
    ]
    for path in wrong_scope:
        response, _statements = context.read(path)
        assert response.status_code == 404 and response.json() == {"detail": "文本块不存在"}

    # Missing workspaces and non-members are rejected by the actual membership
    # SELECT before any document/chunk content query can be issued.
    response, statements = context.read(context.url(target_id, workspace_id=uuid4()))
    assert response.status_code == 404 and response.json() == {"detail": "工作区不存在"}
    assert len(statements) == 1 and "workspace_memberships" in statements[0][0]
    context.as_user(context.outsider_id)
    response, statements = context.read(context.url(target_id))
    assert response.status_code == 404 and response.json() == {"detail": "工作区不存在"}
    assert len(statements) == 1 and "workspace_memberships" in statements[0][0]

    context.as_user(context.owner_id)
    # This is a general read API, not the frontend's ready-to-answer gate.
    parsed, _statements = context.read(context.url(
        context.parsed_chunk_id, document_id=context.parsed_document_id,
    ))
    assert parsed.status_code == 200 and set(parsed.json()) == PUBLIC_FIELDS
    assert parsed.json()["document_id"] == str(context.parsed_document_id)
    # Keep the old ordered list wire contract, including a target beyond twenty.
    listed, _statements = context.read(context.url())
    assert listed.status_code == 200 and len(listed.json()) == 25
    assert [row["id"] for row in listed.json()] == [str(identifier) for identifier in context.chunk_ids]
    assert all(set(row) == PUBLIC_FIELDS for row in listed.json())
    assert listed.json()[23] == context.expected
    assert context.snapshot() == before
