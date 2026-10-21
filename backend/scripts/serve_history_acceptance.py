"""Serve one disposable, read-only workflow-history browser acceptance fixture.

Only the independent eaap_test database and a generated identity/schema are used.
The public synthetic fixture password is intentionally not printed by this entry.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import parse_qsl
from unittest.mock import patch
from uuid import uuid4


BACKEND = Path(__file__).resolve().parents[1]
DIST = BACKEND.parent / "frontend" / "dist"
DEFAULT_PORT = 8810
DEFAULT_MAX_SECONDS = 900
TEMP_PREFIX = "eaap-history-acceptance-"
FIXTURE_PASSWORD = "HistoryBrowser-Disposable-Only"
sys.path.insert(0, str(BACKEND))

from scripts.run_contract_tests import isolated_settings, prepare_environment
from scripts.run_postgres_tests import Admin, ROLE_PATTERN, SCHEMA_PATTERN, local_target
from scripts.history_acceptance_guard import HistoryAcceptanceGuard


class AcceptanceCounts:
    def __init__(self):
        self._lock = Lock()
        self.values = Counter()
        self.seen = set()

    def add(self, name):
        with self._lock:
            self.values[name] += 1

    def snapshot(self):
        with self._lock:
            return dict(self.values)

    def mark(self, name):
        with self._lock:
            self.seen.add(name)

    def covered(self, required):
        with self._lock:
            return required <= self.seen


class ReadOnlyRoutes:
    """Keep the real app behind a fixture-specific HTTP allowlist."""

    def __init__(self, app, workspace_id, workflows_by_kb, counts, static_paths):
        self.app, self.counts = app, counts
        self.empty_query = {
            "/api/v1/auth/web/session",
            f"/api/v1/workspaces/{workspace_id}",
        }
        self.pages = {"/api/v1/workspaces": {0}}
        self.history_pages, self.forty_details = {}, set()
        self.coverage_required = {"login_200", "logout_204", "history_45_20_503", "detail_40_200"}
        base = f"/api/v1/workspaces/{workspace_id}/knowledge-bases"
        self.pages[base] = {0}
        for kb_id, workflow_ids in workflows_by_kb.items():
            kb_path = f"{base}/{kb_id}"
            self.empty_query.add(kb_path)
            self.pages[f"{kb_path}/documents"] = {0}
            history = f"{kb_path}/answer-workflows"
            self.pages[history] = {0, 20, 40}
            self.history_pages[history] = len(workflow_ids)
            details = {f"{history}/{item}" for item in workflow_ids}
            self.empty_query.update(details)
            if len(workflow_ids) == 40:
                self.forty_details.update(details)
            self.coverage_required.update(f"history_{len(workflow_ids)}_{offset}_200" for offset in (0, 20, 40))
        self.static_paths = frozenset(static_paths)

    def coverage_ok(self):
        # This proves response-path coverage; the browser's visible behavior is checked separately.
        return self.counts.covered(self.coverage_required)

    def response_seen(self, method, path, query, status):
        if method == "POST":
            if path == "/api/v1/auth/web/login" and status == 200:
                self.counts.mark("login_200")
            elif path == "/api/v1/auth/web/logout" and status == 204:
                self.counts.mark("logout_204")
        elif method == "GET":
            if path in self.history_pages:
                offset = dict(parse_qsl(query.decode("ascii")))["offset"]
                self.counts.mark(f"history_{self.history_pages[path]}_{offset}_{status}")
            elif path in self.forty_details and status == 200:
                self.counts.mark("detail_40_200")

    def permitted(self, method, path, query):
        if method == "POST":
            return not query and path in {"/api/v1/auth/web/login", "/api/v1/auth/web/logout"}
        if method == "GET":
            if path in self.empty_query:
                return not query
            if path in self.pages:
                try:
                    pairs = parse_qsl(query.decode("ascii"), keep_blank_values=True,
                                      strict_parsing=True, max_num_fields=2)
                except (UnicodeError, ValueError):
                    return False
                return (len(pairs) == 2 and dict(pairs).get("limit") == "20"
                        and dict(pairs).get("offset") in {str(n) for n in self.pages[path]}
                        and {key for key, _ in pairs} == {"limit", "offset"})
        return method in {"GET", "HEAD"} and not query and path in self.static_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        self.counts.add("requests")
        if self.permitted(scope["method"], scope["path"], scope.get("query_string", b"")):
            async def observed_send(message):
                if message["type"] == "http.response.start":
                    self.response_seen(scope["method"], scope["path"], scope.get("query_string", b""), message["status"])
                await send(message)

            await self.app(scope, receive, observed_send)
            return
        self.counts.add("rejected_requests")
        from starlette.responses import JSONResponse

        await JSONResponse({"detail": "This acceptance session permits history reads only"},
                           status_code=405, headers={"Cache-Control": "no-store"})(scope, receive, send)


def static_paths(directory):
    root = directory.resolve(strict=True)
    if not (root / "index.html").is_file():
        raise RuntimeError("Build the current frontend before browser acceptance")
    paths = {"/"}
    for file in root.rglob("*"):
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            raise RuntimeError("Frontend assets must remain inside the current dist directory")
        if file.is_file():
            paths.add("/" + file.relative_to(root).as_posix())
    return paths


def is_read_query(statement):
    from sqlalchemy.sql import visitors
    from sqlalchemy.sql.elements import TextClause

    return bool(getattr(statement, "is_select", False)) and all(
        not getattr(node, "is_dml", False) and not isinstance(node, TextClause)
        for node in visitors.iterate(statement)
    )


def owned_engine(url, schema, *, readonly, query_guard=None):
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool

    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("Invalid isolated schema")
    if readonly and query_guard is None:
        raise ValueError("Browser read connections require the query guard")
    # The real GET scope query uses FOR SHARE, which PostgreSQL rejects in a
    # read-only transaction. Permit its locks and reject non-SELECT statements
    # before dispatch instead of changing the production query for this fixture.
    mode = "off"
    engine = create_engine(
        url, poolclass=NullPool, hide_parameters=True,
        connect_args={"connect_timeout": 5, "options": (
            f"-c search_path={schema} -c default_transaction_read_only={mode} "
            "-c statement_timeout=15000 -c lock_timeout=5000"
        )}, execution_options={"schema_translate_map": {None: schema}},
    )

    @event.listens_for(engine, "connect")
    def confirm_connection(connection, _record):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user, "
                               "current_setting('search_path'), current_setting('default_transaction_read_only')")
                if cursor.fetchone() != ("eaap_test", url.username, schema, mode):
                    raise RuntimeError("Actual isolated database scope does not match")
        finally:
            connection.rollback()

    if readonly:
        @event.listens_for(engine, "before_cursor_execute")
        def require_read_query(_connection, _cursor, _sql, _parameters, context, _many):
            compiled = getattr(context, "compiled", None)
            if not is_read_query(getattr(compiled, "statement", None)):
                query_guard._reject("non-read browser SQL")

    return engine


def workflow_snapshot(engine):
    from sqlalchemy import func, select
    from app.db.models import AnswerWorkflow, AnswerWorkflowAttempt

    with engine.connect() as connection:
        records = connection.execute(select(AnswerWorkflow.__table__).order_by(AnswerWorkflow.id)).all()
        rows = tuple(tuple(row) for row in records)
        pending = sum(row._mapping["status"] == "pending" and row._mapping["next_step"] == "retrieve"
                      and row._mapping["version"] == 0 and row._mapping["attempt_count"] == 0
                      for row in records)
        attempts = connection.scalar(select(func.count()).select_from(AnswerWorkflowAttempt))
    return rows, attempts, pending


def seed_fixture(engine, schema, username):
    from sqlalchemy.orm import Session
    from app.auth.security import hash_password
    from app.db.base import Base
    from app.db.models import (
        AnswerWorkflow, AnswerWorkflowAttempt, Document, KnowledgeBase,
        User, Workspace, WorkspaceMembership,
    )

    tables = [model.__table__ for model in (
        User, Workspace, WorkspaceMembership, KnowledgeBase, Document, AnswerWorkflow, AnswerWorkflowAttempt,
    )]
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{engine.url.username}"')
        Base.metadata.create_all(connection, tables=tables)
    user_id, workspace_id = uuid4(), uuid4()
    workflows_by_kb, kb_labels = {}, []
    now = datetime.now(timezone.utc)
    with Session(engine) as session, session.begin():
        session.add(User(id=user_id, username=username, password_hash=hash_password(FIXTURE_PASSWORD)))
        session.add(Workspace(id=workspace_id, name="历史翻页临时验收空间"))
        session.flush()
        session.add(WorkspaceMembership(workspace_id=workspace_id, user_id=user_id, role="owner"))
        for amount in (40, 45):
            kb_id = uuid4()
            label = f"历史翻页验收 {amount} 条"
            session.add(KnowledgeBase(id=kb_id, workspace_id=workspace_id, name=label))
            session.flush()
            ids = []
            for index in range(amount):
                workflow_id = uuid4()
                ids.append(workflow_id)
                created = now - timedelta(seconds=index)
                session.add(AnswerWorkflow(
                    id=workflow_id, knowledge_base_id=kb_id, creation_request_id=uuid4(),
                    creation_digest="a" * 64, question=f"临时历史记录 {amount}-{index + 1:02d}",
                    strategy="vector", max_context_characters=12000, status="pending", next_step="retrieve",
                    version=0, attempt_count=0, created_at=created, updated_at=created,
                ))
            workflows_by_kb[kb_id] = tuple(ids)
            kb_labels.append({"name": label, "count": amount})
    return workspace_id, workflows_by_kb, kb_labels


def acceptance_app(engine, workspace_id, workflows_by_kb, counts, port, paths):
    from fastapi import HTTPException
    from sqlalchemy.orm import Session
    from starlette.staticfiles import StaticFiles
    from app.api import answer_workflows, answers, documents, reranking
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()
    app.state.web_origin = f"http://127.0.0.1:{port}"

    def read_only_session():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    def forbidden_provider():
        counts.add("provider_violations")
        raise RuntimeError("Model execution is unavailable in browser acceptance")

    app.dependency_overrides[get_session] = read_only_session
    for provider in (documents.get_embedding_provider, answers.get_generation_provider, reranking.get_rerank_provider):
        app.dependency_overrides[provider] = forbidden_provider
    app.mount("/", StaticFiles(directory=str(DIST), html=True, follow_symlink=False), name="acceptance-static")
    fault_kb = next(kb_id for kb_id, ids in workflows_by_kb.items() if len(ids) == 45)
    original, fault_lock = answer_workflows.list_workflows, Lock()
    fault_used = False

    def list_with_one_failure(session, requested_workspace, requested_kb, limit, offset):
        nonlocal fault_used
        if requested_workspace == workspace_id and requested_kb == fault_kb and offset == 20:
            # This service hook is reached only after the real authentication/member dependencies.
            with fault_lock:
                if not fault_used:
                    fault_used = True
                    counts.add("injected_failures")
                    raise HTTPException(503, "临时验收：本页首次读取失败，请手动重试")
        return original(session, requested_workspace, requested_kb, limit, offset)

    wrapped = ReadOnlyRoutes(app, workspace_id, workflows_by_kb, counts, paths)
    return wrapped, patch.object(answer_workflows, "list_workflows", list_with_one_failure)


def remove_owned_temporary(directory, parent):
    # Only the unique directory created by this invocation is eligible for removal.
    if (not directory.is_absolute() or directory.is_symlink() or directory.parent != parent
            or not directory.name.startswith(TEMP_PREFIX) or directory.resolve() != directory):
        raise RuntimeError("Temporary directory cleanup scope does not match")
    shutil.rmtree(directory)


def run_acceptance(docker, port, max_seconds):
    prepare_environment()
    for name in tuple(os.environ):
        if name.upper().startswith("PG"):
            del os.environ[name]
    from sqlalchemy.engine import URL
    import uvicorn

    paths = static_paths(DIST)
    target = local_target()
    if port == target.port:
        raise ValueError("Browser and database ports must differ")
    role, schema, password = f"eaap_check_{uuid4().hex}", f"eaap_test_{uuid4().hex}", secrets.token_hex(32)
    if not ROLE_PATTERN.fullmatch(role):
        raise ValueError("Invalid temporary identity")
    username = f"history_browser_{uuid4().hex[:8]}"
    url = URL.create("postgresql+psycopg", username=role, password=password,
                     host="127.0.0.1", port=target.port, database="eaap_test")
    admin, guard, counts = Admin(docker, target), HistoryAcceptanceGuard(url, port), AcceptanceCounts()
    temporary_parent = Path(tempfile.gettempdir()).resolve(strict=True)
    temporary = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=temporary_parent)).resolve(strict=True)
    stop_file, stop_watcher = temporary / "stop.signal", Event()
    reserved, cleanup_ok, unchanged, coverage_ok, completed = False, True, False, False, False
    seed_engine = api_engine = server = watcher = None
    try:
        admin.confirm()
        if admin.sql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") != "0":
            raise RuntimeError("Temporary identity already exists")
        if admin.sql(f"SELECT count(*) FROM pg_namespace WHERE nspname='{schema}';") != "0":
            raise RuntimeError("Temporary schema already exists")
        # Reserve before dispatch because successful DDL can lose its reply.
        reserved = True
        admin.create(role, password)
        with isolated_settings(), guard:
            try:
                seed_engine = owned_engine(url, schema, readonly=False)
                workspace_id, workflows_by_kb, labels = seed_fixture(seed_engine, schema, username)
                before = workflow_snapshot(seed_engine)
                if len(before[0]) != 85 or before[1] != 0 or before[2] != 85:
                    raise RuntimeError("Initial history fixture is incomplete")
                seed_engine.dispose()
                seed_engine = None
                api_engine = owned_engine(url, schema, readonly=True, query_guard=guard)
                app, fault_patch = acceptance_app(api_engine, workspace_id, workflows_by_kb, counts, port, paths)

                class AcceptanceServer(uvicorn.Server):
                    async def startup(self, sockets=None):
                        await super().startup(sockets=sockets)
                        if self.started:
                            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{port}",
                                              "username": username, "knowledge_bases": labels,
                                              "stop_file": str(stop_file)}, ensure_ascii=False), flush=True)

                server = AcceptanceServer(uvicorn.Config(
                    app, host="127.0.0.1", port=port, loop="asyncio", access_log=False,
                    log_config=None, log_level="critical", timeout_graceful_shutdown=5,
                ))
                deadline = monotonic() + max_seconds

                def monitor_stop():
                    while not stop_watcher.wait(0.25):
                        try:
                            requested = stop_file.exists()
                        except OSError:
                            counts.add("stop_errors")
                            server.should_exit = True
                            return
                        if monotonic() >= deadline or requested:
                            server.should_exit = True
                            return

                watcher = Thread(target=monitor_stop, name="history-acceptance-deadline", daemon=True)
                watcher.start()
                with fault_patch:
                    server.run()
                if not server.started:
                    raise RuntimeError("Browser acceptance listener did not start")
                after = workflow_snapshot(api_engine)
                unchanged = before == after
                coverage_ok = app.coverage_ok()
                if not unchanged:
                    raise RuntimeError("History fixture changed during read-only acceptance")
                completed = True
            finally:
                if server is not None:
                    server.should_exit = True
                stop_watcher.set()
                if watcher is not None:
                    watcher.join(timeout=2)
                for engine in (seed_engine, api_engine):
                    if engine is not None:
                        engine.dispose()
    except (Exception, KeyboardInterrupt, SystemExit) as failure:
        completed = False
        print(f"Browser acceptance did not complete ({type(failure).__name__}); private details omitted.", flush=True)
    finally:
        if reserved:
            try:
                admin.cleanup(role)
            except Exception:
                cleanup_ok = False
                print(f"Cleanup unconfirmed for temporary identity {role}; keep unrelated objects unchanged.", flush=True)
        try:
            remove_owned_temporary(temporary, temporary_parent)
        except Exception:
            cleanup_ok = False
            print("Temporary stop-directory cleanup was not confirmed.", flush=True)
    totals = counts.snapshot()
    final = (0 if completed and cleanup_ok and unchanged and coverage_ok and not guard.attempts
             and totals.get("injected_failures") == 1 and not totals.get("provider_violations")
             and not totals.get("stop_errors") else 1)
    print(f"Browser acceptance: requests={totals.get('requests', 0)} "
          f"rejected_requests={totals.get('rejected_requests', 0)} "
          f"injected_failures={totals.get('injected_failures', 0)} "
          f"provider_violations={totals.get('provider_violations', 0)} violations={len(guard.attempts)} "
          f"unchanged={str(unchanged).lower()} coverage_ok={str(coverage_ok).lower()} "
          f"cleanup_ok={str(cleanup_ok).lower()} final_exit={final}", flush=True)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-exe")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--check-entry", action="store_true")
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535 or not 30 <= arguments.max_seconds <= 900:
        parser.error("Port must be 1..65535 and duration must be 30..900 seconds")
    if arguments.check_entry:
        if (arguments.docker_exe is not None or arguments.port != DEFAULT_PORT
                or arguments.max_seconds != DEFAULT_MAX_SECONDS):
            parser.error("Entry checks cannot select a server or database target")
        from scripts.check_history_acceptance_entry import run_checks

        return run_checks()
    return run_acceptance(arguments.docker_exe or shutil.which("docker") or "docker",
                          arguments.port, arguments.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
