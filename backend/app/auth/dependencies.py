"""显式 Bearer 优先，否则读取网页 Cookie；角色仍由当前成员关系决定。"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import InvalidAccessToken, decode_access_token_claims
from app.auth.web_session import SAFE_METHODS, WEB_SESSION_COOKIE, require_web_request
from app.db.models import User
from app.db.session import get_session


_BEARER = HTTPBearer(auto_error=False)


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="需要有效的登录凭证",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


def token_user(request: Request, token: str, session: Session) -> tuple[User, int]:
    secret = getattr(request.app.state, "jwt_secret_key", None)
    if not isinstance(secret, str):
        raise RuntimeError("应用未配置 JWT 签名密钥")
    try:
        claims = decode_access_token_claims(token, secret)
    except InvalidAccessToken:
        raise authentication_error() from None
    # 每次请求重新确认用户存在；短事务结束后再进入业务服务。
    with session.begin():
        user = session.get(User, claims.user_id)
        if user is not None:
            session.expunge(user)
    if user is None:
        raise authentication_error()
    return user, claims.expires_at


def cookie_user(request: Request, session: Session) -> tuple[User, int]:
    # 恢复页面身份时不接受 Bearer，避免恢复成另一个显式头部身份。
    if "authorization" in request.headers:
        raise authentication_error()
    token = request.cookies.get(WEB_SESSION_COOKIE)
    if not token:
        raise authentication_error()
    return token_user(request, token, session)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    if "authorization" in request.headers:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise authentication_error()
        return token_user(request, credentials.credentials, session)[0]
    token = request.cookies.get(WEB_SESSION_COOKIE)
    if not token:
        raise authentication_error()
    if request.method not in SAFE_METHODS:
        require_web_request(request)
    return token_user(request, token, session)[0]
