"""Safety regression checks for the fixed, no-private-settings contract entry."""

import asyncio
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import psycopg
import pytest
from sqlalchemy.engine import Engine

from app.core.config import Settings
from scripts import run_contract_tests as runner


def passing_collection(audit):
    audit.pytest_collection_modifyitems([SimpleNamespace(path=path) for path in runner.TEST_PATHS])
    audit.passed = audit.collected


def test_environment_preparation_removes_inherited_inputs_without_echo(monkeypatch, capsys):
    monkeypatch.setenv("EAAP_DATABASE_URL", "inherited-private-sentinel")
    monkeypatch.setenv("EAAP_GENERATION_API_KEY", "inherited-key-sentinel")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p unexpected_plugin")
    monkeypatch.setenv("PYTEST_PLUGINS", "unexpected_plugin")
    runner.prepare_environment()

    assert {name for name in os.environ if name.upper().startswith("EAAP_")} == {"EAAP_APP_ENV"}
    assert os.environ["EAAP_APP_ENV"] == "test"
    assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "PYTEST_ADDOPTS" not in os.environ and "PYTEST_PLUGINS" not in os.environ
    assert capsys.readouterr() == ("", "")


def test_settings_ignore_environment_dotenv_and_secret_files(tmp_path, monkeypatch):
    from pydantic_settings import DotEnvSettingsSource, EnvSettingsSource, SecretsSettingsSource

    # These are generated fixtures, never the developer's configuration files.
    untrusted = tmp_path / "untrusted-settings.txt"
    untrusted.write_text("EAAP_APP_ENV=production\nEAAP_GENERATION_API_KEY=fixture-key", encoding="utf-8")
    source_read = Mock(side_effect=AssertionError("private source must not be read"))
    monkeypatch.setattr(EnvSettingsSource, "_load_env_vars", source_read)
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", source_read)
    monkeypatch.setattr(SecretsSettingsSource, "get_field_value", source_read)
    monkeypatch.setenv("EAAP_GENERATION_API_KEY", "environment-key-sentinel")

    with runner.isolated_settings():
        controlled = Settings(_env_file=untrusted, _secrets_dir=tmp_path)
        assert Settings.model_config["env_file"] is None
        assert controlled.app_env == "test" and controlled.generation_api_key is None
        assert controlled.database_url is None
        explicit = Settings(app_env="production", web_origin="https://controlled.example.test")
        assert explicit.app_env == "production"
    source_read.assert_not_called()


@pytest.mark.parametrize("arguments", [["backend/tests"], ["--collect-only"], ["-p", "unexpected_plugin"]])
def test_cli_cannot_expand_the_suite_or_load_plugins(arguments, monkeypatch, capsys):
    preparation = Mock(side_effect=AssertionError("argument rejection must happen first"))
    monkeypatch.setattr(runner, "prepare_environment", preparation)
    assert runner.main(arguments) == 2
    preparation.assert_not_called()
    assert "unexpected_plugin" not in capsys.readouterr().err


def test_entry_isolates_before_collection_from_an_unrelated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EAAP_DATABASE_URL", "inherited-private-sentinel")
    calls = []

    def collect(audit):
        calls.append(True)
        assert Settings.model_config["env_file"] is None
        assert Settings().app_env == "test" and Settings().database_url is None
        assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert "PYTEST_ADDOPTS" not in os.environ
        assert all(path.is_absolute() and path.is_file() for path in runner.TEST_PATHS)
        passing_collection(audit)
        return 0

    monkeypatch.setattr(runner, "_invoke_pytest", collect)
    assert runner.main([]) == 0 and calls == [True]


def test_pytest_has_only_fixed_paths_and_no_discovered_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p unexpected_plugin", encoding="utf-8")
    captured = []

    def run(arguments, *, plugins):
        captured.append(arguments)
        assert len(plugins) == 1 and isinstance(plugins[0], runner.ContractSuiteAudit)
        temporary = Path(arguments[arguments.index("--basetemp") + 1])
        assert temporary.name == "pytest" and not temporary.exists()
        assert temporary.parent.is_dir() and temporary.parent.name.startswith("eaap-contract-")
        return 0

    monkeypatch.setattr(pytest, "main", run)
    assert runner._invoke_pytest(runner.ContractSuiteAudit()) == 0
    arguments, = captured
    assert arguments[arguments.index("-c") + 1] == os.devnull
    assert arguments[arguments.index("--rootdir") + 1] == str(runner.BACKEND)
    assert "--noconftest" in arguments
    assert arguments[arguments.index("-p") + 1] == "no:cacheprovider"
    assert arguments[-len(runner.TEST_NAMES):] == [str(path) for path in runner.TEST_PATHS]
    assert "unexpected_plugin" not in arguments
    assert not Path(arguments[arguments.index("--basetemp") + 1]).parent.exists()


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_collection_must_match_the_whole_whitelist(change):
    paths = list(runner.TEST_PATHS)
    if change == "missing":
        paths.pop()
    else:
        paths.append(runner.BACKEND / "tests/test_readiness_db.py")
    items = [SimpleNamespace(path=path) for path in paths]
    with pytest.raises(pytest.UsageError, match="fixed file whitelist"):
        runner.ContractSuiteAudit().pytest_collection_modifyitems(items)


@pytest.mark.parametrize("incomplete", ["skip", "xfail", "xpass", "zero", "not-all-passed", "not-collected"])
def test_incomplete_or_expected_failure_cannot_turn_the_entry_green(incomplete, monkeypatch, capsys):
    def run(audit):
        passing_collection(audit)
        if incomplete == "skip":
            audit.pytest_runtest_logreport(SimpleNamespace(skipped=True, passed=False, when="setup"))
        elif incomplete in {"xfail", "xpass"}:
            audit.pytest_runtest_logreport(SimpleNamespace(
                skipped=incomplete == "xfail", passed=incomplete == "xpass", when="call", wasxfail="controlled",
            ))
        elif incomplete == "zero":
            audit.collected = audit.passed = 0
        elif incomplete == "not-collected":
            audit.collection_valid = False
        else:
            audit.passed -= 1
        return 0

    monkeypatch.setattr(runner, "_invoke_pytest", run)
    assert runner.main([]) == 1
    assert "pytest_exit=0" in capsys.readouterr().out


@pytest.mark.parametrize("host", ["external.example.invalid", "127.0.0.1", "::1"])
@pytest.mark.parametrize("operation", ["connect", "connect_ex"])
def test_external_and_loopback_socket_connections_are_blocked(host, operation):
    with runner.ResourceGuard() as guard:
        with socket.socket() as connection:
            with pytest.raises(runner.ResourceAccessBlocked):
                getattr(connection, operation)((host, 9))
    assert guard.attempts == ["socket I/O"]


def network_http_request():
    with httpx.Client(trust_env=False) as client:
        client.get("https://external.example.invalid")


@pytest.mark.parametrize("operation", [
    lambda: socket.getaddrinfo("external.example.invalid", 443),
    lambda: socket.create_connection(("127.0.0.1", 9)),
    network_http_request,
    lambda: psycopg.connect("controlled-database-sentinel"),
    lambda: psycopg.Connection.connect("controlled-database-sentinel"),
    lambda: Engine.connect(object()),
    lambda: Engine.raw_connection(object()),
])
def test_network_and_database_dispatch_are_blocked(operation):
    with runner.ResourceGuard() as guard:
        with pytest.raises(runner.ResourceAccessBlocked):
            operation()
    assert len(guard.attempts) == 1


def test_swallowed_resource_violation_still_fails_the_entry(monkeypatch, capsys):
    def apparently_passing_suite(audit):
        passing_collection(audit)
        try:
            socket.create_connection(("127.0.0.1", 9))
        except Exception:
            pass  # Readiness and providers can legitimately contain exceptions.
        return 0

    monkeypatch.setattr(runner, "_invoke_pytest", apparently_passing_suite)
    assert runner.main([]) == 1
    message = capsys.readouterr().err
    assert "prohibited network or database" in message and "127.0.0.1" not in message


def test_async_socket_and_http_network_connections_are_blocked():
    async def attempt():
        loop = asyncio.get_running_loop()
        with socket.socket() as connection:
            with pytest.raises(runner.ResourceAccessBlocked):
                await loop.sock_connect(connection, ("127.0.0.1", 9))
        async with httpx.AsyncClient(trust_env=False) as client:
            with pytest.raises(runner.ResourceAccessBlocked):
                await client.get("https://external.example.invalid")
        with pytest.raises(runner.ResourceAccessBlocked):
            await psycopg.AsyncConnection.connect("controlled-database-sentinel")

    with runner.ResourceGuard() as guard:
        asyncio.run(attempt())
    assert len(guard.attempts) == 3


def test_socketpair_testclient_asgi_and_mock_transports_remain_in_process():
    application = FastAPI()

    @application.get("/controlled")
    async def controlled():
        return {"status": "ok"}

    async def async_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://in-process.test") as client:
            assert (await client.get("/controlled")).json() == {"status": "ok"}

    with runner.ResourceGuard() as guard:
        first, second = socket.socketpair()
        try:
            first.sendall(b"wake")
            assert second.recv(4) == b"wake"
        finally:
            first.close()
            second.close()
        with TestClient(application) as client:
            assert client.get("/controlled").json() == {"status": "ok"}
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"mock": True}))
        with httpx.Client(transport=transport) as client:
            assert client.get("https://mock.example.test").json() == {"mock": True}
        asyncio.run(async_request())
    assert guard.attempts == []
