"""HTTP correlation and safe request logs, without settings, database or models."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.responses import JSONResponse

from app.observability.http import (
    HttpTraceMiddleware,
    configure_http_logging,
    safe_unhandled_error,
)


TRACE_HEADER = "X-Trace-Id"
LOG_FIELDS = {
    "recorded_at", "service", "environment",
    "event", "trace_id", "method", "route", "status_code", "elapsed_ms", "outcome",
}


class RecordCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def request_logs():
    configure_http_logging()
    logger = logging.getLogger("eaap.http")
    collector = RecordCollector()
    logger.addHandler(collector)
    try:
        yield collector.records
    finally:
        logger.removeHandler(collector)


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(HttpTraceMiddleware)
    app.add_exception_handler(Exception, safe_unhandled_error)

    @app.get("/api/v1/items/{item_id}")
    async def item(item_id: str, request: Request):
        return {"item_id": item_id, "trace_id": request.state.http_trace_id}

    @app.get("/api/v1/reject/{status_code}")
    async def reject(status_code: int):
        raise HTTPException(status_code=status_code, detail="controlled public failure")

    @app.get("/api/v1/validate")
    async def validate(count: int):
        return {"count": count}

    @app.post("/api/v1/business-request")
    async def business(request: Request):
        return await request.json()

    @app.get("/api/v1/fail/{item_id}")
    async def fail(item_id: str):
        raise RuntimeError("ERROR_SENTINEL_should_not_appear_in_logs")

    return app


def event(record: logging.LogRecord) -> dict:
    message = record.getMessage()
    assert "\n" not in message and "\r" not in message
    value = json.loads(message)
    assert set(value) == LOG_FIELDS
    assert value["service"] == "eaap-api" and value["environment"] == "local"
    recorded = datetime.fromisoformat(value["recorded_at"])
    assert recorded.utcoffset() == timedelta(0) and value["recorded_at"].endswith("Z")
    assert value["event"] == "http_request"
    identifier = UUID(value["trace_id"])
    assert identifier.version == 4 and str(identifier) == value["trace_id"]
    assert isinstance(value["elapsed_ms"], (int, float))
    assert not isinstance(value["elapsed_ms"], bool) and value["elapsed_ms"] >= 0
    assert record.levelno == logging.INFO
    assert record.exc_info is None and record.stack_info is None
    return value


def assert_response_trace(response: httpx.Response) -> str:
    identifiers = response.headers.get_list(TRACE_HEADER)
    assert len(identifiers) == 1
    identifier = UUID(identifiers[0])
    assert identifier.version == 4 and str(identifier) == identifiers[0]
    return identifiers[0]


def test_success_has_one_server_trace_shared_by_state_header_and_log(request_logs):
    with TestClient(make_app()) as client:
        response = client.get("/api/v1/items/example")

    trace_id = assert_response_trace(response)
    assert response.status_code == 200
    assert response.json() == {"item_id": "example", "trace_id": trace_id}
    assert len(request_logs) == 1
    value = event(request_logs[0])
    assert {key: value[key] for key in ("trace_id", "method", "route", "status_code", "outcome")} == {
        "trace_id": trace_id, "method": "GET", "route": "/api/v1/items/{item_id}",
        "status_code": 200, "outcome": "completed",
    }


@pytest.mark.parametrize("path,method,status,route", [
    ("/api/v1/reject/401", "GET", 401, "/api/v1/reject/{status_code}"),
    ("/api/v1/reject/403", "GET", 403, "/api/v1/reject/{status_code}"),
    ("/api/v1/validate?count=not-an-integer", "GET", 422, "/api/v1/validate"),
    ("/api/v1/reject/503", "GET", 503, "/api/v1/reject/{status_code}"),
    ("/api/v1/unknown", "GET", 404, "<unmatched>"),
    ("/api/v1/validate", "DELETE", 405, "/api/v1/validate"),
])
def test_handled_http_failures_have_correlated_completed_logs(request_logs, path, method, status, route):
    with TestClient(make_app()) as client:
        response = client.request(method, path)

    assert response.status_code == status
    trace_id = assert_response_trace(response)
    assert len(request_logs) == 1
    value = event(request_logs[0])
    assert value["trace_id"] == trace_id
    assert (value["status_code"], value["route"], value["method"], value["outcome"]) == (
        status, route, method, "completed",
    )


@pytest.mark.parametrize("provided", [str(uuid4()), "not-a-valid-uuid"])
def test_inbound_trace_headers_are_ignored_and_each_http_request_gets_new_id(request_logs, provided):
    with TestClient(make_app()) as client:
        responses = [client.get("/api/v1/items/example", headers=[
            (TRACE_HEADER, provided), ("x-trace-id", "another-client-value"),
        ]) for _ in range(2)]

    identifiers = [assert_response_trace(response) for response in responses]
    assert len(set(identifiers)) == 2 and provided not in identifiers
    assert len(request_logs) == 2
    assert [event(record)["trace_id"] for record in request_logs] == identifiers


def test_trace_is_distinct_from_existing_business_request_id_and_body_is_unchanged(request_logs):
    body = {"request_id": str(uuid4()), "question": "controlled question", "nested": {"value": 3}}
    with TestClient(make_app()) as client:
        responses = [client.post("/api/v1/business-request", json=body) for _ in range(2)]

    assert all(response.status_code == 200 and response.json() == body for response in responses)
    identifiers = [assert_response_trace(response) for response in responses]
    assert len(set(identifiers)) == 2 and body["request_id"] not in identifiers
    assert len(request_logs) == 2
    assert [event(record)["trace_id"] for record in request_logs] == identifiers
    assert body["request_id"] not in "\n".join(record.getMessage() for record in request_logs)


def test_request_values_and_raw_exception_are_excluded_from_new_logs(request_logs):
    sentinels = [
        "PATH_SENTINEL", "QUERY_SENTINEL", "AUTH_SENTINEL", "COOKIE_SENTINEL",
        "BODY_SENTINEL", "ERROR_SENTINEL_should_not_appear_in_logs", "TRACE_SENTINEL",
    ]
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        client.get("/api/v1/items/PATH_SENTINEL?secret=QUERY_SENTINEL", headers={
            "Authorization": "Bearer AUTH_SENTINEL", "Cookie": "session=COOKIE_SENTINEL",
            TRACE_HEADER: "TRACE_SENTINEL",
        })
        client.post("/api/v1/business-request", json={"password": "BODY_SENTINEL"})
        client.get("/api/v1/missing/PATH_SENTINEL?secret=QUERY_SENTINEL")
        client.get("/api/v1/fail/PATH_SENTINEL")

    assert len(request_logs) == 4
    values = [event(record) for record in request_logs]
    assert [value["route"] for value in values] == [
        "/api/v1/items/{item_id}", "/api/v1/business-request", "<unmatched>", "/api/v1/fail/{item_id}",
    ]
    messages = "\n".join(record.getMessage() for record in request_logs)
    assert all(sentinel not in messages for sentinel in sentinels)


def test_unhandled_error_retains_safe_plaintext_response_and_same_trace(request_logs):
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fail/example")

    trace_id = assert_response_trace(response)
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert len(request_logs) == 1
    value = event(request_logs[0])
    assert value["trace_id"] == trace_id
    assert (value["status_code"], value["route"], value["outcome"]) == (
        500, "/api/v1/fail/{item_id}", "error",
    )


def test_response_cannot_add_another_trace_identifier(request_logs):
    app = make_app()

    @app.get("/api/v1/preexisting-header")
    async def preexisting_header():
        response = JSONResponse({"ok": True})
        response.raw_headers.extend([
            (b"x-trace-id", b"route-owned-value"),
            (b"X-Trace-Id", b"second-route-owned-value"),
        ])
        return response

    with TestClient(app) as client:
        response = client.get("/api/v1/preexisting-header")

    trace_id = assert_response_trace(response)
    assert response.json() == {"ok": True}
    assert len(request_logs) == 1 and event(request_logs[0])["trace_id"] == trace_id


def test_nonstandard_method_is_recorded_as_other_without_copying_input(request_logs):
    with TestClient(make_app()) as client:
        response = client.request("METHOD_SENTINEL", "/api/v1/items/example")

    trace_id = assert_response_trace(response)
    assert response.status_code == 405 and len(request_logs) == 1
    value = event(request_logs[0])
    assert value["method"] == "OTHER" and value["trace_id"] == trace_id
    assert "METHOD_SENTINEL" not in request_logs[0].getMessage()


def test_concurrent_requests_keep_distinct_state_header_and_log_identifiers(request_logs):
    app = make_app()
    count = 8
    reached = 0

    async def run_requests():
        ready = asyncio.Event()

        @app.get("/api/v1/concurrent/{number}")
        async def concurrent(number: int, request: Request):
            nonlocal reached
            reached += 1
            if reached == count:
                ready.set()
            await ready.wait()
            return {"number": number, "trace_id": request.state.http_trace_id}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://controlled.test") as client:
            return await asyncio.wait_for(asyncio.gather(*[
                client.get(f"/api/v1/concurrent/{number}") for number in range(count)
            ]), timeout=5)

    responses = asyncio.run(run_requests())
    identifiers = [assert_response_trace(response) for response in responses]
    assert reached == count and len(set(identifiers)) == count
    assert all(response.json() == {"number": number, "trace_id": identifiers[number]}
               for number, response in enumerate(responses))
    assert len(request_logs) == count
    values = {value["trace_id"]: value for value in map(event, request_logs)}
    assert set(values) == set(identifiers)
    assert all(value["route"] == "/api/v1/concurrent/{number}"
               and value["status_code"] == 200 and value["outcome"] == "completed"
               for value in values.values())
