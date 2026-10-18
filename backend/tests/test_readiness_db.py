"""Real PostgreSQL probe checks, only with a temporary identity in eaap_test."""

import os
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.api.health import router
from app.db import readiness


@pytest.fixture
def test_url(monkeypatch):
    value = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("需要明确的独立测试库临时连接")
    url = make_url(value)
    if (url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost"}
            or url.database != "eaap_test" or url.port != 5432 or url.query or not url.password
            or not re.fullmatch(r"eaap_check_[0-9a-f]{32}", url.username or "")):
        pytest.fail("拒绝范围外数据库或非临时测试身份", pytrace=False)
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
                           connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT current_database()")).scalar_one() != "eaap_test":
                pytest.fail("实际连接不是独立测试库", pytrace=False)
    finally:
        engine.dispose()
    monkeypatch.setattr(readiness, "Settings", lambda: SimpleNamespace(database_url=value))
    return url


def test_ready_http_with_real_database_and_read_only_timeouts(test_url, monkeypatch):
    observed = []

    def observed_engine(*args, **kwargs):
        engine = create_engine(*args, **kwargs)

        @event.listens_for(engine, "connect")
        def inspect_session(dbapi_connection, _record):
            with dbapi_connection.cursor() as cursor:
                values = []
                for name in ("transaction_read_only", "statement_timeout", "lock_timeout"):
                    cursor.execute(f"SHOW {name}")
                    values.append(cursor.fetchone()[0])
                observed.append(values)
            dbapi_connection.rollback()

        return engine

    monkeypatch.setattr(readiness, "create_engine", observed_engine)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert observed == [["on", "2s", "1s"]]


def test_real_query_timeout_becomes_unavailable(test_url, monkeypatch):
    sqlstates = []

    def observed_engine(*args, **kwargs):
        engine = create_engine(*args, **kwargs)

        @event.listens_for(engine, "handle_error")
        def inspect_failure(context):
            sqlstates.append(context.original_exception.sqlstate)

        return engine

    # Inject a deliberately slow read only for this test; production remains SELECT 1.
    monkeypatch.setattr(readiness, "create_engine", observed_engine)
    monkeypatch.setattr(readiness, "text", lambda _statement: text("SELECT pg_sleep(3)"))
    assert readiness.database_is_ready() is False
    assert sqlstates == ["57014"]  # PostgreSQL query_canceled, not a wrong scalar value.


def test_real_authentication_failure_becomes_unavailable(test_url, monkeypatch):
    invalid = test_url.set(password=f"invalid-{uuid4().hex}").render_as_string(hide_password=False)
    monkeypatch.setattr(readiness, "Settings", lambda: SimpleNamespace(database_url=invalid))
    assert readiness.database_is_ready() is False
