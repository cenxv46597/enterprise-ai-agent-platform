"""Exercise the real Alembic 0009 -> 0010 command path in an owned test schema."""

import os
from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db import models  # noqa: F401


BACKEND = Path(__file__).resolve().parents[1]
OLD_REVISION = "20261012_0009"
NEW_REVISION = "20261013_0010"


def test_0009_to_0010_preserves_legacy_data_and_explicit_membership():
    value = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Requires the dedicated PostgreSQL entry and temporary eaap_test identity")
    url = make_url(value)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.database != "eaap_test" or not url.port or not 1 <= url.port <= 65535
            or url.query or not url.password
            or not re.fullmatch(r"eaap_check_[0-9a-f]{32}", url.username or "")):
        pytest.fail("Refusing a target outside the temporary test database", pytrace=False)

    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={
        "connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=5000",
    })
    schema = f"eaap_test_{uuid4().hex}"
    workspace_id, knowledge_base_id = uuid4(), uuid4()
    created = False

    def scope(connection):
        # Every new transaction sets its own scope; version records are explicit too.
        assert connection.scalar(text("SELECT current_database()")) == "eaap_test"
        assert connection.scalar(text("SELECT current_user")) == url.username
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')

    def revision(connection):
        return connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version'))

    def upgrade(connection, target):
        configuration = Config(str(BACKEND / "alembic.ini"))
        configuration.attributes["connection"] = connection
        configuration.attributes["version_table_schema"] = schema
        # The real env.py must use the supplied target, not find private settings
        # or create an unrelated engine during command dispatch.
        with patch("app.core.config.Settings", side_effect=AssertionError("Unexpected Settings lookup")), \
                patch("sqlalchemy.create_engine", side_effect=AssertionError("Unexpected engine creation")):
            command.upgrade(configuration, target)
        assert not connection.closed

    def legacy_rows(connection):
        return (
            connection.execute(text("SELECT id, name, created_at FROM workspaces ORDER BY id")).all(),
            connection.execute(text(
                "SELECT id, workspace_id, name, created_at, embedding_provider, "
                "embedding_model, embedding_dimensions FROM knowledge_bases ORDER BY id"
            )).all(),
        )

    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_database()")) == "eaap_test"
            assert connection.scalar(text("SELECT current_user")) == url.username
            connection.execute(CreateSchema(schema))
        created = True

        # Build an actual old schema through the migration graph, then commit old data.
        with engine.begin() as connection:
            scope(connection)
            upgrade(connection, OLD_REVISION)
            assert revision(connection) == OLD_REVISION
            old_tables = set(inspect(connection).get_table_names(schema=schema))
            assert "users" not in old_tables and "workspace_memberships" not in old_tables
            connection.execute(text(
                "INSERT INTO workspaces (id, name) VALUES (:id, 'CI虚构旧工作空间')"
            ), {"id": workspace_id})
            connection.execute(text(
                "INSERT INTO knowledge_bases (id, workspace_id, name) "
                "VALUES (:id, :workspace, 'CI虚构旧知识库')"
            ), {"id": knowledge_base_id, "workspace": workspace_id})
            before = legacy_rows(connection)

        # A fresh connection observes the committed old version, then performs the upgrade.
        with engine.begin() as connection:
            scope(connection)
            assert revision(connection) == OLD_REVISION
            assert legacy_rows(connection) == before
            upgrade(connection, NEW_REVISION)
            assert revision(connection) == NEW_REVISION

        # The result must survive that transaction and still match the current application.
        with engine.begin() as connection:
            scope(connection)
            assert revision(connection) == NEW_REVISION
            assert legacy_rows(connection) == before
            assert set(inspect(connection).get_table_names(schema=schema)) == (
                set(Base.metadata.tables) | {"alembic_version"}
            )
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM workspace_memberships")) == 0
            # Reflection sees the selected search_path as schema=None. Use that
            # alias for ORM comparison so Alembic excludes its own version table;
            # the real upgrades and revision assertions above remain explicitly scoped.
            migration_context = MigrationContext.configure(connection, opts={
                "target_metadata": Base.metadata,
            })
            assert compare_metadata(migration_context, Base.metadata) == []

            # Repeating the target upgrade is a no-op, never a second ownership assignment.
            upgrade(connection, NEW_REVISION)
            assert revision(connection) == NEW_REVISION
            assert legacy_rows(connection) == before
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM workspace_memberships")) == 0
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    assert connection.scalar(text("SELECT current_database()")) == "eaap_test"
                    owner = connection.scalar(text(
                        "SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON n.nspowner=r.oid "
                        "WHERE n.nspname=:schema"
                    ), {"schema": schema})
                    assert owner == url.username
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            engine.dispose()
