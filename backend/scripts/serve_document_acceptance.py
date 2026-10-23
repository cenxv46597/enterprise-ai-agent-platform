"""Serve one disposable document-list browser acceptance session on loopback 8811.

Real Cookie authentication and membership use only a generated eaap_test identity
and owned schema. Fixtures contain metadata and one synthetic chunk, no files or
embeddings. Business writes and model routes never reach the production app.
"""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
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
from uuid import UUID, uuid4


BACKEND = Path(__file__).resolve().parents[1]
DIST = BACKEND.parent / "frontend" / "dist"
PORT = 8811
MAX_SECONDS = 900
TEMP_PREFIX = "eaap-document-acceptance-"
FIXTURE_PASSWORD = "DocumentBrowser-Disposable-Only"
CHUNK_CONTENT = "虚构浏览器验收段落：报销资料21。此处只展示合成文本，不代表真实制度或模型回答。"
MAX_BUFFER_BYTES = 256 * 1024
sys.path.insert(0, str(BACKEND))

from scripts.history_acceptance_guard import HistoryAcceptanceGuard
from scripts.run_contract_tests import isolated_settings, prepare_environment
from scripts.run_postgres_tests import Admin, ROLE_PATTERN, SCHEMA_PATTERN, local_target
from scripts.serve_history_acceptance import AcceptanceCounts, owned_engine, static_paths


@dataclass(frozen=True)
class FixtureScope:
    workspace_id: UUID
    main_kb_id: UUID
    special_kb_id: UUID
    empty_kb_id: UUID
    target_document_id: UUID
    target_chunk_id: UUID


def fixture_documents(scope, created):
    """Pure synthetic rows, ordered by creation time; never read a real file."""
    rows = []
    groups = (
        (scope.main_kb_id, [f"基础资料{index:02d}.txt" for index in range(1, 21)]
         + [f"报销资料{index:02d}.txt" for index in range(1, 46)]),
        (scope.special_kb_id, ["预算100%.txt", "预算1000.txt", "RISK_PLAN.MD", "riskXplan.md"]),
    )
    for kb_id, filenames in groups:
        for index, filename in enumerate(filenames):
            target = kb_id == scope.main_kb_id and index == 40
            rows.append(dict(
                id=scope.target_document_id if target else uuid4(), knowledge_base_id=kb_id,
                original_filename=filename, media_type="text/plain", byte_size=100,
                storage_key=f"synthetic-no-file/{uuid4().hex}", sha256="a" * 64,
                status="parsed", error_code=None, created_at=created + timedelta(seconds=index),
                updated_at=created + timedelta(seconds=index),
                chunk_size=1000 if target else None, chunk_overlap=0 if target else None,
                chunking_version="synthetic-v1" if target else None,
            ))
    return tuple(rows)


def fixture_chunk(scope):
    return dict(id=scope.target_chunk_id, document_id=scope.target_document_id, chunk_index=1,
                content=CHUNK_CONTENT, start_char=0, end_char=len(CHUNK_CONTENT), embedding=None)


def seed_fixture(engine, schema, username):
    from sqlalchemy.orm import Session
    from app.auth.security import hash_password
    from app.db.base import Base
    from app.db.models import Chunk, Document, KnowledgeBase, User, Workspace, WorkspaceMembership

    if not SCHEMA_PATTERN.fullmatch(schema) or not ROLE_PATTERN.fullmatch(engine.url.username or ""):
        raise ValueError("Invalid owned fixture schema or identity")
    models = (User, Workspace, WorkspaceMembership, KnowledgeBase, Document, Chunk)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{engine.url.username}"')
        # Only this owned seed transaction needs public.vector type resolution.
        # Production reads retain the helper's single-schema search_path.
        connection.exec_driver_sql(f'SET LOCAL search_path = "{schema}", public')
        Base.metadata.create_all(connection, tables=[model.__table__ for model in models])
    scope = FixtureScope(*(uuid4() for _ in range(6)))
    labels = (
        {"name": "文档查找验收65份", "count": 65},
        {"name": "字面字符验收4份", "count": 4},
        {"name": "空知识库验收0份", "count": 0},
    )
    with Session(engine) as session, session.begin():
        user_id = uuid4()
        session.add(User(id=user_id, username=username, password_hash=hash_password(FIXTURE_PASSWORD)))
        session.add(Workspace(id=scope.workspace_id, name="文档查找临时验收空间"))
        session.flush()
        session.add(WorkspaceMembership(workspace_id=scope.workspace_id, user_id=user_id, role="owner"))
        for identifier, label in zip((scope.main_kb_id, scope.special_kb_id, scope.empty_kb_id), labels):
            session.add(KnowledgeBase(id=identifier, workspace_id=scope.workspace_id, name=label["name"]))
        session.flush()
        session.add_all(Document(**row) for row in fixture_documents(scope, datetime.now(timezone.utc)))
        session.flush()
        session.add(Chunk(**fixture_chunk(scope)))
    return scope, labels


def database_snapshot(engine, schema):
    """Check the exact owned table inventory and every row, without logging bodies."""
    from sqlalchemy import Text, cast, column, select, table
    from app.db.models import Chunk, Document, KnowledgeBase, User, Workspace, WorkspaceMembership

    models = (User, Workspace, WorkspaceMembership, KnowledgeBase, Document, Chunk)
    inventory = table("tables", column("table_schema"), column("table_name"), column("table_type"),
                      schema="information_schema")
    with engine.connect() as connection:
        actual = tuple(connection.scalars(select(inventory.c.table_name).where(
            inventory.c.table_schema == schema, inventory.c.table_type == "BASE TABLE",
        ).order_by(inventory.c.table_name)))
        if actual != tuple(sorted(model.__tablename__ for model in models)):
            raise RuntimeError("Owned table inventory does not match the document fixture")
        return {
            model.__tablename__: tuple(dict(row) for row in connection.execute(select(*[
                cast(field, Text).label(field.key) if field.key == "embedding" else field
                for field in model.__table__.columns
            ]).order_by(*model.__table__.primary_key.columns)).mappings())
            for model in models
        }


def complete_fixture(snapshot, scope):
    expected = {"users": 1, "workspaces": 1, "workspace_memberships": 1,
                "knowledge_bases": 3, "documents": 69, "chunks": 1}
    if {name: len(rows) for name, rows in snapshot.items()} != expected:
        return False
    rows = snapshot["documents"]
    main = [row for row in rows if row["knowledge_base_id"] == scope.main_kb_id]
    special = [row for row in rows if row["knowledge_base_id"] == scope.special_kb_id]
    return (len(main) == 65 and len(special) == 4 and all(row["status"] == "parsed" for row in rows)
            and sum("报销" in row["original_filename"] for row in main) == 45
            and sum("基础" in row["original_filename"] for row in main) == 20
            and snapshot["chunks"][0]["document_id"] == scope.target_document_id
            and snapshot["chunks"][0]["embedding"] is None
            and snapshot["chunks"][0]["content"] == CHUNK_CONTENT)


class ReadOnlyRoutes:
    """Exact fixture routes/queries, with one outer 503 after a real successful read."""

    def __init__(self, app, scope, counts, paths):
        self.app, self.scope, self.counts = app, scope, counts
        self.static_paths = frozenset(paths)
        self._fault_used, self._fault_lock = False, Lock()
        workspace = f"/api/v1/workspaces/{scope.workspace_id}"
        bases = workspace + "/knowledge-bases"
        self.parent_pages = {"/api/v1/workspaces", bases}
        self.empty_query = {"/api/v1/auth/web/session", workspace}
        for identifier in (scope.main_kb_id, scope.special_kb_id, scope.empty_kb_id):
            self.empty_query.add(f"{bases}/{identifier}")
        self.main = f"{bases}/{scope.main_kb_id}/documents"
        self.special = f"{bases}/{scope.special_kb_id}/documents"
        self.empty = f"{bases}/{scope.empty_kb_id}/documents"
        self.detail = f"{self.main}/{scope.target_document_id}"
        self.chunks = self.detail + "/chunks"
        self.empty_query.update({self.detail, self.chunks})
        self.document_pages = {
            self.main: {None: {"0", "20", "40", "60"}, "报销": {"0", "20", "40"},
                        "基础": {"0", "20"}, "不存在": {"0"}},
            self.special: {None: {"0"}, "100%": {"0"}, "risk_plan": {"0"}},
            self.empty: {None: {"0"}},
        }
        self.coverage_required = {"login_200", "logout_204", "detail_200", "chunks_200", "empty_all_0_200",
                                  "main_报销_20_503", "main_不存在_0_200", "special_all_0_200",
                                  "special_100%_0_200", "special_risk_plan_0_200"}
        self.coverage_required.update(f"main_all_{offset}_200" for offset in (0, 20, 40, 60))
        self.coverage_required.update(f"main_报销_{offset}_200" for offset in (0, 20, 40))
        self.coverage_required.update(f"main_基础_{offset}_200" for offset in (0, 20))

    def page_parameters(self, path, query):
        try:
            pairs = parse_qsl(query.decode("ascii"), keep_blank_values=True, strict_parsing=True,
                              max_num_fields=3, encoding="utf-8", errors="strict")
        except (UnicodeError, ValueError):
            return None
        parameters = dict(pairs)
        if len(parameters) != len(pairs) or parameters.get("limit") != "20":
            return None
        keys = set(parameters)
        if path in self.parent_pages:
            return parameters if keys == {"limit", "offset"} and parameters["offset"] == "0" else None
        if path not in self.document_pages or keys not in ({"limit", "offset"}, {"limit", "offset", "filename_query"}):
            return None
        clue = parameters.get("filename_query")
        offsets = self.document_pages[path].get(clue)
        return parameters if offsets is not None and parameters["offset"] in offsets else None

    def permitted(self, method, path, query):
        if method == "POST":
            return not query and path in {"/api/v1/auth/web/login", "/api/v1/auth/web/logout"}
        if method == "GET":
            if path in self.empty_query:
                return not query
            if path in self.parent_pages or path in self.document_pages:
                return self.page_parameters(path, query) is not None
        return method in {"GET", "HEAD"} and not query and path in self.static_paths

    def response_seen(self, method, path, query, status):
        if not self.permitted(method, path, query):
            return
        tag = None
        if method == "POST":
            if path == "/api/v1/auth/web/login" and status == 200:
                tag = "login_200"
            elif path == "/api/v1/auth/web/logout" and status == 204:
                tag = "logout_204"
        elif method == "GET":
            if path in self.document_pages:
                parameters = self.page_parameters(path, query)
                group = {self.main: "main", self.special: "special", self.empty: "empty"}[path]
                tag = f"{group}_{parameters.get('filename_query', 'all')}_{parameters['offset']}_{status}"
            elif path in {self.detail, self.chunks} and status == 200:
                tag = "detail_200" if path == self.detail else "chunks_200"
        if tag is not None:
            self.counts.mark(tag)

    def coverage_ok(self):
        return self.counts.covered(self.coverage_required)

    def is_fault_target(self, method, path, query):
        return (method == "GET" and path == self.main
                and self.page_parameters(path, query) == {"limit": "20", "offset": "20", "filename_query": "报销"})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse

        method, path, query = scope["method"], scope["path"], scope.get("query_string", b"")
        self.counts.add("requests")
        if not self.permitted(method, path, query):
            self.counts.add("rejected_requests")
            await JSONResponse({"detail": "This acceptance session permits document reads only"},
                               status_code=405, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return

        async def observed_send(message):
            if message["type"] == "http.response.start":
                self.response_seen(method, path, query, message["status"])
            await send(message)

        if not self.is_fault_target(method, path, query):
            await self.app(scope, receive, observed_send)
            return
        messages, body_bytes = [], 0

        async def buffered_send(message):
            nonlocal body_bytes
            body_bytes += len(message.get("body", b""))
            if body_bytes > MAX_BUFFER_BYTES:
                self.counts.add("buffer_violations")
                raise RuntimeError("Fixture response exceeded its buffer budget")
            messages.append(dict(message))

        # The real app authenticates, checks membership and completes its SELECT
        # before this outer fixture chooses whether to replace the 200 response.
        await self.app(scope, receive, buffered_send)
        successful = bool(messages and messages[0].get("type") == "http.response.start"
                          and messages[0].get("status") == 200)
        with self._fault_lock:
            inject = successful and not self._fault_used
            if inject:
                self._fault_used = True
                self.counts.add("injected_failures")
        if inject:
            # New headers/body intentionally omit the successful response's trace
            # headers and content. Coverage records only what the browser sees.
            await JSONResponse({"detail": "临时验收：本页首次读取失败，请手动刷新"}, status_code=503,
                               headers={"Cache-Control": "no-store"})(scope, receive, observed_send)
        else:
            for message in messages:
                await observed_send(message)


def acceptance_app(engine, scope, counts, paths):
    from sqlalchemy.orm import Session
    from starlette.staticfiles import StaticFiles
    from app.api import answers, documents, reranking
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()
    app.state.web_origin = f"http://127.0.0.1:{PORT}"

    def read_only_session():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    def forbidden_provider():
        counts.add("provider_violations")
        raise RuntimeError("Models and file stores are unavailable in document acceptance")

    app.dependency_overrides[get_session] = read_only_session
    for provider in (documents.get_embedding_provider, documents.get_document_store,
                     answers.get_generation_provider, reranking.get_rerank_provider):
        app.dependency_overrides[provider] = forbidden_provider
    app.mount("/", StaticFiles(directory=str(DIST), html=True, follow_symlink=False), name="document-acceptance-static")
    return ReadOnlyRoutes(app, scope, counts, paths)


@contextmanager
def isolated_cookie_name():
    from app.auth import dependencies, web_session

    name = f"eaap_document_acceptance_{uuid4().hex}"
    with ExitStack() as stack:
        stack.enter_context(patch.object(web_session, "WEB_SESSION_COOKIE", name))
        stack.enter_context(patch.object(dependencies, "WEB_SESSION_COOKIE", name))
        yield name


def remove_owned_temporary(directory, parent):
    if (not directory.is_absolute() or directory.is_symlink() or directory.parent != parent
            or not directory.name.startswith(TEMP_PREFIX) or directory.resolve() != directory):
        raise RuntimeError("Temporary directory cleanup scope does not match")
    shutil.rmtree(directory)
    if directory.exists():
        raise RuntimeError("Temporary directory cleanup was not confirmed")


def result_is_success(completed, cleanup_ok, unchanged, coverage_ok, attempts, totals):
    return (completed and cleanup_ok and unchanged and coverage_ok and not attempts
            and totals.get("injected_failures") == 1 and not totals.get("provider_violations")
            and not totals.get("stop_errors") and not totals.get("buffer_violations")
            and not totals.get("timed_out"))


def stop_reason(requested, now, deadline):
    if now >= deadline:
        return "timed_out"
    return "requested" if requested else None


def run_acceptance(docker, max_seconds):
    if type(max_seconds) is not int or not 30 <= max_seconds <= MAX_SECONDS:
        raise ValueError("Duration must be 30..900 seconds")
    prepare_environment()
    for name in tuple(os.environ):
        if name.upper().startswith("PG"):
            del os.environ[name]
    from sqlalchemy.engine import URL
    import uvicorn

    paths, target = static_paths(DIST), local_target()
    role, schema, password = f"eaap_check_{uuid4().hex}", f"eaap_test_{uuid4().hex}", secrets.token_hex(32)
    if not ROLE_PATTERN.fullmatch(role):
        raise ValueError("Invalid temporary identity")
    username = f"document_browser_{uuid4().hex[:8]}"
    url = URL.create("postgresql+psycopg", username=role, password=password,
                     host="127.0.0.1", port=target.port, database="eaap_test")
    admin, guard, counts = Admin(docker, target), HistoryAcceptanceGuard(url, PORT), AcceptanceCounts()
    parent = Path(tempfile.gettempdir()).resolve(strict=True)
    temporary = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=parent)).resolve(strict=True)
    stop_file, stop_watcher = temporary / "stop.signal", Event()
    reserved, cleanup_ok, unchanged, coverage_ok, completed = False, True, False, False, False
    seed_engine = api_engine = server = watcher = None
    try:
        admin.confirm()
        admin.ensure_vector_extension()
        if admin.sql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") != "0":
            raise RuntimeError("Temporary identity already exists")
        if admin.sql(f"SELECT count(*) FROM pg_namespace WHERE nspname='{schema}';") != "0":
            raise RuntimeError("Temporary schema already exists")
        reserved = True
        admin.create(role, password)
        with isolated_settings(), guard, isolated_cookie_name():
            try:
                seed_engine = owned_engine(url, schema, readonly=False)
                scope, labels = seed_fixture(seed_engine, schema, username)
                before = database_snapshot(seed_engine, schema)
                if not complete_fixture(before, scope):
                    raise RuntimeError("Initial document fixture is incomplete")
                seed_engine.dispose()
                seed_engine = None
                api_engine = owned_engine(url, schema, readonly=True, query_guard=guard)
                app = acceptance_app(api_engine, scope, counts, paths)

                class AcceptanceServer(uvicorn.Server):
                    async def startup(self, sockets=None):
                        await super().startup(sockets=sockets)
                        if self.started:
                            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{PORT}",
                                              "username": username, "knowledge_bases": labels,
                                              "stop_file": str(stop_file)}, ensure_ascii=False), flush=True)

                server = AcceptanceServer(uvicorn.Config(
                    app, host="127.0.0.1", port=PORT, loop="asyncio", access_log=False,
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
                        reason = stop_reason(requested, monotonic(), deadline)
                        if reason is not None:
                            if reason == "timed_out":
                                counts.add("timed_out")
                            server.should_exit = True
                            return

                watcher = Thread(target=monitor_stop, name="document-acceptance-deadline", daemon=True)
                watcher.start()
                server.run()
                if not server.started:
                    raise RuntimeError("Browser acceptance listener did not start")
                unchanged = before == database_snapshot(api_engine, schema)
                coverage_ok = app.coverage_ok()
                if not unchanged:
                    raise RuntimeError("Owned fixture data changed during read-only acceptance")
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
        print(f"Document browser acceptance did not complete ({type(failure).__name__}); private details omitted.", flush=True)
    finally:
        if reserved:
            try:
                admin.cleanup(role)
            except Exception:
                cleanup_ok = False
                print(f"Cleanup unconfirmed for temporary identity {role}; keep unrelated objects unchanged.", flush=True)
        try:
            remove_owned_temporary(temporary, parent)
        except Exception:
            cleanup_ok = False
            print("Temporary stop-directory cleanup was not confirmed.", flush=True)
    totals = counts.snapshot()
    final = 0 if result_is_success(completed, cleanup_ok, unchanged, coverage_ok, guard.attempts, totals) else 1
    print(f"Document browser acceptance: requests={totals.get('requests', 0)} "
          f"rejected_requests={totals.get('rejected_requests', 0)} "
          f"injected_failures={totals.get('injected_failures', 0)} "
          f"provider_violations={totals.get('provider_violations', 0)} violations={len(guard.attempts)} "
          f"timed_out={totals.get('timed_out', 0)} "
          f"unchanged={str(unchanged).lower()} coverage_ok={str(coverage_ok).lower()} "
          f"cleanup_ok={str(cleanup_ok).lower()} final_exit={final}", flush=True)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-exe")
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    parser.add_argument("--check-entry", action="store_true")
    arguments = parser.parse_args(argv)
    if not 30 <= arguments.max_seconds <= MAX_SECONDS:
        parser.error("Duration must be 30..900 seconds; listener is fixed at 127.0.0.1:8811")
    if arguments.check_entry:
        if arguments.docker_exe is not None or arguments.max_seconds != MAX_SECONDS:
            parser.error("Entry checks cannot select a server or database target")
        from scripts.check_document_acceptance_entry import run_checks

        return run_checks()
    return run_acceptance(arguments.docker_exe or shutil.which("docker") or "docker", arguments.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
