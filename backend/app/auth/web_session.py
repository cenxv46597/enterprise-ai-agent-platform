"""网页登录 Cookie 与显式来源校验；不续期或保存服务端会话。"""

from datetime import datetime, timezone
from time import time

from fastapi import HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


WEB_SESSION_COOKIE = "eaap_web_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def safe_web_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/v1/auth/web/"):
        return JSONResponse(status_code=422, content={"detail": "网页认证参数无效"},
                            headers={"Cache-Control": "no-store"})
    return await request_validation_exception_handler(request, exc)


def require_web_request(request: Request) -> None:
    expected = request.app.state.web_origin
    if request.headers.getlist("origin") != [expected] or request.headers.getlist("x-eaap-web") != ["1"]:
        raise HTTPException(403, "网页请求来源无效", headers={"Cache-Control": "no-store"})


def set_session_cookie(request: Request, response: Response, token: str, expires_at: int) -> None:
    response.set_cookie(
        WEB_SESSION_COOKIE, token,
        max_age=max(0, expires_at - int(time())),
        expires=datetime.fromtimestamp(expires_at, timezone.utc),
        path="/", httponly=True, samesite="strict", secure=request.app.state.web_cookie_secure,
    )


def delete_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        WEB_SESSION_COOKIE, path="/", httponly=True, samesite="strict",
        secure=request.app.state.web_cookie_secure,
    )
