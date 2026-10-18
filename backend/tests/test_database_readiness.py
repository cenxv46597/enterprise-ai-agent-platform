"""Probe failure boundaries and resource handling; no database or Settings I/O."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import NullPool

from app.db import readiness


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(readiness, "Settings", lambda: SimpleNamespace(
        database_url="postgresql+psycopg://test:test@127.0.0.1/eaap_test",
    ))
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = 1
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(readiness, "create_engine", factory)
    return SimpleNamespace(connection=connection, engine=engine, factory=factory)


def test_success_uses_short_lived_read_only_connection(probe):
    assert readiness.database_is_ready() is True
    arguments = probe.factory.call_args.kwargs
    assert arguments["poolclass"] is NullPool
    assert arguments["hide_parameters"] is True
    assert arguments["connect_args"]["connect_timeout"] == 3
    options = arguments["connect_args"]["options"]
    assert "statement_timeout=2000" in options
    assert "lock_timeout=1000" in options
    assert "default_transaction_read_only=on" in options
    assert str(probe.connection.execute.call_args.args[0]) == "SELECT 1"
    probe.connection.execute.assert_called_once()
    probe.connection.commit.assert_not_called()
    probe.engine.connect.return_value.__exit__.assert_called_once()
    probe.engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("url", [None, "", "not a URL", "sqlite://", "postgresql://host/eaap_test"])
def test_missing_invalid_or_unsupported_configuration_does_not_connect(monkeypatch, probe, url):
    monkeypatch.setattr(readiness, "Settings", lambda: SimpleNamespace(database_url=url))
    assert readiness.database_is_ready() is False
    probe.factory.assert_not_called()


def test_settings_error_is_contained_before_engine_creation(monkeypatch, probe, capsys):
    def unavailable_settings():
        raise ValueError("synthetic secret must remain private")

    monkeypatch.setattr(readiness, "Settings", unavailable_settings)
    assert readiness.database_is_ready() is False
    probe.factory.assert_not_called()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("failure_stage", ["factory", "connect", "query", "result", "close", "dispose"])
def test_failures_are_contained_and_engine_disposal_is_attempted(probe, capsys, failure_stage):
    failure = RuntimeError("synthetic password/connection details must remain private")
    if failure_stage == "factory":
        probe.factory.side_effect = failure
    elif failure_stage == "connect":
        probe.engine.connect.side_effect = failure
    elif failure_stage == "query":
        probe.connection.execute.side_effect = failure
    elif failure_stage == "result":
        probe.connection.execute.return_value.scalar_one.side_effect = failure
    elif failure_stage == "close":
        probe.engine.connect.return_value.__exit__.side_effect = failure
    else:
        probe.engine.dispose.side_effect = failure

    assert readiness.database_is_ready() is False
    if failure_stage != "factory":
        probe.engine.dispose.assert_called_once_with()
    if failure_stage in {"query", "result", "close", "dispose"}:
        probe.engine.connect.return_value.__exit__.assert_called_once()
    assert capsys.readouterr() == ("", "")


def test_unexpected_query_value_is_not_ready(probe):
    probe.connection.execute.return_value.scalar_one.return_value = 0
    assert readiness.database_is_ready() is False
    probe.engine.dispose.assert_called_once_with()
