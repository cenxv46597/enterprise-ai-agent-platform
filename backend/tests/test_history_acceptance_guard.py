"""Pure fixtures for the temporary history browser's listener and DB boundary."""

from asyncio import BaseEventLoop
import os
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import psycopg
import pytest
import sqlalchemy
from sqlalchemy import Column, Integer, MetaData, String, Table, select, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.schema import CreateTable

from scripts.history_acceptance_guard import HistoryAcceptanceGuard
from scripts.run_contract_tests import ResourceAccessBlocked
from scripts import check_history_acceptance_entry as entry_checks
from scripts import serve_history_acceptance as acceptance


API_PORT = 18181
ROLE = "eaap_check_" + "a" * 32


def database_url(**changes):
    return URL.create(**({
        "drivername": "postgresql+psycopg", "username": ROLE,
        "password": "fixture-only-password", "host": "127.0.0.1",
        "port": 5432, "database": "eaap_test",
    } | changes))


class FixtureSocket:
    """Only supplies socket metadata; it never creates a system socket."""

    def __init__(self, local=("127.0.0.1", API_PORT), peer=("127.0.0.1", 49152), *, closed=False):
        self.local, self.peer, self.closed = local, peer, closed

    def getsockname(self):
        if self.closed:
            raise OSError("fixture-closed-socket")
        return self.local

    def getpeername(self):
        if self.closed or self.peer is None:
            raise OSError("fixture-unconnected-socket")
        return self.peer


def socket_dispatch(name):
    dispatched = Mock(return_value="fixture-dispatched")

    def operation(*args, **kwargs):
        return dispatched(*args, **kwargs)

    operation.__name__ = name
    return operation, dispatched


def complete_immediate(coroutine):
    """Drive an immediately completing fixture without creating an event loop."""
    try:
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        pytest.fail("Fixture unexpectedly required an event loop")
    finally:
        coroutine.close()


@pytest.mark.parametrize("changes", [
    {"database": "eaap"}, {"username": "eaap"}, {"username": "eaap_check_short"},
    {"username": "eaap_check_" + "A" * 32}, {"password": None}, {"password": ""},
    {"host": "localhost"}, {"host": "::1"}, {"host": "0.0.0.0"},
    {"host": "external.example.invalid"}, {"port": None}, {"port": 0}, {"port": 65536},
    {"drivername": "postgresql"}, {"query": {"service": "fixture-service"}},
    {"query": {"hostaddr": "127.0.0.1"}},
])
def test_constructor_rejects_other_databases_roles_and_implicit_connection_inputs(changes):
    with pytest.raises(ValueError, match="Invalid isolated browser target"):
        HistoryAcceptanceGuard(database_url(**changes), API_PORT)


@pytest.mark.parametrize("port", [None, 0, -1, 65536, True, 18181.0, "18181", 5432])
def test_browser_port_must_be_a_distinct_strict_integer(port):
    with pytest.raises(ValueError, match="Invalid isolated browser target"):
        HistoryAcceptanceGuard(database_url(), port)


def test_valid_constructor_keeps_one_explicit_database_and_browser_endpoint():
    url = database_url(port=15432)
    guard = HistoryAcceptanceGuard(url, API_PORT)
    assert guard.url == url and guard.api_port == API_PORT
    assert guard.attempts == []


@pytest.mark.parametrize("name,args,accepted", [
    ("bind", (("127.0.0.1", API_PORT),), False),
    ("listen", (128,), False),
    ("send", (b"fixture-response",), True),
    ("sendall", (b"fixture-response",), True),
    ("sendmsg", ([b"fixture-response"],), True),
])
def test_only_target_bind_listen_and_loopback_client_response_reach_dispatch(name, args, accepted):
    guard = HistoryAcceptanceGuard(database_url(), API_PORT)
    sock = FixtureSocket(peer=("127.0.0.1", 49152) if accepted else None)
    original, dispatched = socket_dispatch(name)
    guarded = guard._socket_operation(original, internal_send=name.startswith("send"))
    assert guarded(sock, *args) == "fixture-dispatched"
    dispatched.assert_called_once_with(sock, *args)
    assert guard.attempts == []


@pytest.mark.parametrize("name,args,sock", [
    ("bind", (("0.0.0.0", API_PORT),), FixtureSocket()),
    ("bind", (("localhost", API_PORT),), FixtureSocket()),
    ("bind", (("127.0.0.1", API_PORT + 1),), FixtureSocket()),
    ("bind", (("127.0.0.1", 0),), FixtureSocket()),
    ("listen", (128,), FixtureSocket(local=("127.0.0.1", API_PORT + 1))),
    ("listen", (128,), FixtureSocket(local=("0.0.0.0", API_PORT))),
    ("connect", (("127.0.0.1", API_PORT),), FixtureSocket()),
    ("connect_ex", (("127.0.0.1", API_PORT),), FixtureSocket()),
    ("sendto", (b"fixture", ("127.0.0.1", API_PORT)), FixtureSocket()),
    ("send", (b"fixture",), FixtureSocket(peer=("external.example.invalid", 49152))),
    ("sendall", (b"fixture",), FixtureSocket(peer=("::1", 49152))),
    ("sendmsg", ([b"fixture"],), FixtureSocket(local=("127.0.0.1", API_PORT + 1))),
    ("send", (b"fixture",), FixtureSocket(peer=None)),
    ("sendall", (b"fixture",), FixtureSocket(closed=True)),
])
def test_other_socket_operations_fail_before_dispatch_and_remain_auditable(name, args, sock):
    guard = HistoryAcceptanceGuard(database_url(), API_PORT)
    original, dispatched = socket_dispatch(name)
    guarded = guard._socket_operation(original, internal_send=name.startswith("send"))
    with pytest.raises(ResourceAccessBlocked, match="out-of-scope browser socket"):
        guarded(sock, *args)
    dispatched.assert_not_called()
    assert guard.attempts == ["out-of-scope browser socket"]


def test_wakeup_pair_exception_allows_its_send_but_never_an_unrelated_socket():
    guard = HistoryAcceptanceGuard(database_url(), API_PORT)
    wakeup = FixtureSocket(local=("127.0.0.1", 49153), peer=("127.0.0.1", 49154))
    guard._socketpairs.add(wakeup)
    original, dispatched = socket_dispatch("send")
    guarded = guard._socket_operation(original, internal_send=True)
    assert guarded(wakeup, b"wake") == "fixture-dispatched"
    with pytest.raises(ResourceAccessBlocked):
        guarded(FixtureSocket(local=("127.0.0.1", 49153)), b"fixture")
    dispatched.assert_called_once_with(wakeup, b"wake")
    assert len(guard.attempts) == 1


@pytest.mark.parametrize("host,port", [("localhost", API_PORT), ("0.0.0.0", API_PORT),
                                      ("external.example.invalid", 443), ("127.0.0.1", API_PORT + 1)])
def test_dns_exception_does_not_resolve_another_endpoint(host, port, monkeypatch):
    resolution = Mock(return_value=["fixture-address"])
    monkeypatch.setattr(socket, "getaddrinfo", resolution)
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        with pytest.raises(ResourceAccessBlocked, match="out-of-scope browser resolution"):
            socket.getaddrinfo(host, port)
    resolution.assert_not_called()
    assert guard.attempts == ["out-of-scope browser resolution"]


def test_exact_browser_address_can_be_resolved_without_enabling_other_networks(monkeypatch):
    resolution = Mock(return_value=["fixture-address"])
    monkeypatch.setattr(socket, "getaddrinfo", resolution)
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        assert socket.getaddrinfo("127.0.0.1", API_PORT) == ["fixture-address"]
    resolution.assert_called_once_with("127.0.0.1", API_PORT)
    assert guard.attempts == []


@pytest.mark.parametrize("host,port,supplied", [
    ("127.0.0.1", API_PORT, None),
    (None, None, FixtureSocket(peer=None)),
])
def test_async_server_dispatch_only_accepts_the_selected_loopback_listener(host, port, supplied, monkeypatch):
    dispatched = AsyncMock(return_value="fixture-server")
    monkeypatch.setattr(BaseEventLoop, "create_server", dispatched)
    loop, factory = object(), object()
    options = {"sock": supplied} if supplied is not None else {}
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        result = complete_immediate(BaseEventLoop.create_server(loop, factory, host, port, **options))
    assert result == "fixture-server"
    dispatched.assert_awaited_once_with(loop, factory, host, port, **options)
    assert guard.attempts == []


@pytest.mark.parametrize("host,port,supplied", [
    ("0.0.0.0", API_PORT, None), ("localhost", API_PORT, None),
    ("127.0.0.1", API_PORT + 1, None), (None, None, None),
    (None, None, FixtureSocket(local=("127.0.0.1", API_PORT + 1))),
    ("127.0.0.1", API_PORT, FixtureSocket(peer=None)),
])
def test_async_server_cannot_add_a_listener_or_override_a_supplied_socket(host, port, supplied, monkeypatch):
    dispatched = AsyncMock()
    monkeypatch.setattr(BaseEventLoop, "create_server", dispatched)
    options = {"sock": supplied} if supplied is not None else {}
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        with pytest.raises(ResourceAccessBlocked, match="out-of-scope browser listener"):
            complete_immediate(BaseEventLoop.create_server(object(), object(), host, port, **options))
    dispatched.assert_not_awaited()
    assert guard.attempts == ["out-of-scope browser listener"]


def test_inherited_database_guard_rejects_wrong_database_before_dispatch(monkeypatch):
    engine_dispatch, driver_dispatch = Mock(), Mock()
    monkeypatch.setattr(Engine, "connect", engine_dispatch)
    monkeypatch.setattr(psycopg, "connect", driver_dispatch)
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        with pytest.raises(ResourceAccessBlocked):
            Engine.connect(SimpleNamespace(url=database_url(database="eaap")))
        with pytest.raises(ResourceAccessBlocked):
            psycopg.connect(host="127.0.0.1", port="5432", dbname="eaap", user=ROLE, password="fixture-only-password")
    engine_dispatch.assert_not_called()
    driver_dispatch.assert_not_called()
    assert guard.attempts == ["out-of-scope database connection"] * 2


def test_allowed_database_dispatch_keeps_null_passfile_and_no_certificate_discovery(monkeypatch):
    dispatch = Mock(return_value="fixture-db-connection")
    monkeypatch.setattr(psycopg, "connect", dispatch)
    parameters = dict(host="127.0.0.1", port="5432", dbname="eaap_test", user=ROLE, password="fixture-only-password")
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        assert psycopg.connect(**parameters) == "fixture-db-connection"
    dispatch.assert_called_once_with("", **parameters, sslmode="disable", gssencmode="disable", passfile=os.devnull)
    assert guard.attempts == []


def test_http_and_async_connections_remain_blocked_before_dispatch(monkeypatch):
    http_dispatch, async_dispatch = Mock(), AsyncMock()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", http_dispatch)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(lambda _cls, *args, **kwargs: async_dispatch(*args, **kwargs)))
    with HistoryAcceptanceGuard(database_url(), API_PORT) as guard:
        with pytest.raises(ResourceAccessBlocked):
            httpx.HTTPTransport.handle_request(object(), httpx.Request("GET", "https://model.example.invalid"))
        with pytest.raises(ResourceAccessBlocked):
            complete_immediate(psycopg.AsyncConnection.connect("fixture-only-conninfo"))
        with pytest.raises(ResourceAccessBlocked):
            complete_immediate(BaseEventLoop.create_connection(object(), object(), "127.0.0.1", API_PORT))
    http_dispatch.assert_not_called()
    async_dispatch.assert_not_awaited()
    assert len(guard.attempts) == 3


WORKSPACE = UUID(int=1)
BASE_40, BASE_45 = UUID(int=2), UUID(int=3)
WORKFLOWS_40 = tuple(UUID(int=100 + index) for index in range(40))
WORKFLOWS_45 = tuple(UUID(int=200 + index) for index in range(45))
WORKSPACE_PATH = f"/api/v1/workspaces/{WORKSPACE}"
BASES_PATH = f"{WORKSPACE_PATH}/knowledge-bases"


@pytest.fixture
def route_policy():
    app = AsyncMock()
    counts = acceptance.AcceptanceCounts()
    policy = acceptance.ReadOnlyRoutes(
        app, WORKSPACE, {BASE_40: WORKFLOWS_40, BASE_45: WORKFLOWS_45}, counts,
        {"/", "/index.html", "/assets/fixture.js"},
    )
    return policy, app, counts


@pytest.mark.parametrize("path,offset", [
    ("/api/v1/workspaces", 0), (BASES_PATH, 0),
    *[(f"{BASES_PATH}/{base}/documents", 0) for base in (BASE_40, BASE_45)],
    *[(f"{BASES_PATH}/{base}/answer-workflows", offset)
      for base in (BASE_40, BASE_45) for offset in (0, 20, 40)],
])
def test_only_fixture_get_pages_with_exact_limit_and_offset_are_permitted(route_policy, path, offset):
    policy, _app, _counts = route_policy
    assert policy.permitted("GET", path, f"limit=20&offset={offset}".encode("ascii"))
    assert policy.permitted("GET", path, f"offset={offset}&limit=20".encode("ascii"))


@pytest.mark.parametrize("base,workflow", [
    (BASE_40, WORKFLOWS_40[0]), (BASE_40, WORKFLOWS_40[-1]),
    (BASE_45, WORKFLOWS_45[0]), (BASE_45, WORKFLOWS_45[-1]),
])
def test_workflow_detail_is_bound_to_its_fixture_knowledge_base(route_policy, base, workflow):
    policy, _app, _counts = route_policy
    path = f"{BASES_PATH}/{base}/answer-workflows/{workflow}"
    assert policy.permitted("GET", path, b"")
    assert not policy.permitted("GET", path, b"limit=20&offset=0")
    assert not policy.permitted("HEAD", path, b"")


@pytest.mark.parametrize("path", [
    "/api/v1/auth/web/session", WORKSPACE_PATH,
    f"{BASES_PATH}/{BASE_40}", f"{BASES_PATH}/{BASE_45}",
])
def test_auth_and_fixture_parent_reads_require_get_and_no_query(route_policy, path):
    policy, _app, _counts = route_policy
    assert policy.permitted("GET", path, b"")
    assert not policy.permitted("GET", path, b"unexpected=fixture")
    assert not policy.permitted("HEAD", path, b"")


@pytest.mark.parametrize("path", ["/api/v1/auth/web/login", "/api/v1/auth/web/logout"])
def test_only_cookie_login_and_logout_allow_post_without_query(route_policy, path):
    policy, _app, _counts = route_policy
    assert policy.permitted("POST", path, b"")
    assert not policy.permitted("POST", path, b"unexpected=fixture")
    assert not policy.permitted("GET", path, b"")
    assert not policy.permitted("HEAD", path, b"")


@pytest.mark.parametrize("query", [
    b"", b"limit=20", b"offset=0", b"limit=20&offset=0&offset=20",
    b"limit=20&limit=20", b"offset=0&offset=20", b"limit=20&offset=0&extra=fixture",
    b"limit=1&offset=0", b"limit=100&offset=0", b"limit=20&offset=-20",
    b"limit=20&offset=1", b"limit=20&offset=60", b"limit=20&offset=9007199254740980",
    b"limit=20&offset=20.0", b"limit=20&offset=00", b"limit=20&offset=true",
    b"limit=20&offset=", b"limit=20&offset", b"limit=20&offset=\xff",
])
def test_page_queries_reject_duplicates_extras_and_out_of_fixture_offsets(route_policy, query):
    policy, _app, _counts = route_policy
    for base in (BASE_40, BASE_45):
        assert not policy.permitted("GET", f"{BASES_PATH}/{base}/answer-workflows", query)


@pytest.mark.parametrize("path", [
    f"/api/v1/workspaces/{UUID(int=999)}/knowledge-bases/{BASE_40}/answer-workflows",
    f"{BASES_PATH}/{UUID(int=999)}/answer-workflows",
    f"{BASES_PATH}/{BASE_40}/answer-workflows/{UUID(int=999)}",
    f"{BASES_PATH}/{BASE_40}/answer-workflows/{WORKFLOWS_45[0]}",
    f"{BASES_PATH}/{BASE_45}/answer-workflows/{WORKFLOWS_40[0]}",
    f"{BASES_PATH}/{BASE_40}/answer-workflows/",
    f"{BASES_PATH}/{BASE_40}/answer-workflows/{WORKFLOWS_40[0]}/",
])
def test_other_resources_and_alias_paths_never_reach_the_real_app(route_policy, path):
    policy, _app, _counts = route_policy
    assert not policy.permitted("GET", path, b"")
    assert not policy.permitted("GET", path, b"limit=20&offset=0")


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/auth/register"), ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/workspaces"), ("POST", BASES_PATH),
    ("POST", f"{BASES_PATH}/{BASE_40}/documents"),
    ("POST", f"{BASES_PATH}/{BASE_40}/answer-workflows"),
    ("POST", f"{BASES_PATH}/{BASE_40}/answer-workflows/{WORKFLOWS_40[0]}/execute"),
    ("POST", f"{BASES_PATH}/{BASE_40}/search"),
    ("POST", f"{BASES_PATH}/{BASE_40}/answers"),
    ("POST", f"{BASES_PATH}/{BASE_40}/agent-runs"),
    ("POST", f"{BASES_PATH}/{BASE_40}/conversations"),
    ("PUT", f"{BASES_PATH}/{BASE_40}/answer-preferences"),
    ("DELETE", f"{BASES_PATH}/{BASE_40}/answer-workflows/{WORKFLOWS_40[0]}"),
    ("HEAD", "/api/v1/workspaces"),
    ("HEAD", f"{BASES_PATH}/{BASE_40}/answer-workflows"),
    ("OPTIONS", f"{BASES_PATH}/{BASE_40}/answer-workflows"),
])
def test_business_writes_model_routes_and_head_api_are_blocked(route_policy, method, path):
    policy, _app, _counts = route_policy
    assert not policy.permitted(method, path, b"")
    assert not policy.permitted(method, path, b"limit=20&offset=0")


@pytest.mark.parametrize("path", ["/", "/index.html", "/assets/fixture.js"])
def test_static_assets_only_allow_get_or_head_without_query(route_policy, path):
    policy, _app, _counts = route_policy
    assert policy.permitted("GET", path, b"")
    assert policy.permitted("HEAD", path, b"")
    assert not policy.permitted("GET", path, b"extra=fixture")
    assert not policy.permitted("POST", path, b"")
    assert not policy.permitted("GET", "/../index.html", b"")


def test_denied_business_post_returns_safe_response_without_dispatching_app(route_policy):
    policy, app, counts = route_policy
    send, receive = AsyncMock(), AsyncMock()
    scope = {"type": "http", "method": "POST", "path": f"{BASES_PATH}/{BASE_40}/answers",
             "query_string": b"", "http_version": "1.1", "headers": []}
    complete_immediate(policy(scope, receive, send))
    app.assert_not_awaited()
    receive.assert_not_awaited()
    messages = [call.args[0] for call in send.await_args_list]
    assert messages[0]["type"] == "http.response.start" and messages[0]["status"] == 405
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert messages[1]["type"] == "http.response.body"
    assert b"history reads only" in messages[1]["body"]
    assert str(BASE_40).encode("ascii") not in messages[1]["body"]
    assert counts.snapshot()["requests"] == 1 and counts.snapshot()["rejected_requests"] == 1


def test_allowed_history_page_dispatches_once_without_performing_a_request(route_policy):
    policy, app, counts = route_policy
    send, receive = AsyncMock(), AsyncMock()
    scope = {"type": "http", "method": "GET", "path": f"{BASES_PATH}/{BASE_45}/answer-workflows",
             "query_string": b"limit=20&offset=20", "http_version": "1.1", "headers": []}
    start = {"type": "http.response.start", "status": 200, "headers": []}
    body = {"type": "http.response.body", "body": b"[]"}

    async def respond(actual_scope, actual_receive, observed_send):
        assert actual_scope is scope and actual_receive is receive
        assert observed_send is not send
        await observed_send(start)
        await observed_send(body)

    app.side_effect = respond
    complete_immediate(policy(scope, receive, send))
    app.assert_awaited_once()
    assert app.await_args.args[:2] == (scope, receive)
    assert [call.args[0] for call in send.await_args_list] == [start, body]
    receive.assert_not_awaited()
    assert counts.covered({"history_45_20_200"})
    assert not policy.coverage_ok()
    assert counts.snapshot()["requests"] == 1
    assert counts.snapshot().get("rejected_requests", 0) == 0


def test_idle_or_partial_response_coverage_cannot_mark_browser_acceptance_complete(route_policy):
    policy, _app, counts = route_policy
    assert counts.snapshot() == {} and not policy.coverage_ok()
    for base in (BASE_40, BASE_45):
        for offset in (0, 20, 40):
            policy.response_seen("GET", f"{BASES_PATH}/{base}/answer-workflows",
                                 f"limit=20&offset={offset}".encode("ascii"), 200)
    policy.response_seen("GET", f"{BASES_PATH}/{BASE_45}/answer-workflows", b"limit=20&offset=20", 503)
    policy.response_seen("POST", "/api/v1/auth/web/login", b"", 200)
    policy.response_seen("POST", "/api/v1/auth/web/logout", b"", 204)
    assert not policy.coverage_ok()  # All pages alone still omit the required task detail.
    policy.response_seen("GET", f"{BASES_PATH}/{BASE_40}/answer-workflows/{WORKFLOWS_40[0]}", b"", 404)
    assert not policy.coverage_ok()
    policy.response_seen("GET", f"{BASES_PATH}/{BASE_40}/answer-workflows/{WORKFLOWS_40[0]}", b"", 200)
    assert policy.coverage_ok()


@pytest.mark.parametrize("arguments", [
    ["--check-entry", "--docker-exe", "fixture-docker"],
    ["--check-entry", "--port", "8811"],
    ["--check-entry", "--port", "5432"],
    ["--check-entry", "--max-seconds", "30"],
    ["--check-entry", "--port", "0"],
    ["--check-entry", "--max-seconds", "901"],
])
def test_check_entry_rejects_target_and_duration_selection_before_any_dispatch(arguments, monkeypatch, capsys):
    checks = Mock(side_effect=AssertionError("invalid options cannot run checks"))
    serve = Mock(side_effect=AssertionError("entry checks cannot start a server"))
    monkeypatch.setattr(entry_checks, "run_checks", checks)
    monkeypatch.setattr(acceptance, "run_acceptance", serve)
    with pytest.raises(SystemExit) as failure:
        acceptance.main(arguments)
    assert failure.value.code == 2
    checks.assert_not_called()
    serve.assert_not_called()
    capsys.readouterr()


def test_check_entry_dispatches_only_its_safety_checks_without_starting_a_server(monkeypatch):
    checks = Mock(return_value=7)
    serve = Mock(side_effect=AssertionError("entry checks cannot start a server"))
    monkeypatch.setattr(entry_checks, "run_checks", checks)
    monkeypatch.setattr(acceptance, "run_acceptance", serve)
    assert acceptance.main(["--check-entry"]) == 7
    checks.assert_called_once_with()
    serve.assert_not_called()


@pytest.fixture
def sql_fixture():
    return Table("fixture_history", MetaData(), Column("id", Integer, primary_key=True), Column("name", String))


def query_fixture(table, kind):
    if kind == "select":
        return select(table).where(table.c.id == 1)
    if kind == "share-lock":
        return select(table).where(table.c.id == 1).with_for_update(read=True)
    if kind == "read-cte":
        selected = select(table.c.id).cte("fixture_read")
        return select(selected.c.id)
    if kind == "insert":
        return table.insert().values(id=1, name="fixture")
    if kind == "update":
        return table.update().where(table.c.id == 1).values(name="fixture")
    if kind == "delete":
        return table.delete().where(table.c.id == 1)
    if kind == "ddl":
        return CreateTable(table)
    if kind == "raw-select":
        return text("SELECT id FROM fixture_history")
    if kind == "raw-delete":
        return text("DELETE FROM fixture_history")
    if kind == "text-filter":
        return select(table).where(text("id = 1"))
    if kind.endswith("-cte"):
        operation = kind.removesuffix("-cte")
        written = query_fixture(table, operation).returning(table.c.id).cte("fixture_write")
        return select(written.c.id)
    if kind == "attached-write":
        written = table.insert().values(id=1, name="fixture").cte("fixture_write")
        return select(table.c.id).add_cte(written)
    raise AssertionError("Unknown synthetic SQL fixture")


@pytest.mark.parametrize("kind", ["select", "share-lock", "read-cte"])
def test_structured_reads_and_production_share_locks_are_allowed(sql_fixture, kind):
    assert acceptance.is_read_query(query_fixture(sql_fixture, kind))


@pytest.mark.parametrize("kind", [
    "insert", "update", "delete", "ddl", "raw-select", "raw-delete", "text-filter",
    "insert-cte", "update-cte", "delete-cte", "attached-write",
])
def test_writes_raw_text_and_dml_hidden_under_select_are_rejected(sql_fixture, kind):
    assert not acceptance.is_read_query(query_fixture(sql_fixture, kind))


def test_browser_engine_without_sql_guard_is_rejected_before_engine_construction(monkeypatch):
    create = Mock(side_effect=AssertionError("unguarded engine must not be constructed"))
    monkeypatch.setattr(sqlalchemy, "create_engine", create)
    with pytest.raises(ValueError, match="require the query guard"):
        acceptance.owned_engine(database_url(), "eaap_test_" + "b" * 32, readonly=True)
    create.assert_not_called()


def capture_query_listener(monkeypatch):
    engine, listeners = object(), {}
    create = Mock(return_value=engine)
    monkeypatch.setattr(sqlalchemy, "create_engine", create)

    def register(target, name):
        assert target is engine

        def record(callback):
            listeners[name] = callback
            return callback

        return record

    monkeypatch.setattr(sqlalchemy.event, "listens_for", register)
    guard = HistoryAcceptanceGuard(database_url(), API_PORT)
    result = acceptance.owned_engine(
        database_url(), "eaap_test_" + "b" * 32, readonly=True, query_guard=guard,
    )
    assert result is engine
    options = create.call_args.kwargs["connect_args"]["options"]
    assert "-c default_transaction_read_only=off" in options
    assert "before_cursor_execute" in listeners
    return listeners["before_cursor_execute"], guard


@pytest.mark.parametrize("kind,permitted", [
    ("select", True), ("share-lock", True), ("update", False), ("ddl", False),
    ("raw-select", False), ("insert-cte", False), ("attached-write", False),
])
def test_browser_sql_listener_runs_before_dispatch_and_records_denied_writes(monkeypatch, sql_fixture, kind, permitted):
    listener, guard = capture_query_listener(monkeypatch)
    driver = Mock()
    context = SimpleNamespace(compiled=SimpleNamespace(statement=query_fixture(sql_fixture, kind)))

    def dispatch():
        listener(None, None, "fixture-compiled-sql", {}, context, False)
        driver("fixture-compiled-sql", {})

    if permitted:
        dispatch()
        driver.assert_called_once_with("fixture-compiled-sql", {})
        assert guard.attempts == []
    else:
        with pytest.raises(ResourceAccessBlocked, match="non-read browser SQL"):
            dispatch()
        driver.assert_not_called()
        assert guard.attempts == ["non-read browser SQL"]


@pytest.mark.parametrize("context", [SimpleNamespace(), SimpleNamespace(compiled=None),
                                      SimpleNamespace(compiled=SimpleNamespace(statement=None))])
def test_driver_sql_without_a_structured_statement_cannot_bypass_the_listener(monkeypatch, context):
    listener, guard = capture_query_listener(monkeypatch)
    with pytest.raises(ResourceAccessBlocked, match="non-read browser SQL"):
        listener(None, None, "fixture-raw-sql", {}, context, False)
    assert guard.attempts == ["non-read browser SQL"]
