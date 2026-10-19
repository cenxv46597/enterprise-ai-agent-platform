"""HTTP log origin and clocks, without settings, network, database or models."""

import asyncio
from datetime import UTC, datetime
import json

import pytest

from app.observability import http


@pytest.fixture
def events(monkeypatch):
    values = []
    monkeypatch.setattr(http.logger, "info", lambda message: values.append(json.loads(message)))
    return values


def request_scope(*, headers=None):
    return {
        "type": "http",
        "method": "GET",
        "path": "/controlled",
        "headers": headers or [],
    }


async def unused_receive():
    raise AssertionError("HTTP logging must not consume the request body")


async def successful_application(_scope, _receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def invoke(middleware, *, scope=None):
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(request_scope() if scope is None else scope, unused_receive, send))
    return messages


def test_summary_uses_utc_milliseconds_and_records_after_application_returns(events, monkeypatch):
    application_finished = False
    clock_calls = []

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            assert application_finished, "recorded_at describes summary recording, not request arrival"
            clock_calls.append(tz)
            return datetime(2026, 10, 19, 3, 4, 5, 123456, tzinfo=UTC)

    async def application(scope, receive, send):
        nonlocal application_finished
        await successful_application(scope, receive, send)
        application_finished = True

    elapsed_ticks = iter([100.0, 100.25])
    monkeypatch.setattr(http, "datetime", ControlledDateTime)
    monkeypatch.setattr(http, "perf_counter", lambda: next(elapsed_ticks))
    invoke(http.HttpTraceMiddleware(application))

    assert clock_calls == [UTC] and len(events) == 1
    assert events[0]["recorded_at"] == "2026-10-19T03:04:05.123Z"
    assert events[0]["elapsed_ms"] == 250.0
    assert events[0]["outcome"] == "completed"


def test_wall_clock_can_move_backwards_without_changing_monotonic_durations(events, monkeypatch):
    wall_times = iter([
        datetime(2026, 10, 19, 3, 4, 5, tzinfo=UTC),
        datetime(2026, 10, 19, 2, 4, 5, tzinfo=UTC),
    ])

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return next(wall_times)

    elapsed_ticks = iter([10.0, 10.05, 20.0, 20.125])
    monkeypatch.setattr(http, "datetime", ControlledDateTime)
    monkeypatch.setattr(http, "perf_counter", lambda: next(elapsed_ticks))
    middleware = http.HttpTraceMiddleware(successful_application)
    invoke(middleware)
    invoke(middleware)

    assert [event["recorded_at"] for event in events] == [
        "2026-10-19T03:04:05.000Z", "2026-10-19T02:04:05.000Z",
    ]
    assert [event["elapsed_ms"] for event in events] == [50.0, 125.0]


def test_interleaved_applications_keep_their_own_environments(events):
    production_scope = request_scope()
    test_scope = request_scope()

    async def run_interleaved():
        production_entered = asyncio.Event()
        release_production = asyncio.Event()

        async def production_application(scope, receive, send):
            production_entered.set()
            await release_production.wait()
            await successful_application(scope, receive, send)

        async def send(_message):
            pass

        production = http.HttpTraceMiddleware(production_application, app_env="production")
        testing = http.HttpTraceMiddleware(successful_application, app_env="test")
        production_task = asyncio.create_task(production(production_scope, unused_receive, send))
        try:
            await production_entered.wait()
            await testing(test_scope, unused_receive, send)
            release_production.set()
            await production_task
        finally:
            if not production_task.done():
                production_task.cancel()
                await asyncio.gather(production_task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(run_interleaved(), timeout=5))

    assert len(events) == 2
    assert [(event["trace_id"], event["environment"]) for event in events] == [
        (test_scope["state"]["http_trace_id"], "test"),
        (production_scope["state"]["http_trace_id"], "production"),
    ]
    assert all(event["service"] == "eaap-api" for event in events)


def test_request_headers_cannot_supply_service_or_environment_metadata(events):
    scope = request_scope(headers=[
        (b"service", b"SERVICE_HEADER_SENTINEL"),
        (b"x-service", b"SERVICE_HEADER_SENTINEL"),
        (b"environment", b"production"),
        (b"x-environment", b"production"),
        (b"x-app-env", b"production"),
    ])
    messages = invoke(http.HttpTraceMiddleware(successful_application), scope=scope)

    assert messages[0]["status"] == 204
    assert len(events) == 1
    assert events[0]["service"] == "eaap-api"
    assert events[0]["environment"] == "local"
    assert "SERVICE_HEADER_SENTINEL" not in json.dumps(events)


@pytest.mark.parametrize("app_env", ["", "staging", "production\n", "ENV_SECRET_SENTINEL"])
def test_invalid_environment_is_rejected_without_echoing_arbitrary_configuration(events, app_env):
    with pytest.raises(ValueError) as caught:
        http.HttpTraceMiddleware(successful_application, app_env=app_env)

    assert "ENV_SECRET_SENTINEL" not in str(caught.value)
    assert events == []


def test_failed_timestamp_collection_does_not_replace_a_successful_response(events, monkeypatch):
    class BrokenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            raise RuntimeError("CLOCK_FAILURE_SENTINEL")

    monkeypatch.setattr(http, "datetime", BrokenDateTime)
    scope = request_scope()
    messages = invoke(http.HttpTraceMiddleware(successful_application), scope=scope)

    assert messages[0]["status"] == 204 and messages[1]["body"] == b""
    assert dict(messages[0]["headers"])[b"x-trace-id"].decode() == scope["state"]["http_trace_id"]
    assert events == []


def test_failed_timestamp_collection_does_not_mask_the_original_business_exception(events, monkeypatch):
    original_error = ValueError("controlled business failure")

    class BrokenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            raise RuntimeError("CLOCK_FAILURE_SENTINEL")

    async def application(_scope, _receive, _send):
        raise original_error

    monkeypatch.setattr(http, "datetime", BrokenDateTime)
    with pytest.raises(ValueError) as caught:
        invoke(http.HttpTraceMiddleware(application))

    assert caught.value is original_error
    assert events == []
