"""Safety checks for the PostgreSQL entry; all credentials and dispatches are fixtures."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import psycopg
import pytest
from sqlalchemy.engine import Engine, URL

from app.core.config import Settings
from scripts import run_postgres_tests as runner
from scripts.run_contract_tests import ResourceAccessBlocked


ROLE = "eaap_check_" + "a" * 32
CONTAINER = "b" * 64


def target_url(**changes):
    return URL.create(**({
        "drivername": "postgresql+psycopg", "username": ROLE,
        "password": "fixture-only-password", "host": "127.0.0.1",
        "port": 5432, "database": "eaap_test",
    } | changes))


def connection_arguments(**changes):
    return {
        "host": "127.0.0.1", "port": "5432", "dbname": "eaap_test",
        "user": ROLE, "password": "fixture-only-password",
    } | changes


def database_items():
    paths = {path.name: path for path in runner.DB_TEST_PATHS}
    return [SimpleNamespace(path=paths[file_name], originalname=name, name=name)
            for file_name, name in (node.split("::", 1) for node in sorted(runner.EXPECTED_NODES))]


def passing_database(audit):
    audit.pytest_collection_modifyitems(database_items())
    audit.passed = audit.collected


def fake_admin(monkeypatch):
    admin = Mock(spec=runner.Admin)
    admin.sql.return_value = "0"
    factory = Mock(return_value=admin)
    monkeypatch.setattr(runner, "Admin", factory)
    return admin, factory


def test_targets_are_fixed_and_immutable():
    local = runner.local_target()
    assert local == runner.Target("enterprise_ai_agent_platform-db-1", "eaap", 5432)
    with pytest.raises(FrozenInstanceError):
        local.port = 9
    assert runner.ci_target(CONTAINER, 15432) == runner.Target(CONTAINER, "eaap_ci_admin", 15432)


@pytest.mark.parametrize("container", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "project-db-1"])
def test_ci_rejects_noncanonical_container_identifiers(container):
    with pytest.raises(ValueError):
        runner.ci_target(container, 5432)


@pytest.mark.parametrize("port", [0, -1, 65536, True, 5432.0, "5432", None])
def test_ci_requires_a_strict_valid_port(port):
    with pytest.raises(ValueError):
        runner.ci_target(CONTAINER, port)


@pytest.mark.parametrize("arguments", [
    ["--port", "5432"], ["--ci-container", CONTAINER],
    ["--ci-container", "invalid", "--port", "5432"],
    ["--ci-container", CONTAINER, "--port", "0"],
    ["--ci-container", CONTAINER, "--port", "65536"],
    ["--check-entry", "--ci-container", CONTAINER, "--port", "5432"],
    ["--check-entry", "--port", "5432"],
    ["--collect-only"],
])
def test_invalid_cli_targets_are_rejected_before_dispatch(arguments, monkeypatch, capsys):
    database, checks = Mock(), Mock()
    monkeypatch.setattr(runner, "run_database", database)
    monkeypatch.setattr(runner, "run_checks", checks)
    with pytest.raises(SystemExit) as failure:
        runner.main(arguments)
    assert failure.value.code == 2
    database.assert_not_called()
    checks.assert_not_called()
    capsys.readouterr()


@pytest.mark.parametrize("ci", [False, True])
def test_cli_dispatches_only_the_selected_fixed_database_target(ci, monkeypatch):
    run = Mock(return_value=7)
    monkeypatch.setattr(runner, "run_database", run)
    arguments = ["--docker-exe", "fixture-docker"]
    if ci:
        arguments += ["--ci-container", CONTAINER, "--port", "15432"]
    assert runner.main(arguments) == 7
    expected = runner.ci_target(CONTAINER, 15432) if ci else runner.local_target()
    run.assert_called_once_with("fixture-docker", expected)


@pytest.mark.parametrize("method", ["connect", "raw_connection"])
@pytest.mark.parametrize("changes", [
    {"drivername": "sqlite"}, {"host": "localhost"}, {"host": "external.example.invalid"},
    {"port": 5433}, {"username": "another-test-user"}, {"database": "eaap"},
    {"password": None}, {"password": ""}, {"query": {"service": "fixture-service"}},
    {"query": {"hostaddr": "127.0.0.1"}},
])
def test_sqlalchemy_rejects_other_targets_before_driver_dispatch(method, changes, monkeypatch):
    dispatch = Mock(return_value=object())
    monkeypatch.setattr(Engine, method, dispatch)
    engine = SimpleNamespace(url=target_url(**changes))
    with runner.PostgresGuard(target_url()) as guard:
        with pytest.raises(ResourceAccessBlocked):
            getattr(Engine, method)(engine)
    dispatch.assert_not_called()
    assert guard.attempts == ["out-of-scope database connection"]


@pytest.mark.parametrize("method", ["connect", "raw_connection"])
@pytest.mark.parametrize("password", ["fixture-only-password", "fixture-deliberately-wrong-password"])
def test_sqlalchemy_allows_only_the_same_target_including_readiness_wrong_password(method, password, monkeypatch):
    sentinel = object()
    dispatch = Mock(return_value=sentinel)
    monkeypatch.setattr(Engine, method, dispatch)
    engine = SimpleNamespace(url=target_url(password=password))
    with runner.PostgresGuard(target_url()) as guard:
        assert getattr(Engine, method)(engine) is sentinel
    dispatch.assert_called_once_with(engine)
    assert guard.attempts == []


@pytest.mark.parametrize("class_method", [False, True])
@pytest.mark.parametrize("changes", [
    {"host": "localhost"}, {"host": "external.example.invalid"}, {"port": "5433"},
    {"dbname": "eaap"}, {"user": "another-test-user"}, {"password": None},
    {"password": ""}, {"hostaddr": "127.0.0.1"}, {"service": "fixture-service"},
    {"passfile": "fixture-password-file"},
    {"sslmode": "require"}, {"sslmode": None}, {"sslcert": "fixture-client-cert"},
    {"sslkey": "fixture-client-key"}, {"sslrootcert": "fixture-root-cert"},
    {"gssencmode": "require"},
])
def test_libpq_overrides_are_rejected_before_dispatch(class_method, changes, monkeypatch):
    dispatch = Mock(return_value=object())
    monkeypatch.setattr(psycopg, "connect", dispatch)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda _cls, *args, **kwargs: dispatch(*args, **kwargs)))
    with runner.PostgresGuard(target_url()) as guard:
        with pytest.raises(ResourceAccessBlocked):
            connect = psycopg.Connection.connect if class_method else psycopg.connect
            connect(**connection_arguments(**changes))
    dispatch.assert_not_called()
    assert guard.attempts == ["out-of-scope database connection"]


@pytest.mark.parametrize("conninfo", [
    "", "host=127.0.0.1 dbname=eaap_test", "not-a-valid-conninfo",
    "postgresql://fixture:fixture@127.0.0.1:5432/eaap_test?hostaddr=127.0.0.1",
])
def test_libpq_requires_an_explicit_complete_target_without_uri_overrides(conninfo, monkeypatch):
    dispatch = Mock()
    monkeypatch.setattr(psycopg, "connect", dispatch)
    with runner.PostgresGuard(target_url()) as guard:
        with pytest.raises(ResourceAccessBlocked):
            psycopg.connect(conninfo)
    dispatch.assert_not_called()
    assert len(guard.attempts) == 1


@pytest.mark.parametrize("class_method", [False, True])
@pytest.mark.parametrize("port", ["5432", 5432])
def test_libpq_same_target_allows_probe_client_controls_and_wrong_password(class_method, port, monkeypatch):
    sentinel = object()
    dispatch = Mock(return_value=sentinel)
    monkeypatch.setattr(psycopg, "connect", dispatch)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda _cls, *args, **kwargs: dispatch(*args, **kwargs)))
    arguments = connection_arguments(password="fixture-deliberately-wrong-password", port=port) | {
        "connect_timeout": 3, "options": "-c default_transaction_read_only=on -c statement_timeout=2000",
        "autocommit": False, "prepare_threshold": None,
    }
    with runner.PostgresGuard(target_url()) as guard:
        connect = psycopg.Connection.connect if class_method else psycopg.connect
        assert connect(**arguments) is sentinel
    dispatch.assert_called_once_with("", **arguments, sslmode="disable", gssencmode="disable", passfile=os.devnull)
    assert guard.attempts == []


@pytest.mark.parametrize("operation", ["socket", "http", "resolution"])
def test_allowing_postgres_does_not_allow_other_network_operations(operation):
    with runner.PostgresGuard(target_url()) as guard:
        with pytest.raises(ResourceAccessBlocked):
            if operation == "socket":
                with socket.socket() as connection:
                    connection.connect(("127.0.0.1", 5432))
            elif operation == "http":
                with httpx.Client(trust_env=False) as client:
                    client.get("https://model.example.invalid")
            else:
                socket.getaddrinfo("external.example.invalid", 443)
    assert len(guard.attempts) == 1


def test_exact_seventeen_database_nodes_are_required():
    audit = runner.DatabaseSuiteAudit()
    passing_database(audit)
    assert audit.collected == 17 and audit.complete()


@pytest.mark.parametrize("change", ["missing", "extra", "renamed", "duplicate", "different-path"])
def test_collection_cannot_substitute_nodes_even_with_the_same_count(change):
    items = database_items()
    if change == "missing":
        items.pop()
    elif change == "extra":
        items.append(SimpleNamespace(path=items[0].path, originalname="extra_test", name="extra_test"))
    elif change == "renamed":
        items[0].originalname = items[0].name = "unexpected_test"
    elif change == "duplicate":
        items[0] = items[1]
    else:
        items[0].path = Path("unrelated") / items[0].path.name
    with pytest.raises(pytest.UsageError, match="exact fixed nodes"):
        runner.DatabaseSuiteAudit().pytest_collection_modifyitems(items)


@pytest.mark.parametrize("incomplete", ["skip", "xfail", "xpass", "missing-pass", "uncollected"])
def test_pytest_zero_does_not_hide_incomplete_database_checks(incomplete, monkeypatch, capsys):
    admin, _factory = fake_admin(monkeypatch)

    def invoke(audit, paths):
        assert paths == runner.DB_TEST_PATHS
        passing_database(audit)
        if incomplete in {"skip", "xfail", "xpass"}:
            report = SimpleNamespace(skipped=incomplete != "xpass", passed=incomplete == "xpass", when="call")
            if incomplete in {"xfail", "xpass"}:
                report.wasxfail = "fixture-expected-failure"
            audit.pytest_runtest_logreport(report)
        elif incomplete == "missing-pass":
            audit.passed -= 1
        else:
            audit.collection_valid = False
        return 0

    monkeypatch.setattr(runner, "invoke_pytest", invoke)
    assert runner.run_database("fixture-docker", runner.local_target()) == 1
    admin.cleanup.assert_called_once()
    assert "pytest_exit=0" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["none", "cleanup", "confirm", "create", "swallowed-io", "pytest"])
def test_database_result_requires_tests_guards_and_cleanup_to_succeed(failure, monkeypatch, capsys):
    admin, _factory = fake_admin(monkeypatch)
    if failure in {"cleanup", "confirm", "create"}:
        getattr(admin, failure).side_effect = RuntimeError("fixture-private-detail-must-not-be-printed")

    def invoke(audit, paths):
        assert paths == runner.DB_TEST_PATHS
        assert Settings().database_url is None and Settings().app_env == "test"
        assert Settings.model_config["env_file"] is None
        passing_database(audit)
        if failure == "swallowed-io":
            try:
                socket.create_connection(("127.0.0.1", 5432))
            except Exception:
                pass
        return 4 if failure == "pytest" else 0

    invoke_mock = Mock(side_effect=invoke)
    monkeypatch.setattr(runner, "invoke_pytest", invoke_mock)
    result = runner.run_database("fixture-docker", runner.local_target())
    assert result == (0 if failure == "none" else 4 if failure == "pytest" else 1)
    if failure == "confirm":
        admin.create.assert_not_called()
        admin.cleanup.assert_not_called()
        invoke_mock.assert_not_called()
    else:
        admin.cleanup.assert_called_once()
    assert "EAAP_TEST_DATABASE_URL" not in os.environ
    output = capsys.readouterr().out
    assert "fixture-private-detail-must-not-be-printed" not in output
    if failure == "cleanup":
        assert "cleanup_ok=False" in output
    if failure == "swallowed-io":
        assert "violations=1" in output and "pytest_exit=0" in output


def test_existing_role_is_neither_created_nor_cleaned_up(monkeypatch, capsys):
    admin, _factory = fake_admin(monkeypatch)
    admin.sql.return_value = "1"
    invoke = Mock(side_effect=AssertionError("existing role must stop before pytest"))
    monkeypatch.setattr(runner, "invoke_pytest", invoke)
    assert runner.run_database("fixture-docker", runner.local_target()) == 1
    admin.confirm.assert_called_once()
    admin.create.assert_not_called()
    admin.cleanup.assert_not_called()
    invoke.assert_not_called()
    assert "pytest_exit=not-run" in capsys.readouterr().out


def test_database_entry_removes_inherited_libpq_credential_and_target_inputs(monkeypatch):
    admin, _factory = fake_admin(monkeypatch)
    for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.setenv(name, "fixture-untrusted-libpq-value")

    def invoke(audit, paths):
        assert not any(name.upper().startswith("PG") for name in os.environ)
        assert paths == runner.DB_TEST_PATHS
        passing_database(audit)
        return 0

    monkeypatch.setattr(runner, "invoke_pytest", invoke)
    assert runner.run_database("fixture-docker", runner.local_target()) == 0
    admin.cleanup.assert_called_once()


def test_check_entry_uses_only_the_safety_file_without_database_management(monkeypatch):
    administration = Mock(side_effect=AssertionError("entry checks cannot manage a database"))
    monkeypatch.setattr(runner, "Admin", administration)

    def invoke(audit, paths):
        assert paths == (runner.BACKEND / "tests/test_postgres_runner.py",)
        assert isinstance(audit, runner.EntrySuiteAudit)
        assert Settings().database_url is None and Settings().app_env == "test"
        audit.pytest_collection_modifyitems([SimpleNamespace(path=paths[0])])
        audit.passed = audit.collected
        return 0

    monkeypatch.setattr(runner, "invoke_pytest", invoke)
    assert runner.main(["--check-entry"]) == 0
    administration.assert_not_called()


def test_settings_sources_remain_disabled_even_with_explicit_overrides(tmp_path, monkeypatch):
    from pydantic_settings import DotEnvSettingsSource, EnvSettingsSource, SecretsSettingsSource

    fixture_file = tmp_path / "synthetic-settings.txt"
    fixture_file.write_text("EAAP_APP_ENV=production\nEAAP_DATABASE_URL=fixture-only-value", encoding="utf-8")
    source_read = Mock(side_effect=AssertionError("private settings source must not be read"))
    monkeypatch.setattr(EnvSettingsSource, "_load_env_vars", source_read)
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", source_read)
    monkeypatch.setattr(SecretsSettingsSource, "get_field_value", source_read)
    monkeypatch.setenv("EAAP_DATABASE_URL", "fixture-inherited-value")
    with runner.isolated_settings():
        settings = Settings(_env_file=fixture_file, _secrets_dir=tmp_path)
        assert settings.app_env == "test" and settings.database_url is None
        assert Settings.model_config["env_file"] is None
    source_read.assert_not_called()


def test_pytest_invocation_disables_configuration_and_uses_only_fixed_paths(monkeypatch):
    captured = []

    def invoke(arguments, *, plugins):
        captured.append(arguments)
        assert len(plugins) == 1 and isinstance(plugins[0], runner.DatabaseSuiteAudit)
        temporary = Path(arguments[arguments.index("--basetemp") + 1])
        assert temporary.parent.is_dir() and temporary.parent.name.startswith("eaap-postgres-")
        return 0

    monkeypatch.setattr(pytest, "main", invoke)
    assert runner.invoke_pytest(runner.DatabaseSuiteAudit(), runner.DB_TEST_PATHS) == 0
    arguments, = captured
    assert arguments[arguments.index("-c") + 1] == os.devnull
    assert "--noconftest" in arguments and "--strict-config" in arguments
    assert arguments[-len(runner.DB_TEST_PATHS):] == [str(path) for path in runner.DB_TEST_PATHS]
    assert not Path(arguments[arguments.index("--basetemp") + 1]).parent.exists()


def test_cleanup_rejects_unexpected_owned_schema_without_expanding_drop_scope(monkeypatch):
    admin = runner.Admin("fixture-docker", runner.local_target())
    sql = Mock(side_effect=["1", "", "", "unexpected_owned_schema"])
    monkeypatch.setattr(admin, "sql", sql)
    with pytest.raises(RuntimeError, match="Unexpected owned schema"):
        admin.cleanup(ROLE)
    assert not any("DROP" in call.args[0] for call in sql.call_args_list)


def test_admin_rejects_wrong_actual_database_before_port_mapping(monkeypatch):
    admin = runner.Admin("fixture-docker", runner.local_target())
    monkeypatch.setattr(admin, "sql", Mock(return_value="eaap:eaap"))
    docker = Mock(side_effect=AssertionError("port mapping must not be queried after a database mismatch"))
    monkeypatch.setattr(runner.subprocess, "run", docker)
    with pytest.raises(RuntimeError, match="Actual database"):
        admin.confirm()
    docker.assert_not_called()


def test_admin_sql_uses_clean_container_environment_and_fixed_local_socket(monkeypatch):
    target = runner.ci_target(CONTAINER, 15432)
    admin = runner.Admin("fixture-docker", target)
    dispatch = Mock(return_value=SimpleNamespace(returncode=0, stdout=" fixture-row \n", stderr=""))
    monkeypatch.setattr(runner.subprocess, "run", dispatch)
    monkeypatch.setenv("PGHOST", "fixture-untrusted-host")
    monkeypatch.setenv("PGPASSFILE", "fixture-untrusted-password-file")
    assert admin.sql("SELECT 1;") == "fixture-row"
    arguments = dispatch.call_args.args[0]
    assert arguments[:6] == ["fixture-docker", "exec", "-i", CONTAINER, "env", "-i"]
    psql_index = arguments.index("psql")
    assert arguments[6:psql_index] == [
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PGPASSFILE=/dev/null", "PGSERVICEFILE=/dev/null",
    ]
    psql_arguments = arguments[psql_index + 1:]
    assert "-X" in psql_arguments and "-w" in psql_arguments
    assert psql_arguments[psql_arguments.index("-h") + 1] == "/var/run/postgresql"
    assert psql_arguments[psql_arguments.index("-p") + 1] == "5432"
    assert psql_arguments[psql_arguments.index("-U") + 1] == "eaap_ci_admin"
    assert psql_arguments[psql_arguments.index("-d") + 1] == "eaap_test"
    assert "ON_ERROR_STOP=1" in psql_arguments
    assert "fixture-untrusted-host" not in arguments and "fixture-untrusted-password-file" not in arguments
    options = dispatch.call_args.kwargs
    assert options["timeout"] == 30 and options["capture_output"] is True
    assert options["input"].endswith("SELECT 1;")


def test_admin_sql_failure_omits_process_diagnostics_and_sql(monkeypatch):
    admin = runner.Admin("fixture-docker", runner.local_target())
    dispatch = Mock(return_value=SimpleNamespace(
        returncode=1, stdout="fixture-private-diagnostic", stderr="fixture-private-diagnostic",
    ))
    monkeypatch.setattr(runner.subprocess, "run", dispatch)
    with pytest.raises(RuntimeError) as failure:
        admin.sql("SELECT 'fixture-private-statement';")
    assert str(failure.value) == "Test database administration was not confirmed"
