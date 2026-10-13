"""在独立数据库模式中验证身份迁移与旧工作空间保留。"""

import os
from pathlib import Path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.db.base import Base
from test_conversation_migration import load_migration

from app.db import models  # noqa: F401


VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


def test_auth_migration_preserves_existing_unowned_workspace_and_matches_models() -> None:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置独立 EAAP_TEST_DATABASE_URL 后运行身份迁移测试")
    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("身份迁移测试不能使用日常应用数据库")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("身份迁移测试需要独立 PostgreSQL 数据库")

    modules = [load_migration(path) for path in sorted(VERSIONS.glob("*.py"))]
    auth_positions = [
        index for index, module in enumerate(modules)
        if module.down_revision == "20261012_0009"
    ]
    assert len(auth_positions) == 1
    auth_position = auth_positions[0]
    assert auth_position == len(modules) - 1

    engine = create_engine(database_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    schema = f"eaap_auth_migration_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
            migration_context = MigrationContext.configure(
                connection, opts={"target_metadata": Base.metadata}
            )
            with Operations.context(migration_context):
                for module in modules[:auth_position]:
                    module.upgrade()

                workspace_id, knowledge_base_id = uuid4(), uuid4()
                connection.execute(
                    text("INSERT INTO workspaces (id, name) VALUES (:id, '旧工作空间')"),
                    {"id": workspace_id},
                )
                connection.execute(
                    text("INSERT INTO knowledge_bases (id, workspace_id, name) "
                         "VALUES (:id, :workspace_id, '旧知识库')"),
                    {"id": knowledge_base_id, "workspace_id": workspace_id},
                )

                modules[auth_position].upgrade()
                assert set(inspect(connection).get_table_names(schema=schema)) == set(
                    Base.metadata.tables
                )
                assert compare_metadata(migration_context, Base.metadata) == []
                assert connection.execute(
                    text("SELECT name FROM workspaces WHERE id=:id"),
                    {"id": workspace_id},
                ).scalar_one() == "旧工作空间"
                assert connection.execute(
                    text("SELECT name FROM knowledge_bases WHERE id=:id"),
                    {"id": knowledge_base_id},
                ).scalar_one() == "旧知识库"

                membership_tables = [
                    table for table in Base.metadata.tables.values()
                    if {"workspace_id", "user_id"}.issubset(table.columns.keys())
                ]
                assert len(membership_tables) == 1
                assert connection.scalar(
                    select(func.count()).select_from(membership_tables[0])
                ) == 0
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
