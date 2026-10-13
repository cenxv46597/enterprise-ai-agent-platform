"""独立测试库的临时模式验证完整迁移、旧数据保留与新增表约束。"""

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from test_retrieval import retrieval_context


def load_migration(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_schema_migrations_preserve_knowledge_data_and_match_models(retrieval_context):
    context = retrieval_context
    schema = f"eaap_migration_{uuid4().hex}"
    engine = context.engine.execution_options(schema_translate_map={None: schema})
    paths = sorted((Path(__file__).parents[1] / "alembic" / "versions").glob("*.py"))
    modules = [load_migration(path) for path in paths]
    by_revision = {module.revision: module for module in modules}
    conversation_migration = by_revision["20261011_0005"]
    execution_migration = by_revision["20261011_0006"]
    billing_migration = by_revision["20261011_0007"]
    preference_migration = by_revision["20261012_0008"]
    workflow_migration = by_revision["20261012_0009"]
    auth_migration = by_revision["20261013_0010"]
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
            migration_context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
            with Operations.context(migration_context):
                for migration in modules:
                    if migration.revision == preference_migration.revision:
                        break
                    migration.upgrade()
                workspace, knowledge_base = uuid4(), uuid4()
                connection.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, '迁移保留团队')"), {"id": workspace})
                connection.execute(text("INSERT INTO knowledge_bases (id, workspace_id, name) VALUES (:id, :workspace, '原知识库')"),
                                   {"id": knowledge_base, "workspace": workspace})
                conversation = uuid4()
                connection.execute(text("INSERT INTO conversations (id, knowledge_base_id) VALUES (:id, :kb)"),
                                   {"id": conversation, "kb": knowledge_base})
                old_turn = uuid4()
                connection.execute(text("INSERT INTO conversation_turns "
                    "(id, conversation_id, request_id, request_digest, version, content, reply, response) "
                    "VALUES (:id, :conversation, :request, :digest, 1, '原消息', '原回复', '{}'::jsonb)"),
                    {"id": old_turn, "conversation": conversation, "request": uuid4(), "digest": "a" * 64})
                preference_migration.upgrade()
                workflow_migration.upgrade()
                auth_migration.upgrade()
                assert set(inspect(connection).get_table_names(schema=schema)) == set(Base.metadata.tables)
                assert compare_metadata(migration_context, Base.metadata) == []
                assert connection.execute(text("SELECT name FROM knowledge_bases WHERE id = :id"), {"id": knowledge_base}).scalar() == "原知识库"
                assert connection.execute(text("SELECT mode, content, reply FROM conversation_turns WHERE id=:id"),
                                          {"id": old_turn}).one() == ("continue", "原消息", "原回复")
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE conversation_turns SET mode='unknown' WHERE id=:id"), {"id": old_turn})
                # 新增身份表与工作流表为空时可降级，再继续验证历史迁移。
                auth_migration.downgrade()
                assert "users" not in inspect(connection).get_table_names(schema=schema)
                assert "workspace_memberships" not in inspect(connection).get_table_names(schema=schema)
                workflow_migration.downgrade()
                assert "answer_workflows" not in inspect(connection).get_table_names(schema=schema)
                assert "answer_workflow_attempts" not in inspect(connection).get_table_names(schema=schema)
                # 新增表有任何版本行都拒绝降级，已清除偏好的空值行也受保护。
                for detail in ("concise", None):
                    temporary = connection.begin_nested()
                    connection.execute(text("INSERT INTO answer_preferences (knowledge_base_id, version, answer_detail) "
                                            "VALUES (:id, 1, :detail)"), {"id": knowledge_base, "detail": detail})
                    with pytest.raises(RuntimeError, match="不能无损降级"):
                        preference_migration.downgrade()
                    assert connection.execute(text("SELECT count(*) FROM answer_preferences")).scalar() == 1
                    temporary.rollback()
                # 正常降级空偏好表后，继续验证已有迁移保护；不依赖文件列表的相对位置。
                preference_migration.downgrade()
                assert "answer_preferences" not in inspect(connection).get_table_names(schema=schema)
                # 执行模式继续有效；0006 的历史保护不能因新增0007而漏测。
                temporary = connection.begin_nested()
                connection.execute(text("UPDATE conversation_turns SET mode='execute' WHERE id=:id"), {"id": old_turn})
                with pytest.raises(RuntimeError, match="不能无损降级"):
                    execution_migration.downgrade()
                assert connection.execute(text("SELECT mode FROM conversation_turns WHERE id=:id"), {"id": old_turn}).scalar() == "execute"
                temporary.rollback()
                # 当前完成状态与已清除任务的计费历史，均不能无损降级为旧业务契约。
                for statement in (
                    "UPDATE conversations SET status='completed', task='{}'::jsonb WHERE id=:id",
                    "UPDATE conversations SET status='waiting_for_user', task='{\"billing_plan\":{}}'::jsonb WHERE id=:id",
                    "UPDATE conversation_turns SET response='{\"billing_result\":{}}'::jsonb WHERE conversation_id=:id",
                    "UPDATE conversation_turns SET response='{\"task\":{\"billing_plan\":{}}}'::jsonb WHERE conversation_id=:id",
                ):
                    temporary = connection.begin_nested()
                    connection.execute(text(statement), {"id": conversation})
                    with pytest.raises(RuntimeError, match="不能无损降级"):
                        billing_migration.downgrade()
                    temporary.rollback()
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE conversations SET status = 'waiting_for_user' WHERE id = :id"),
                                       {"id": conversation})
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE conversations SET version = -1 WHERE id = :id"), {"id": conversation})
                billing_migration.downgrade()
                assert "conversations" in inspect(connection).get_table_names(schema=schema)
                execution_migration.downgrade()
                assert "conversations" in inspect(connection).get_table_names(schema=schema)
                conversation_migration.downgrade()
                assert "conversations" not in inspect(connection).get_table_names(schema=schema)
                assert connection.execute(text("SELECT count(*) FROM knowledge_bases")).scalar() == 1
                conversation_migration.upgrade()
                execution_migration.upgrade()
                billing_migration.upgrade()
                preference_migration.upgrade()
                workflow_migration.upgrade()
                auth_migration.upgrade()
                assert compare_metadata(migration_context, Base.metadata) == []
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
