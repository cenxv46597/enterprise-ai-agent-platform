"""No-network/DB safety fixtures for the disposable document browser entry."""

from datetime import datetime, timezone
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode
from uuid import UUID

import pytest

from scripts import check_document_acceptance_entry as entry
from scripts import serve_document_acceptance as acceptance
from test_history_acceptance_guard import complete_immediate


SCOPE = acceptance.FixtureScope(*(UUID(int=index) for index in range(1, 7)))
STATIC = {"/", "/index.html", "/assets/fixture.js"}


def query(offset=0, clue=None, **changes):
    values = {"limit": "20", "offset": str(offset)}
    if clue is not None:
        values["filename_query"] = clue
    values.update(changes)
    return urlencode(values).encode("ascii")


@pytest.fixture
def policy():
    return acceptance.ReadOnlyRoutes(AsyncMock(), SCOPE, acceptance.AcceptanceCounts(), STATIC)


def required_responses(policy):
    rows = [
        ("login_200", "POST", "/api/v1/auth/web/login", b"", 200),
        ("logout_204", "POST", "/api/v1/auth/web/logout", b"", 204),
        ("detail_200", "GET", policy.detail, b"", 200),
        ("chunks_200", "GET", policy.chunks, b"", 200),
        ("empty_all_0_200", "GET", policy.empty, query(), 200),
        ("special_all_0_200", "GET", policy.special, query(), 200),
        ("special_100%_0_200", "GET", policy.special, query(clue="100%"), 200),
        ("special_risk_plan_0_200", "GET", policy.special, query(clue="risk_plan"), 200),
        ("main_不存在_0_200", "GET", policy.main, query(clue="不存在"), 200),
        ("main_报销_20_503", "GET", policy.main, query(20, "报销"), 503),
    ]
    rows.extend((f"main_all_{offset}_200", "GET", policy.main, query(offset), 200)
                for offset in (0, 20, 40, 60))
    rows.extend((f"main_报销_{offset}_200", "GET", policy.main, query(offset, "报销"), 200)
                for offset in (0, 20, 40))
    rows.extend((f"main_基础_{offset}_200", "GET", policy.main, query(offset, "基础"), 200)
                for offset in (0, 20))
    return rows


@pytest.mark.parametrize("omitted", range(19))
def test_every_one_of_nineteen_response_tags_is_required(policy, omitted):
    rows = required_responses(policy)
    assert len(rows) == 19 and {row[0] for row in rows} == policy.coverage_required
    for index, (_tag, method, path, parameters, status) in enumerate(rows):
        if index != omitted:
            assert policy.permitted(method, path, parameters)
            policy.response_seen(method, path, parameters, status)
    assert not policy.coverage_ok()
    _tag, method, path, parameters, status = rows[omitted]
    policy.response_seen(method, path, parameters, status)
    assert policy.coverage_ok()


def test_only_fixture_pages_conditions_and_offsets_are_permitted(policy):
    for _tag, method, path, parameters, _status in required_responses(policy):
        assert policy.permitted(method, path, parameters)
    for path in policy.parent_pages:
        assert policy.permitted("GET", path, query())
        assert not policy.permitted("GET", path, query(20))
        assert not policy.permitted("GET", path, query(clue="报销"))
    for path in policy.empty_query:
        assert policy.permitted("GET", path, b"")
        assert not policy.permitted("GET", path, query())


@pytest.mark.parametrize("parameters", [
    b"", b"limit=20", b"offset=0", b"limit=20&offset", b"limit=20&offset=00",
    b"limit=20&offset=-20", b"limit=20&offset=1", b"limit=20&offset=80",
    b"limit=1&offset=0", b"limit=100&offset=0", b"limit=20&offset=0&limit=20",
    b"limit=20&offset=0&offset=20", b"limit=20&offset=0&filename_query=",
    b"limit=20&offset=0&filename_query=%20", b"limit=20&offset=0&filename_query=%00",
    b"limit=20&offset=0&filename_query=%FF", b"limit=20&offset=0&filename_query=\xff",
    b"limit=20&offset=0&filename_query=report", b"limit=20&offset=0&filename_query=all",
    b"limit=20&offset=0&Filename_query=100%25", b"limit=20&offset=0&filename_query=100%2525",
    b"limit=20&offset=0&status=parsed", b"limit=20&offset=0&extra=value",
    b"limit=20&offset=0&filename_query=100%25&filename_query=100%25",
    b"limit=20&offset=0&filename_query=100%25&extra=value",
    b"limit=20&offset=0&filename_query=risk_plan%26offset%3D20",
    query(clue=" 报销"), query(clue="报销 "), query(clue="采购"), query(clue="Risk_Plan"),
    query(clue="😀" * 256), "limit=20&offset=0&filename_query=报销".encode("utf-8"),
])
def test_invalid_queries_never_expand_any_document_page(policy, parameters):
    for path in (policy.main, policy.special, policy.empty):
        assert not policy.permitted("GET", path, parameters)


@pytest.mark.parametrize("which,offset,clue", [
    ("main", 60, "报销"), ("main", 40, "基础"), ("main", 20, "不存在"),
    ("main", 0, "100%"), ("main", 0, "risk_plan"), ("special", 0, "报销"),
    ("special", 20, "100%"), ("special", 20, None), ("empty", 20, None), ("empty", 0, "不存在"),
])
def test_query_conditions_and_pages_are_bound_to_their_fixture_knowledge_base(policy, which, offset, clue):
    assert not policy.permitted("GET", getattr(policy, which), query(offset, clue))


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
def test_api_methods_other_than_exact_get_or_cookie_posts_are_denied(policy, method):
    for path in (policy.main, policy.detail, policy.chunks):
        assert not policy.permitted(method, path, b"")
        assert not policy.permitted(method, path, query(20, "报销"))


def test_unknown_resources_writes_aliases_and_model_routes_are_denied(policy):
    base = policy.main.rsplit("/", 1)[0]
    for path in (policy.main + "/", policy.detail + "/", policy.chunks + f"/{SCOPE.target_chunk_id}",
                 policy.main + f"/{UUID(int=999)}", base + "/search", base + "/answers",
                 base + "/agent-runs", base + "/conversations", base + "/answer-workflows"):
        for method in ("GET", "POST"):
            assert not policy.permitted(method, path, b"")
            assert not policy.permitted(method, path, query())
    foreign = policy.main.replace(str(SCOPE.main_kb_id), str(SCOPE.special_kb_id))
    assert not policy.permitted("GET", f"{foreign}/{SCOPE.target_document_id}", b"")


def test_auth_and_static_routes_do_not_accept_query_parameters_or_other_methods(policy):
    for path in ("/api/v1/auth/web/login", "/api/v1/auth/web/logout"):
        assert policy.permitted("POST", path, b"")
        assert not policy.permitted("POST", path, query())
        assert not policy.permitted("GET", path, b"")
    for path in STATIC:
        assert policy.permitted("GET", path, b"") and policy.permitted("HEAD", path, b"")
        assert not policy.permitted("GET", path, query())
        assert not policy.permitted("POST", path, b"")
    assert not policy.permitted("GET", "/../index.html", b"")


def scope_for(path, parameters=b"", method="GET"):
    return {"type": "http", "method": method, "path": path, "query_string": parameters,
            "http_version": "1.1", "headers": []}


def dispatch(policy, path, parameters=b"", method="GET"):
    send, receive = AsyncMock(), AsyncMock()
    complete_immediate(policy(scope_for(path, parameters, method), receive, send))
    return [call.args[0] for call in send.await_args_list]


def make_response(policy, status=200):
    messages = [
        {"type": "http.response.start", "status": status,
         "headers": [(b"x-request-id", b"successful-trace-id"), (b"content-type", b"application/json")]},
        {"type": "http.response.body", "body": b"fixture-success-body-part-1", "more_body": True},
        {"type": "http.response.body", "body": b"fixture-success-body-part-2", "more_body": False},
    ]

    async def respond(_scope, _receive, send):
        for message in messages:
            await send(message)

    policy.app.side_effect = respond
    return messages


@pytest.mark.parametrize("status", [401, 404, 422, 503])
def test_non_200_target_responses_do_not_consume_failure_or_success_coverage(policy, status):
    original = make_response(policy, status)
    assert dispatch(policy, policy.main, query(20, "报销")) == original
    assert policy.counts.snapshot().get("injected_failures", 0) == 0
    assert not policy.counts.covered({"main_报销_20_200"})
    original = make_response(policy)
    messages = dispatch(policy, policy.main, query(20, "报销"))
    assert messages[0]["status"] == 503
    assert policy.counts.snapshot()["injected_failures"] == 1


def test_one_successful_target_is_replaced_without_leaking_success_body_or_trace_headers(policy):
    original = make_response(policy)
    first = dispatch(policy, policy.main, query(20, "报销"))
    assert first[0]["status"] == 503
    assert (b"cache-control", b"no-store") in first[0]["headers"]
    assert all(b"successful-trace-id" not in value for _name, value in first[0]["headers"])
    assert all(b"fixture-success-body" not in message.get("body", b"") for message in first)
    assert policy.counts.covered({"main_报销_20_503"})
    assert not policy.counts.covered({"main_报销_20_200"})
    assert dispatch(policy, policy.main, query(20, "报销")) == original
    assert policy.counts.covered({"main_报销_20_503", "main_报销_20_200"})
    assert policy.counts.snapshot()["injected_failures"] == 1
    assert policy.app.await_count == 2


@pytest.mark.parametrize("which,offset,clue", [
    ("main", 0, "报销"), ("main", 40, "报销"), ("main", 20, None),
    ("main", 20, "基础"), ("special", 0, "risk_plan"),
])
def test_other_successful_reads_forward_all_messages_without_using_the_failure(policy, which, offset, clue):
    original = make_response(policy)
    assert dispatch(policy, getattr(policy, which), query(offset, clue)) == original
    assert policy.counts.snapshot().get("injected_failures", 0) == 0


def test_disallowed_query_is_rejected_before_app_dispatch_or_coverage(policy):
    result = dispatch(policy, policy.main, query(20, "采购"))
    assert result[0]["status"] == 405
    policy.app.assert_not_awaited()
    assert policy.counts.snapshot() == {"requests": 1, "rejected_requests": 1}
    assert not policy.coverage_ok() and not policy.counts.seen


def test_buffer_limit_failure_is_auditable_before_any_buffered_body_reaches_the_browser(policy, monkeypatch):
    make_response(policy)
    monkeypatch.setattr(acceptance, "MAX_BUFFER_BYTES", 1)
    send = AsyncMock()
    with pytest.raises(RuntimeError, match="buffer budget"):
        complete_immediate(policy(scope_for(policy.main, query(20, "报销")), AsyncMock(), send))
    send.assert_not_awaited()
    assert policy.counts.snapshot()["buffer_violations"] == 1
    assert not policy.counts.snapshot().get("injected_failures")


def fixture_snapshot():
    rows = acceptance.fixture_documents(SCOPE, datetime(2030, 1, 1, tzinfo=timezone.utc))
    return {"users": ({"id": 1},), "workspaces": ({"id": SCOPE.workspace_id},),
            "workspace_memberships": ({"id": 1},), "knowledge_bases": ({}, {}, {}),
            "documents": rows, "chunks": (acceptance.fixture_chunk(SCOPE),)}


def test_pure_seed_contract_is_69_parsed_documents_with_one_synthetic_chunk_and_exact_page_shapes():
    from app.api.documents import ChunkRead, DocumentRead

    snapshot = fixture_snapshot()
    assert acceptance.complete_fixture(snapshot, SCOPE)
    rows = snapshot["documents"]
    main = [row for row in rows if row["knowledge_base_id"] == SCOPE.main_kb_id]
    special = [row for row in rows if row["knowledge_base_id"] == SCOPE.special_kb_id]
    assert len(main) == 65 and len(special) == 4 and len(rows) == 69
    assert [row["original_filename"] for row in main[:20]] == [f"基础资料{index:02d}.txt" for index in range(1, 21)]
    assert [row["original_filename"] for row in main[20:]] == [f"报销资料{index:02d}.txt" for index in range(1, 46)]
    assert [row["created_at"] for row in main] == sorted({row["created_at"] for row in main})
    assert [len(main[offset:offset + 20]) for offset in (0, 20, 40, 60)] == [20, 20, 20, 5]
    matched = [row for row in main if "报销" in row["original_filename"]]
    assert [len(matched[offset:offset + 20]) for offset in (0, 20, 40)] == [20, 20, 5]
    assert matched[20]["id"] == SCOPE.target_document_id
    assert [row["original_filename"] for row in special] == ["预算100%.txt", "预算1000.txt", "RISK_PLAN.MD", "riskXplan.md"]
    for row in rows:
        assert DocumentRead.model_validate(SimpleNamespace(**row)).status == "parsed"
    chunk = ChunkRead.model_validate(snapshot["chunks"][0])
    assert chunk.document_id == SCOPE.target_document_id and chunk.end_char == len(acceptance.CHUNK_CONTENT)
    assert snapshot["chunks"][0]["embedding"] is None
    assert len({row["id"] for row in rows}) == 69


@pytest.mark.parametrize("changed", ["table", "document", "state", "scope", "chunk", "embedding"])
def test_incomplete_seed_cannot_pass_the_full_fixture_gate(changed):
    snapshot = fixture_snapshot()
    if changed == "table":
        snapshot["extra_table"] = ()
    elif changed == "document":
        snapshot["documents"] = snapshot["documents"][:-1]
    elif changed == "state":
        snapshot["documents"][0]["status"] = "ready"
    elif changed == "scope":
        snapshot["documents"][0]["knowledge_base_id"] = SCOPE.empty_kb_id
    elif changed == "chunk":
        snapshot["chunks"][0]["document_id"] = UUID(int=999)
    else:
        snapshot["chunks"][0]["embedding"] = "[0.25]"
    assert not acceptance.complete_fixture(snapshot, SCOPE)


def test_cookie_name_is_unique_shared_by_real_login_and_dependencies_and_restored():
    from app.auth import dependencies, web_session

    original = web_session.WEB_SESSION_COOKIE, dependencies.WEB_SESSION_COOKIE
    with acceptance.isolated_cookie_name() as name:
        assert name.startswith("eaap_document_acceptance_") and name not in original
        assert web_session.WEB_SESSION_COOKIE == dependencies.WEB_SESSION_COOKIE == name
        with acceptance.isolated_cookie_name() as second:
            assert second != name and web_session.WEB_SESSION_COOKIE == dependencies.WEB_SESSION_COOKIE == second
        assert web_session.WEB_SESSION_COOKIE == dependencies.WEB_SESSION_COOKIE == name
    assert (web_session.WEB_SESSION_COOKIE, dependencies.WEB_SESSION_COOKIE) == original


def test_cookie_patch_also_restores_defaults_after_a_failure():
    from app.auth import dependencies, web_session

    original = web_session.WEB_SESSION_COOKIE, dependencies.WEB_SESSION_COOKIE
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        with acceptance.isolated_cookie_name():
            raise RuntimeError("synthetic interruption")
    assert (web_session.WEB_SESSION_COOKIE, dependencies.WEB_SESSION_COOKIE) == original


def test_stop_directory_cleanup_removes_only_the_unique_verified_owned_directory(tmp_path):
    owned = tmp_path / (acceptance.TEMP_PREFIX + "fixture")
    owned.mkdir()
    (owned / "stop.signal").write_text("stop", encoding="utf-8")
    acceptance.remove_owned_temporary(owned, tmp_path)
    assert tmp_path.is_dir() and not owned.exists()


@pytest.mark.parametrize("kind", ["relative", "wrong-prefix", "wrong-parent", "parent-itself", "symlink"])
def test_stop_cleanup_rejects_unowned_targets_before_dispatch(kind, tmp_path, monkeypatch):
    remove = Mock(side_effect=AssertionError("unowned deletion must never dispatch"))
    monkeypatch.setattr(acceptance.shutil, "rmtree", remove)
    target = tmp_path / (acceptance.TEMP_PREFIX + "fixture")
    if kind == "relative":
        target = Path(acceptance.TEMP_PREFIX + "relative")
    elif kind == "wrong-prefix":
        target = tmp_path / "not-an-owned-acceptance-directory"
    elif kind == "wrong-parent":
        target = tmp_path.parent / target.name
    elif kind == "parent-itself":
        target = tmp_path
    else:
        target = Mock(spec=Path)
        target.is_absolute.return_value = True
        target.is_symlink.return_value = True
    with pytest.raises(RuntimeError, match="cleanup scope"):
        acceptance.remove_owned_temporary(target, tmp_path)
    remove.assert_not_called()


def test_stop_cleanup_requires_confirmation_instead_of_silently_claiming_success(tmp_path, monkeypatch):
    owned = tmp_path / (acceptance.TEMP_PREFIX + "fixture")
    owned.mkdir()
    monkeypatch.setattr(acceptance.shutil, "rmtree", Mock())
    with pytest.raises(RuntimeError, match="not confirmed"):
        acceptance.remove_owned_temporary(owned, tmp_path)


@pytest.mark.parametrize("failure", ["completed", "cleanup", "unchanged", "coverage", "guard", "no-fault", "two-faults",
                                     "provider_violations", "stop_errors", "buffer_violations", "timed_out"])
def test_final_success_requires_coverage_unchanged_data_cleanup_exact_one_fault_and_no_timeout(failure):
    arguments = [True, True, True, True, [], {"injected_failures": 1}]
    assert acceptance.result_is_success(*arguments)
    if failure in {"completed", "cleanup", "unchanged", "coverage"}:
        arguments[["completed", "cleanup", "unchanged", "coverage"].index(failure)] = False
    elif failure == "guard":
        arguments[4] = ["synthetic denied I/O"]
    elif failure in {"no-fault", "two-faults"}:
        arguments[5]["injected_failures"] = 0 if failure == "no-fault" else 2
    else:
        arguments[5][failure] = 1
    assert not acceptance.result_is_success(*arguments)


@pytest.mark.parametrize("requested,now,expected", [
    (False, 9.0, None), (True, 9.0, "requested"),
    (False, 10.0, "timed_out"), (True, 10.0, "timed_out"), (True, 10.1, "timed_out"),
])
def test_deadline_wins_when_a_stop_signal_arrives_at_or_after_the_limit(requested, now, expected):
    assert acceptance.stop_reason(requested, now, 10.0) == expected


@pytest.mark.parametrize("arguments", [
    ["--port", "8811"], ["--port", "8810"], ["--max-seconds", "29"], ["--max-seconds", "901"],
    ["--check-entry", "--docker-exe", "fixture-docker"], ["--check-entry", "--max-seconds", "30"],
    ["--collect-only"],
])
def test_invalid_entry_options_cannot_start_a_server_or_connect_a_database(arguments, monkeypatch, capsys):
    serve, checks = Mock(), Mock()
    monkeypatch.setattr(acceptance, "run_acceptance", serve)
    monkeypatch.setattr(entry, "run_checks", checks)
    with pytest.raises(SystemExit) as failure:
        acceptance.main(arguments)
    assert failure.value.code == 2
    serve.assert_not_called()
    checks.assert_not_called()
    capsys.readouterr()


def test_check_entry_dispatches_only_fixed_safety_checks(monkeypatch):
    serve, checks = Mock(), Mock(return_value=7)
    monkeypatch.setattr(acceptance, "run_acceptance", serve)
    monkeypatch.setattr(entry, "run_checks", checks)
    assert acceptance.PORT == 8811 and acceptance.MAX_SECONDS == 900
    assert acceptance.main(["--check-entry"]) == 7
    checks.assert_called_once_with()
    serve.assert_not_called()


@pytest.mark.parametrize("changed", ["missing-history", "missing-document", "extra"])
def test_safety_collection_requires_both_the_reused_guard_and_new_document_safety_file(changed):
    paths = list(entry.TEST_PATHS)
    if changed == "missing-history":
        paths = [path for path in paths if path.name != "test_history_acceptance_guard.py"]
    elif changed == "missing-document":
        paths = [path for path in paths if path.name != "test_document_acceptance_guard.py"]
    else:
        paths.append(entry.BACKEND / "tests/test_document_listing_db.py")
    with pytest.raises(pytest.UsageError, match="both fixed safety files"):
        entry.DocumentAcceptanceEntryAudit().pytest_collection_modifyitems([SimpleNamespace(path=path) for path in paths])


@pytest.mark.parametrize("incomplete", ["skip", "xfail", "zero", "missing-pass", "guard-violation"])
def test_safe_entry_does_not_turn_incomplete_or_denied_io_green(incomplete, monkeypatch, capsys):
    def invoke(audit, paths):
        assert paths == entry.TEST_PATHS
        audit.pytest_collection_modifyitems([SimpleNamespace(path=path) for path in paths])
        audit.passed = audit.collected
        if incomplete == "skip":
            audit.skipped = 1
        elif incomplete == "xfail":
            audit.xfailed = 1
        elif incomplete == "zero":
            audit.collected = audit.passed = 0
        elif incomplete == "missing-pass":
            audit.passed -= 1
        else:
            try:
                socket.create_connection(("127.0.0.1", 8811))
            except Exception:
                pass
        return 0

    monkeypatch.setattr(entry, "invoke_pytest", invoke)
    assert entry.run_checks() == 1
    assert "final_exit=1" in capsys.readouterr().out
