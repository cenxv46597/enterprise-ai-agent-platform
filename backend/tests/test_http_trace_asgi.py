"""ASGI lifecycle and logging failures without network, settings or database."""

import asyncio
from io import StringIO
import json
import logging
from types import SimpleNamespace

import pytest

from app.observability import http


@pytest.fixture
def events(monkeypatch):
    values = []
    monkeypatch.setattr(http.logger, "info", lambda message: values.append(json.loads(message)))
    return values


def scope():
    return {"type": "http", "method": "GET", "path": "/not-to-log",
            "route": SimpleNamespace(path="/stream/{identifier}")}


async def unused_receive():
    raise AssertionError("middleware must not consume request bodies")


@pytest.mark.parametrize("headers_sent", [False, True])
def test_exceptions_keep_response_status_if_headers_have_already_started(events, headers_sent):
    messages = []

    async def send(message):
        messages.append(message)

    async def application(_scope, _receive, send):
        if headers_sent:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        raise RuntimeError("private details")

    request_scope = scope()
    with pytest.raises(RuntimeError, match="private details"):
        asyncio.run(http.HttpTraceMiddleware(application)(request_scope, unused_receive, send))
    assert len(events) == 1
    assert events[0]["status_code"] == (200 if headers_sent else 500)
    assert events[0]["outcome"] == "error"
    assert events[0]["trace_id"] == request_scope["state"]["http_trace_id"]
    if headers_sent:
        assert dict(messages[0]["headers"])[b"x-trace-id"].decode() == events[0]["trace_id"]
    else:
        assert messages == []  # The outer ServerErrorMiddleware produces the 500.


def test_cancellation_propagates_without_claiming_an_unsent_http_response(events):
    async def application(_scope, _receive, _send):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(http.HttpTraceMiddleware(application)(scope(), unused_receive, unused_receive))
    assert len(events) == 1
    assert events[0]["outcome"] == "interrupted" and events[0]["status_code"] is None


@pytest.mark.parametrize("kind", ["lifespan", "websocket"])
def test_non_http_scopes_pass_through_untouched(events, monkeypatch, kind):
    request_scope = {"type": kind}
    seen = []
    monkeypatch.setattr(http, "uuid4", lambda: pytest.fail("non-HTTP must not allocate trace IDs"))

    async def application(value, receive, send):
        seen.append((value, receive, send))

    asyncio.run(http.HttpTraceMiddleware(application)(request_scope, unused_receive, unused_receive))
    assert seen == [(request_scope, unused_receive, unused_receive)]
    assert request_scope == {"type": kind} and events == []


def test_logger_failure_does_not_replace_the_response(monkeypatch):
    messages = []

    def broken_logger(_message):
        raise OSError("controlled broken sink")

    async def application(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        messages.append(message)

    monkeypatch.setattr(http.logger, "info", broken_logger)
    asyncio.run(http.HttpTraceMiddleware(application)(scope(), unused_receive, send))
    assert messages[0]["status"] == 204 and messages[1]["body"] == b""


def test_incomplete_stream_is_not_reported_as_completed_and_timing_covers_invocation(events, monkeypatch):
    times = iter([10.0, 10.25])
    monkeypatch.setattr(http, "perf_counter", lambda: next(times))

    async def application(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})

    async def send(_message):
        pass

    asyncio.run(http.HttpTraceMiddleware(application)(scope(), unused_receive, send))
    assert len(events) == 1
    assert events[0]["outcome"] == "incomplete" and events[0]["elapsed_ms"] == 250.0


@pytest.mark.parametrize("send_trailers,expected", [(False, "incomplete"), (True, "completed")])
def test_promised_trailers_must_finish_before_exchange_is_complete(events, send_trailers, expected):
    async def application(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [], "trailers": True})
        await send({"type": "http.response.body", "body": b"done"})
        if send_trailers:
            await send({"type": "http.response.trailers", "headers": []})

    async def send(_message):
        pass

    asyncio.run(http.HttpTraceMiddleware(application)(scope(), unused_receive, send))
    assert len(events) == 1 and events[0]["outcome"] == expected


def test_logger_configuration_is_visible_idempotent_and_does_not_change_root(monkeypatch):
    isolated = logging.Logger("isolated.http")
    root = logging.getLogger()
    root_before = (root.level, root.handlers[:])
    stream = StringIO()
    monkeypatch.setattr(http, "logger", isolated)
    monkeypatch.setattr("sys.stderr", stream)
    http.configure_http_logging()
    http.configure_http_logging()
    assert len(isolated.handlers) == 1
    assert isolated.level == logging.INFO and isolated.propagate is False
    assert (root.level, root.handlers) == root_before
    isolated.info('{"event":"controlled"}')
    assert stream.getvalue() == '{"event":"controlled"}\n'


def test_broken_stream_does_not_dump_a_logging_traceback(monkeypatch, capsys):
    class BrokenStream:
        def write(self, _message):
            raise OSError("controlled sink error")

    isolated = logging.Logger("isolated.http")
    monkeypatch.setattr(http, "logger", isolated)
    monkeypatch.setattr("sys.stderr", BrokenStream())
    http.configure_http_logging()
    isolated.info("controlled summary")
    assert capsys.readouterr() == ("", "")
