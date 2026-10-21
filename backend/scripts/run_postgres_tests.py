"""Run fixed PostgreSQL checks in eaap_test, without private settings or models.

Default: the existing local project container, through a newly generated role.
CI: an explicitly selected service container and its mapped loopback port.
--check-entry runs only the entry's safety tests, with all DB/network I/O blocked.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from scripts.check_workflow_listing_db import scram_verifier
from scripts.run_contract_tests import (
    ContractSuiteAudit, ResourceGuard, isolated_settings, prepare_environment,
)


ROLE_PATTERN = re.compile(r"eaap_check_[0-9a-f]{32}")
SCHEMA_PATTERN = re.compile(r"eaap_test_[0-9a-f]{32}")
DB_TEST_PATHS = tuple(BACKEND / "tests" / name for name in (
    "test_answer_workflow_listing_db.py", "test_readiness_db.py", "test_auth_upgrade_ci_db.py",
))
EXPECTED_NODES = frozenset({
    "test_answer_workflow_listing_db.py::" + name for name in (
        "test_db_empty_knowledge_base_returns_empty_list",
        "test_db_creation_time_then_uuid_desc_is_stable",
        "test_db_recent_update_does_not_promote_old_creation",
        "test_db_scope_filter_precedes_limit",
        "test_db_pagination_uses_stable_offset_and_empty_tail",
        "test_db_default_page_is_latest_twenty_creation_records",
        "test_db_owner_and_member_share_same_knowledge_base_list",
        "test_db_nonmember_cannot_list_or_read_detail",
        "test_db_wrong_workspace_parent_cannot_expose_another_knowledge_base",
        "test_db_missing_knowledge_base_is_not_an_empty_existing_list",
        "test_db_workflow_detail_cannot_cross_knowledge_base",
        "test_db_completed_summary_omits_saved_bodies_and_reads_change_nothing",
        "test_db_create_pending_persists_across_requests_without_execution",
        "test_db_later_detail_observes_new_state_after_old_list_snapshot",
    )
} | {
    "test_readiness_db.py::" + name for name in (
        "test_ready_http_with_real_database_and_read_only_timeouts",
        "test_real_query_timeout_becomes_unavailable",
        "test_real_authentication_failure_becomes_unavailable",
    )
} | {
    "test_auth_upgrade_ci_db.py::test_0009_to_0010_preserves_legacy_data_and_explicit_membership",
})


@dataclass(frozen=True)
class Target:
    container: str
    admin: str
    port: int


def local_target() -> Target:
    return Target("enterprise_ai_agent_platform-db-1", "eaap", 5432)


def ci_target(container: str, port: int) -> Target:
    if (not isinstance(container, str) or not re.fullmatch(r"[0-9a-f]{64}", container)
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError("Invalid CI service target")
    return Target(container, "eaap_ci_admin", port)


class DatabaseSuiteAudit(ContractSuiteAudit):
    def pytest_collection_modifyitems(self, items):
        import pytest

        nodes = [f"{Path(item.path).name}::{item.originalname or item.name}" for item in items]
        self.collected = len(items)
        if ({Path(item.path).resolve() for item in items} != set(DB_TEST_PATHS)
                or len(nodes) != len(EXPECTED_NODES) or set(nodes) != EXPECTED_NODES):
            raise pytest.UsageError("PostgreSQL suite must collect the exact fixed nodes")
        self.collection_valid = True


class EntrySuiteAudit(ContractSuiteAudit):
    def pytest_collection_modifyitems(self, items):
        import pytest

        self.collected = len(items)
        if {Path(item.path).resolve() for item in items} != {BACKEND / "tests/test_postgres_runner.py"}:
            raise pytest.UsageError("Entry checks must collect the fixed safety file")
        self.collection_valid = True


class PostgresGuard(ResourceGuard):
    """Allow libpq only to this run's test role; keep HTTP/socket guards in place."""

    def __init__(self, url):
        super().__init__()
        self.url = url

    def _check_url(self, url):
        if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
                or url.port != self.url.port or url.username != self.url.username
                or url.database != "eaap_test" or not url.password or url.query):
            self._reject("out-of-scope database connection")

    def _check_conninfo(self, conninfo, kwargs):
        from psycopg.conninfo import conninfo_to_dict

        # These are Python client controls, not libpq target parameters.
        client_options = {"autocommit", "prepare_threshold", "context", "row_factory", "cursor_factory"}
        allowed = {"host", "port", "dbname", "user", "password", "connect_timeout", "options"}
        if set(kwargs) - allowed - client_options:
            self._reject("out-of-scope database connection")
        try:
            parameters = conninfo_to_dict(conninfo, **{
                key: value for key, value in kwargs.items() if key not in client_options
            })
            valid = (not (set(parameters) - allowed)
                     and parameters.get("host") == "127.0.0.1"
                     and str(parameters.get("port")) == str(self.url.port)
                     and parameters.get("dbname") == "eaap_test"
                     and parameters.get("user") == self.url.username
                     and bool(parameters.get("password")))
        except Exception:
            valid = False
        if not valid:
            self._reject("out-of-scope database connection")

    def __enter__(self):
        import psycopg
        from sqlalchemy.engine import Engine

        engine_connect, engine_raw = Engine.connect, Engine.raw_connection
        connect, class_connect = psycopg.connect, psycopg.Connection.connect
        super().__enter__()

        def guarded_engine(original):
            def call(engine, *args, **kwargs):
                self._check_url(engine.url)
                return original(engine, *args, **kwargs)
            return call

        def guarded_connect(conninfo="", **kwargs):
            self._check_conninfo(conninfo, kwargs)
            return connect(conninfo, **kwargs, sslmode="disable", gssencmode="disable", passfile=os.devnull)

        def guarded_class_connect(_cls, conninfo="", **kwargs):
            self._check_conninfo(conninfo, kwargs)
            return class_connect(conninfo, **kwargs, sslmode="disable", gssencmode="disable", passfile=os.devnull)

        self._patches.enter_context(patch.object(Engine, "connect", guarded_engine(engine_connect)))
        self._patches.enter_context(patch.object(Engine, "raw_connection", guarded_engine(engine_raw)))
        self._patches.enter_context(patch.object(psycopg, "connect", guarded_connect))
        self._patches.enter_context(patch.object(psycopg.Connection, "connect", classmethod(guarded_class_connect)))
        return self


class Admin:
    def __init__(self, docker: str, target: Target):
        self.docker, self.target = docker, target

    def sql(self, statement: str) -> str:
        result = subprocess.run([
            self.docker, "exec", "-i", self.target.container,
            "env", "-i", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "PGPASSFILE=/dev/null", "PGSERVICEFILE=/dev/null",
            "psql", "-X", "-w", "-h", "/var/run/postgresql", "-p", "5432",
            "-U", self.target.admin, "-d", "eaap_test", "-v", "ON_ERROR_STOP=1", "-Atq",
        ], input="SET statement_timeout='10s'; SET lock_timeout='5s'; " + statement,
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        if result.returncode:
            raise RuntimeError("Test database administration was not confirmed")
        return result.stdout.strip()

    def confirm(self):
        if self.sql("SELECT current_database() || ':' || current_user;") != f"eaap_test:{self.target.admin}":
            raise RuntimeError("Actual database or management identity does not match")
        # Map validation prevents connecting the test role to a different local server.
        result = subprocess.run([self.docker, "port", self.target.container, "5432/tcp"],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        bindings = result.stdout.strip().splitlines()
        if result.returncode or not any(line in {
            f"127.0.0.1:{self.target.port}", f"0.0.0.0:{self.target.port}",
        } for line in bindings):
            raise RuntimeError("Service port mapping does not match")

    def ensure_vector_extension(self):
        # This fixed database prerequisite is retained locally; never move or drop it.
        namespace_query = (
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
            "WHERE e.extname='vector';"
        )
        namespace = self.sql(namespace_query)
        if namespace == "public":
            return
        if namespace:
            raise RuntimeError("Test vector extension must be in public; no changes were made")
        self.sql("CREATE EXTENSION vector WITH SCHEMA public;")
        if self.sql(namespace_query) != "public":
            raise RuntimeError("Test vector extension preparation was not confirmed")

    def create(self, role, password):
        if not ROLE_PATTERN.fullmatch(role):
            raise ValueError("Invalid temporary identity")
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.sql("BEGIN; "
                 f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                 f"NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 4 "
                 f"PASSWORD '{scram_verifier(password)}' VALID UNTIL '{expires}'; "
                 f'GRANT CONNECT, CREATE ON DATABASE eaap_test TO "{role}"; COMMIT;')

    def cleanup(self, role):
        if not ROLE_PATTERN.fullmatch(role):
            raise ValueError("Invalid cleanup identity")
        if self.sql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") == "0":
            return
        self.sql(f'ALTER ROLE "{role}" NOLOGIN;')
        self.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                 f"WHERE usename='{role}' AND datname='eaap_test' AND pid<>pg_backend_pid();")
        names = self.sql("SELECT n.nspname FROM pg_namespace n JOIN pg_roles r "
                         f"ON n.nspowner=r.oid WHERE r.rolname='{role}' ORDER BY n.nspname;")
        schemas = names.splitlines() if names else []
        if any(not SCHEMA_PATTERN.fullmatch(name) for name in schemas):
            raise RuntimeError("Unexpected owned schema; cleanup stopped")
        for schema in schemas:
            self.sql(f'DROP SCHEMA "{schema}" CASCADE;')
        self.sql(f'REVOKE CREATE, CONNECT ON DATABASE eaap_test FROM "{role}"; DROP ROLE "{role}";')
        if self.sql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") != "0":
            raise RuntimeError("Temporary identity cleanup was not confirmed")


def invoke_pytest(audit, paths):
    import pytest

    with TemporaryDirectory(prefix="eaap-postgres-") as temporary:
        return int(pytest.main([
            "-q", "-c", os.devnull, "--rootdir", str(BACKEND), "--noconftest",
            "--basetemp", str(Path(temporary) / "pytest"), "-p", "no:cacheprovider",
            "--tb=short", "--strict-config", "--strict-markers", *(str(path) for path in paths),
        ], plugins=[audit]))


def run_checks():
    prepare_environment()
    guard, audit = ResourceGuard(), EntrySuiteAudit()
    with isolated_settings(), guard:
        result = invoke_pytest(audit, (BACKEND / "tests/test_postgres_runner.py",))
    final = result if result or (audit.complete() and not guard.attempts) else 1
    print(f"Entry checks: pytest_exit={result} collected={audit.collected} passed={audit.passed} "
          f"skipped={audit.skipped} xfail={audit.xfailed} violations={len(guard.attempts)} final_exit={final}")
    return final


def run_database(docker, target):
    prepare_environment()
    # libpq must not inherit service/passfile/target overrides from the parent process.
    for name in tuple(os.environ):
        if name.upper().startswith("PG"):
            del os.environ[name]
    from sqlalchemy.engine import URL

    role, password = f"eaap_check_{uuid4().hex}", secrets.token_hex(32)
    url = URL.create("postgresql+psycopg", username=role, password=password,
                     host="127.0.0.1", port=target.port, database="eaap_test")
    admin, audit, output = Admin(docker, target), DatabaseSuiteAudit(), StringIO()
    guard = PostgresGuard(url)
    reserved, cleanup_ok, pytest_exit, final_exit = False, True, None, 1
    try:
        admin.confirm()
        admin.ensure_vector_extension()
        if admin.sql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") != "0":
            raise RuntimeError("Temporary identity already exists")
        # Reserve before dispatch: DDL may commit even when its reply is interrupted.
        reserved = True
        admin.create(role, password)
        os.environ["EAAP_TEST_DATABASE_URL"] = url.render_as_string(hide_password=False)
        print("Target confirmed: independent eaap_test, temporary test identity; private settings disabled.")
        with isolated_settings(), guard, redirect_stdout(output), redirect_stderr(output):
            pytest_exit = invoke_pytest(audit, DB_TEST_PATHS)
        final_exit = pytest_exit if pytest_exit or (audit.complete() and not guard.attempts) else 1
    except (Exception, KeyboardInterrupt) as failure:
        print(f"PostgreSQL checks did not complete ({type(failure).__name__}); connection details omitted.")
    finally:
        os.environ.pop("EAAP_TEST_DATABASE_URL", None)
        if reserved:
            try:
                admin.cleanup(role)
                print("Temporary identity and its test schemas: cleanup confirmed.")
            except Exception:
                cleanup_ok = False
                print(f"Cleanup unconfirmed for temporary identity {role}; do not remove unrelated objects.")
        if not cleanup_ok:
            final_exit = 1
    print(output.getvalue().replace(password, "[REDACTED]"), end="")
    print(f"PostgreSQL suite: pytest_exit={pytest_exit if pytest_exit is not None else 'not-run'} "
          f"collected={audit.collected} passed={audit.passed} skipped={audit.skipped} "
          f"xfail={audit.xfailed} violations={len(guard.attempts)} cleanup_ok={cleanup_ok} final_exit={final_exit}")
    return final_exit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-exe", default=shutil.which("docker") or "docker")
    parser.add_argument("--ci-container")
    parser.add_argument("--port", type=int)
    parser.add_argument("--check-entry", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.check_entry:
        if arguments.ci_container is not None or arguments.port is not None:
            parser.error("Entry checks cannot select a database")
        return run_checks()
    if (arguments.ci_container is None) != (arguments.port is None):
        parser.error("CI container and mapped port must be supplied together")
    try:
        target = (local_target() if arguments.ci_container is None
                  else ci_target(arguments.ci_container, arguments.port))
    except ValueError:
        parser.error("Invalid CI service target")
    return run_database(arguments.docker_exe, target)


if __name__ == "__main__":
    raise SystemExit(main())
