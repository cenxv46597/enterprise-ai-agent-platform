"""固定文档状态查询的真实 PostgreSQL 范围与只读边界；不调用模型。"""

import os
from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Document, KnowledgeBase, Workspace
from app.knowledge.document_status import (
    DocumentProcessingStatus,
    DocumentStatusNotFoundError,
    get_document_status,
)


STATES = ["uploaded", "processing", "parsed", "ready", "failed"]


@pytest.fixture
def status_db() -> Iterator[SimpleNamespace]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置独立 EAAP_TEST_DATABASE_URL 后运行文档状态测试")
    test_url = make_url(database_url)
    daily_url = Settings().database_url
    if daily_url and test_url == make_url(daily_url):
        pytest.fail("文档状态测试不能使用日常应用数据库")
    if test_url.get_backend_name() != "postgresql" or test_url.database != "eaap_test" or test_url.query:
        pytest.fail("文档状态测试只允许独立 eaap_test PostgreSQL 数据库")

    engine = create_engine(database_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    schema = f"eaap_document_status_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        scoped = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped, tables=[
            Workspace.__table__, KnowledgeBase.__table__, Document.__table__,
        ])
        context = SimpleNamespace(
            engine=scoped, workspace_a=uuid4(), workspace_b=uuid4(),
            kb_a=uuid4(), kb_a_second=uuid4(), kb_b=uuid4(),
            documents={state: uuid4() for state in STATES}, foreign_document=uuid4(),
        )
        with Session(scoped) as session, session.begin():
            session.add_all([
                Workspace(id=context.workspace_a, name="A 空间"),
                Workspace(id=context.workspace_b, name="B 空间"),
            ])
            session.flush()
            session.add_all([
                KnowledgeBase(id=context.kb_a, workspace_id=context.workspace_a, name="A 知识库"),
                KnowledgeBase(id=context.kb_a_second, workspace_id=context.workspace_a, name="A 第二知识库"),
                KnowledgeBase(id=context.kb_b, workspace_id=context.workspace_b, name="B 知识库"),
            ])
            session.flush()
            documents = [(identifier, context.kb_a, state) for state, identifier in context.documents.items()]
            documents.append((context.foreign_document, context.kb_b, "ready"))
            for identifier, kb_id, state in documents:
                session.add(Document(
                    id=identifier, knowledge_base_id=kb_id, original_filename="同名资料.txt",
                    media_type="text/plain", storage_key=str(identifier), byte_size=1,
                    sha256="a" * 64, status=state,
                    error_code="parse_failed" if state == "failed" else None,
                ))
        yield context
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


@pytest.mark.parametrize("state", STATES)
def test_reads_all_processing_states(status_db: SimpleNamespace, state: str) -> None:
    with Session(status_db.engine) as session:
        result = get_document_status(
            session, status_db.workspace_a, status_db.kb_a, status_db.documents[state],
        )
    assert isinstance(result, DocumentProcessingStatus)
    assert result.model_dump() == {
        "document_id": status_db.documents[state], "original_filename": "同名资料.txt",
        "status": state, "error_code": "parse_failed" if state == "failed" else None,
    }


@pytest.mark.parametrize("case", ["wrong_workspace", "wrong_knowledge_base", "foreign_document", "missing_document"])
def test_outside_bound_scope_is_not_found(status_db: SimpleNamespace, case: str) -> None:
    workspace_id, kb_id = status_db.workspace_a, status_db.kb_a
    document_id = status_db.documents["ready"]
    if case == "wrong_workspace":
        workspace_id = status_db.workspace_b
    elif case == "wrong_knowledge_base":
        kb_id = status_db.kb_a_second
    elif case == "foreign_document":
        document_id = status_db.foreign_document
    else:
        document_id = uuid4()
    with Session(status_db.engine) as session:
        with pytest.raises(DocumentStatusNotFoundError, match="^文档不存在$"):
            get_document_status(session, workspace_id, kb_id, document_id)


def test_duplicate_names_are_distinguished_by_document_id(status_db: SimpleNamespace) -> None:
    with Session(status_db.engine) as session:
        ready = get_document_status(
            session, status_db.workspace_a, status_db.kb_a, status_db.documents["ready"],
        )
        failed = get_document_status(
            session, status_db.workspace_a, status_db.kb_a, status_db.documents["failed"],
        )
    assert ready.original_filename == failed.original_filename
    assert ready.document_id == status_db.documents["ready"]
    assert failed.document_id == status_db.documents["failed"]
    assert (ready.status, ready.error_code) == ("ready", None)
    assert (failed.status, failed.error_code) == ("failed", "parse_failed")


def test_dirty_session_is_not_flushed_and_only_selects_are_sent(status_db: SimpleNamespace) -> None:
    statements = []

    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.split(None, 1)[0].upper())

    with Session(status_db.engine) as session:
        document = session.get(Document, status_db.documents["ready"])
        document.original_filename = "尚未提交的名字.txt"
        document.status = "failed"
        document.error_code = "pending_change"
        event.listen(status_db.engine, "before_cursor_execute", record_statement)
        try:
            result = get_document_status(
                session, status_db.workspace_a, status_db.kb_a, document.id,
            )
            assert (result.original_filename, result.status, result.error_code) == ("同名资料.txt", "ready", None)
            assert document in session.dirty
            assert session.in_transaction()
            assert statements and all(statement == "SELECT" for statement in statements)
        finally:
            event.remove(status_db.engine, "before_cursor_execute", record_statement)
            session.rollback()
