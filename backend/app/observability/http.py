"""Server-generated HTTP correlation IDs and minimal, safe access summaries."""

import json
import logging
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Literal
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.answer_errors import answer_error_code


logger = logging.getLogger("eaap.http")
TRACE_HEADER = "X-Trace-Id"
_HEADER = b"x-trace-id"
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"})
_CONFIGURATION_LOCK = Lock()
_SERVICE = "eaap-api"
_ENVIRONMENTS = frozenset({"local", "test", "production"})


class _SafeStreamHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # A broken log sink must neither break a business response nor dump errors.
        pass


def configure_http_logging() -> None:
    """Configure only this logger, once; leave root and existing domain logs alone."""
    with _CONFIGURATION_LOCK:
        if not any(isinstance(handler, _SafeStreamHandler) for handler in logger.handlers):
            handler = _SafeStreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def _write_summary(scope: Scope, trace_id: str, status_code: int | None,
                   elapsed_ms: float, outcome: str, app_env: str) -> None:
    try:
        # Route templates are application-owned. Never fall back to the incoming URL.
        route = scope.get("route")
        template = getattr(route, "path", None) or "<unmatched>"
        method = scope.get("method", "")
        payload = {
            # This is summary-recording time, not request arrival or business completion.
            "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "service": _SERVICE,
            "environment": app_env,
            "event": "http_request",
            "trace_id": trace_id,
            "method": method if method in _METHODS else "OTHER",
            "route": template,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "outcome": outcome,
        }
        code = answer_error_code(scope, status_code)
        if code is not None:
            payload["error_code"] = code
        logger.info(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    except Exception:
        # Summary delivery is best effort; no recursive logging or exception text.
        pass


class HttpTraceMiddleware:
    """Observe each HTTP invocation without reading its body or trusting headers.

    The outer ServerErrorMiddleware generates unhandled 500 responses, so pair
    this middleware with safe_unhandled_error to add the same ID to that response.
    """

    def __init__(self, app: ASGIApp, *,
                 app_env: Literal["local", "test", "production"] = "local") -> None:
        if app_env not in _ENVIRONMENTS:
            raise ValueError("Unsupported HTTP log environment")
        self.app = app
        self.app_env = app_env

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = str(uuid4())
        scope.setdefault("state", {})["http_trace_id"] = trace_id
        started = perf_counter()
        status_code = None
        finished = False
        trailers = False
        outcome = "incomplete"

        async def traced_send(message: Message) -> None:
            nonlocal status_code, finished, trailers
            kind = message["type"]
            if kind == "http.response.start":
                status_code = message["status"]
                trailers = message.get("trailers", False)
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() != _HEADER]
                message = {**message, "headers": headers + [(_HEADER, trace_id.encode("ascii"))]}
            await send(message)
            if kind == "http.response.body" and not message.get("more_body", False):
                finished = not trailers
            elif kind == "http.response.trailers" and not message.get("more_trailers", False):
                finished = True
            elif kind == "http.response.pathsend":
                finished = True

        try:
            await self.app(scope, receive, traced_send)
            if finished:
                outcome = "completed"
        except Exception:
            outcome = "error"
            if status_code is None:
                status_code = 500
            raise
        except BaseException:
            outcome = "interrupted"
            raise
        finally:
            elapsed_ms = round(max(0, perf_counter() - started) * 1000, 2)
            _write_summary(scope, trace_id, status_code, elapsed_ms, outcome, self.app_env)


async def safe_unhandled_error(request: Request, _exc: Exception) -> PlainTextResponse:
    """Keep Starlette's generic 500 body and attach the middleware's server ID.

    Starlette still re-raises the exception for the server; this does not sanitize
    Uvicorn tracebacks or existing domain loggers.
    """
    trace_id = request.scope.get("state", {}).get("http_trace_id")
    headers = {"Cache-Control": "no-store"}
    if trace_id is not None:
        headers[TRACE_HEADER] = trace_id
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)
