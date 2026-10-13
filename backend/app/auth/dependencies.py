"""从经过签名校验的 Bearer 令牌解析当前用户。"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import InvalidAccessToken, decode_access_token
from app.db.models import User
from app.db.session import get_session


_BEARER = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="需要有效的登录凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise error

    secret = getattr(request.app.state, "jwt_secret_key", None)
    if not isinstance(secret, str):
        raise RuntimeError("应用未配置 JWT 签名密钥")
    try:
        user_id = decode_access_token(credentials.credentials, secret)
    except InvalidAccessToken as exc:
        raise error from exc

    # 关闭读取事务后再进入资源服务，避免长时间持有认证查询的事务。
    with session.begin():
        user = session.get(User, user_id)
        if user is not None:
            # 某些测试会话默认在提交时使实例过期；预先分离以保持身份字段可读。
            session.expunge(user)
    if user is None:
        raise error
    return user
