"""使用独立测试库验证 PostgreSQL 约束。

每次运行都使用随机模式并在结束时清理，不修改测试库的默认模式。
"""

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import Base, Document, KnowledgeBase, Workspace


@pytest.fixture(scope="module")
def test_database() -> Iterator[tuple[Engine, str]]:
    test_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("Set EAAP_TEST_DATABASE_URL to run PostgreSQL constraint tests")

    app_url = Settings().database_url
    if app_url and make_url(test_url) == make_url(app_url):
        pytest.fail("EAAP_TEST_DATABASE_URL must differ from EAAP_DATABASE_URL")

    engine = create_engine(test_url)
    schema = f"eaap_test_{uuid4().hex}"
    # 模式名称仅来自 uuid4，不接收外部输入。
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.connect().execution_options(schema_translate_map={None: schema}) as connection:
            Base.metadata.create_all(connection)
            connection.commit()
        yield engine, schema
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def db_connection(test_database: tuple[Engine, str]) -> Iterator[Connection]:
    engine, schema = test_database
    with engine.connect().execution_options(schema_translate_map={None: schema}) as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    with Session(db_connection) as session:
        yield session


def _assert_constraint(session: Session, row: object, name: str) -> None:
    with pytest.raises(IntegrityError) as error:
        with session.begin_nested():
            session.add(row)
            session.flush()
    assert error.value.orig.diag.constraint_name == name


def _knowledge_base(session: Session) -> KnowledgeBase:
    workspace = Workspace(name="Acme")
    session.add(workspace)
    session.flush()
    knowledge_base = KnowledgeBase(workspace_id=workspace.id, name="Manuals")
    session.add(knowledge_base)
    session.flush()
    return knowledge_base


def _document(knowledge_base_id: UUID, **overrides: object) -> Document:
    values: dict[str, object] = {
        "knowledge_base_id": knowledge_base_id,
        "original_filename": "manual.pdf",
        "media_type": "application/pdf",
        "storage_key": "documents/one.pdf",
        "byte_size": 32,
        "sha256": "a" * 64,
    }
    values.update(overrides)
    return Document(**values)


def test_names_are_nonblank_and_kb_name_is_unique_per_workspace(db_session: Session) -> None:
    _assert_constraint(db_session, Workspace(name="   "), "ck_workspaces_name_nonblank")

    first_workspace = Workspace(name="First")
    second_workspace = Workspace(name="Second")
    db_session.add_all([first_workspace, second_workspace])
    db_session.flush()

    _assert_constraint(
        db_session,
        KnowledgeBase(workspace_id=first_workspace.id, name="  "),
        "ck_knowledge_bases_name_nonblank",
    )

    db_session.add(KnowledgeBase(workspace_id=first_workspace.id, name="Manuals"))
    db_session.add(KnowledgeBase(workspace_id=second_workspace.id, name="Manuals"))
    db_session.flush()
    _assert_constraint(
        db_session,
        KnowledgeBase(workspace_id=first_workspace.id, name="Manuals"),
        "uq_knowledge_bases_workspace_id_name",
    )


def test_foreign_keys_reject_missing_parents(db_session: Session) -> None:
    _assert_constraint(
        db_session,
        KnowledgeBase(workspace_id=uuid4(), name="Orphan"),
        "fk_knowledge_bases_workspace_id_workspaces",
    )
    _assert_constraint(
        db_session,
        _document(uuid4()),
        "fk_documents_knowledge_base_id_knowledge_bases",
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"original_filename": "   "}, "ck_documents_original_filename_nonblank"),
        ({"storage_key": "  "}, "ck_documents_storage_key_nonblank"),
        ({"byte_size": 0}, "ck_documents_byte_size_positive"),
        ({"byte_size": -1}, "ck_documents_byte_size_positive"),
        ({"sha256": "A" * 64}, "ck_documents_sha256_lower_hex_64"),
        ({"sha256": "a" * 63}, "ck_documents_sha256_lower_hex_64"),
        ({"status": "unknown"}, "ck_documents_status_valid"),
    ],
)
def test_document_checks_reject_invalid_values(
    db_session: Session, overrides: dict[str, object], constraint: str
) -> None:
    knowledge_base = _knowledge_base(db_session)
    _assert_constraint(db_session, _document(knowledge_base.id, **overrides), constraint)


def test_document_defaults_and_timezone_aware_timestamps(db_session: Session) -> None:
    knowledge_base = _knowledge_base(db_session)
    document = _document(knowledge_base.id)
    db_session.add(document)
    db_session.flush()

    assert document.status == "uploaded"
    assert document.error_code is None
    assert document.created_at.tzinfo is not None
    assert document.updated_at.tzinfo is not None
