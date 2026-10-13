"""独立测试库临时模式验证工作流迁移与拒绝有历史数据的降级。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from test_conversation_migration import load_migration
from test_retrieval import retrieval_context


VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"
WORKFLOW_MIGRATION = VERSIONS / "20261012_0009_answer_workflows.py"


def test_workflow_migration_has_single_head_link() -> None:
    migration = load_migration(WORKFLOW_MIGRATION)
    assert migration.revision == "20261012_0009"
    assert migration.down_revision == "20261012_0008"
    assert migration.branch_labels is None
    assert migration.depends_on is None


@pytest.mark.parametrize("has_data", [False, True])
def test_downgrade_locks_both_tables_before_data_check_or_drop(monkeypatch, has_data) -> None:
    migration = load_migration(WORKFLOW_MIGRATION)
    actions = []

    class Bind:
        def execute(self, statement):
            actions.append(("lock", str(statement)))

        def scalar(self, statement):
            actions.append(("check", str(statement)))
            return has_data

    bind = Bind()
    get_bind = Mock(return_value=bind)
    fake_op = SimpleNamespace(
        get_bind=get_bind,
        drop_table=lambda table: actions.append(("drop_table", table)),
        drop_index=lambda index, table_name: actions.append(("drop_index", index, table_name)),
    )
    monkeypatch.setattr(migration, "op", fake_op)
    if has_data:
        with pytest.raises(RuntimeError, match="不能无损降级"):
            migration.downgrade()
    else:
        migration.downgrade()
    get_bind.assert_called_once_with()
    assert actions[:2] == [
        ("lock", "LOCK TABLE answer_workflows, answer_workflow_attempts IN ACCESS EXCLUSIVE MODE"),
        ("check", "SELECT EXISTS (SELECT 1 FROM answer_workflows) OR EXISTS (SELECT 1 FROM answer_workflow_attempts)"),
    ]
    assert actions[2:] == ([] if has_data else [
        ("drop_table", "answer_workflow_attempts"),
        ("drop_index", "ix_answer_workflows_knowledge_base_id", "answer_workflows"),
        ("drop_table", "answer_workflows"),
    ])


@pytest.fixture
def migrated_workflow_schema(retrieval_context):
    context = retrieval_context
    schema = f"eaap_workflow_migration_{uuid4().hex}"
    engine = context.engine.execution_options(schema_translate_map={None: schema})
    migrations = [load_migration(path) for path in sorted(VERSIONS.glob("*.py"))]
    workflow_migration = next(
        migration for migration in migrations if migration.revision == "20261012_0009"
    )
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
            migration_context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
            with Operations.context(migration_context):
                for migration in migrations:
                    migration.upgrade()
                workspace_id, knowledge_base_id = uuid4(), uuid4()
                connection.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, '工作流迁移团队')"),
                                   {"id": workspace_id})
                connection.execute(text("INSERT INTO knowledge_bases (id, workspace_id, name) "
                                        "VALUES (:id, :workspace_id, '原有知识库')"),
                                   {"id": knowledge_base_id, "workspace_id": workspace_id})
                yield SimpleNamespace(connection=connection, schema=schema, migration_context=migration_context,
                                      migration=workflow_migration, workspace_id=workspace_id,
                                      knowledge_base_id=knowledge_base_id)
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))


def insert_pending_workflow(context, *, request_id=None):
    workflow_id = uuid4()
    context.connection.execute(text("INSERT INTO answer_workflows "
        "(id, knowledge_base_id, creation_request_id, creation_digest, question) "
        "VALUES (:id, :knowledge_base_id, :request_id, :digest, '如何恢复服务？')"),
        {"id": workflow_id, "knowledge_base_id": context.knowledge_base_id,
         "request_id": request_id or uuid4(), "digest": "a" * 64})
    return workflow_id


def test_workflow_migration_matches_models_and_empty_downgrade_preserves_knowledge(migrated_workflow_schema):
    context = migrated_workflow_schema
    connection = context.connection
    assert set(inspect(connection).get_table_names(schema=context.schema)) == set(Base.metadata.tables)
    assert compare_metadata(context.migration_context, Base.metadata) == []
    assert connection.execute(text("SELECT name FROM knowledge_bases WHERE id=:id"),
                              {"id": context.knowledge_base_id}).scalar() == "原有知识库"
    context.migration.downgrade()
    tables = inspect(connection).get_table_names(schema=context.schema)
    assert "answer_workflows" not in tables
    assert "answer_workflow_attempts" not in tables
    assert connection.execute(text("SELECT count(*) FROM knowledge_bases")).scalar() == 1
    context.migration.upgrade()
    assert compare_metadata(context.migration_context, Base.metadata) == []


@pytest.mark.parametrize("attempt_status", [None, "running", "failed", "completed", "interrupted"])
def test_downgrade_refuses_pending_or_attempt_history_without_changing_rows(migrated_workflow_schema, attempt_status):
    context = migrated_workflow_schema
    connection = context.connection
    workflow_id = insert_pending_workflow(context)
    if attempt_status is not None:
        if attempt_status == "running":
            connection.execute(text("UPDATE answer_workflows SET status='running', attempt_count=1, "
                                    "active_request_id=:request_id, lease_expires_at=now() + interval '900 seconds' "
                                    "WHERE id=:id"), {"id": workflow_id, "request_id": uuid4()})
        elif attempt_status == "completed":
            connection.execute(text("UPDATE answer_workflows SET status='completed', attempt_count=1, "
                                    "next_step='done', checkpoint='{}'::jsonb, result='{}'::jsonb WHERE id=:id"),
                               {"id": workflow_id})
        else:
            connection.execute(text("UPDATE answer_workflows SET status='failed', attempt_count=1, "
                                    "error_code=:error WHERE id=:id"),
                               {"id": workflow_id, "error": "execution_interrupted" if attempt_status == "interrupted" else "generation_error"})
        connection.execute(text("INSERT INTO answer_workflow_attempts "
            "(id, workflow_id, request_id, request_digest, attempt_number, status, response, finished_at) "
            "VALUES (:id, :workflow, :request, :digest, 1, :status, "
            "CASE WHEN :running THEN NULL ELSE '{}'::jsonb END, "
            "CASE WHEN :running THEN NULL ELSE now() END)"),
            {"id": uuid4(), "workflow": workflow_id, "request": uuid4(), "digest": "b" * 64,
             "status": attempt_status, "running": attempt_status == "running"})
    before_workflows = connection.execute(text("SELECT * FROM answer_workflows ORDER BY id")).mappings().all()
    before_attempts = connection.execute(text("SELECT * FROM answer_workflow_attempts ORDER BY id")).mappings().all()
    with pytest.raises(RuntimeError, match="不能无损降级"):
        context.migration.downgrade()
    assert connection.execute(text("SELECT * FROM answer_workflows ORDER BY id")).mappings().all() == before_workflows
    assert connection.execute(text("SELECT * FROM answer_workflow_attempts ORDER BY id")).mappings().all() == before_attempts
    assert connection.execute(text("SELECT count(*) FROM knowledge_bases")).scalar() == 1
    assert "answer_workflows" in inspect(connection).get_table_names(schema=context.schema)
    assert "answer_workflow_attempts" in inspect(connection).get_table_names(schema=context.schema)


def test_workflow_database_rejects_invalid_states_and_duplicate_creation(migrated_workflow_schema):
    context = migrated_workflow_schema
    connection = context.connection
    request_id = uuid4()
    workflow_id = insert_pending_workflow(context, request_id=request_id)
    assert connection.execute(text("SELECT status, next_step, version, attempt_count, checkpoint "
                                   "FROM answer_workflows WHERE id=:id"), {"id": workflow_id}).one() == (
        "pending", "retrieve", 0, 0, None)
    invalid_updates = (
        "question=''", "question='   '", "question=repeat('x', 2001)",
        "creation_digest=repeat('A', 64)", "creation_digest='a'", "strategy='unknown'",
        "max_context_characters=0", "max_context_characters=12001", "version=-1", "version=33",
        "attempt_count=-1", "attempt_count=6", "status='unknown'", "next_step='unknown'",
        "status='running', attempt_count=1", "active_request_id=gen_random_uuid()", "lease_expires_at=now()",
        "checkpoint='{}'::jsonb", "next_step='generate'", "result='{}'::jsonb",
        "status='completed', attempt_count=1", "error_code='generation_error'",
        "status='failed', attempt_count=1", "attempt_count=1",
    )
    for update in invalid_updates:
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text(f"UPDATE answer_workflows SET {update} WHERE id=:id"), {"id": workflow_id})
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_pending_workflow(context, request_id=request_id)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE answer_workflows SET knowledge_base_id=:missing WHERE id=:id"),
                           {"id": workflow_id, "missing": uuid4()})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("DELETE FROM knowledge_bases WHERE id=:id"), {"id": context.knowledge_base_id})


def test_attempt_database_rejects_invalid_receipts_duplicates_and_parent_deletion(migrated_workflow_schema):
    context = migrated_workflow_schema
    connection = context.connection
    workflow_id = insert_pending_workflow(context)
    attempt_id, request_id = uuid4(), uuid4()
    parameters = {"id": attempt_id, "workflow": workflow_id, "request": request_id, "digest": "c" * 64}
    insert = text("INSERT INTO answer_workflow_attempts (id, workflow_id, request_id, request_digest, attempt_number) "
                  "VALUES (:id, :workflow, :request, :digest, :number)")
    connection.execute(insert, {**parameters, "number": 1})
    assert connection.execute(text("SELECT status, response, finished_at FROM answer_workflow_attempts WHERE id=:id"),
                              {"id": attempt_id}).one() == ("running", None, None)
    for update in (
        "attempt_number=0", "attempt_number=6", "request_digest=repeat('C',64)", "request_digest='c'",
        "status='unknown'", "status='completed'", "status='failed'", "status='interrupted'",
        "response='{}'::jsonb", "finished_at=now()", "status='completed', response='{}'::jsonb",
        "status='failed', finished_at=now()",
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text(f"UPDATE answer_workflow_attempts SET {update} WHERE id=:id"), {"id": attempt_id})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(insert, {**parameters, "id": uuid4(), "number": 2})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(insert, {**parameters, "id": uuid4(), "request": uuid4(), "number": 1})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(insert, {**parameters, "id": uuid4(), "workflow": uuid4(), "number": 2})
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("DELETE FROM answer_workflows WHERE id=:id"), {"id": workflow_id})
