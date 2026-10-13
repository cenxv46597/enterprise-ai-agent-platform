"""密码哈希与有界签名访问令牌。"""

from datetime import datetime, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError


ACCESS_TOKEN_TTL_SECONDS = 30 * 60
TOKEN_ISSUER = "enterprise-ai-agent-platform"
_PASSWORD_HASH = PasswordHash.recommended()
# 未知用户名也执行一次密码校验，避免登录耗时直接暴露用户名是否存在。
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash("not-a-real-user-password")


class InvalidAccessToken(ValueError):
    """令牌签名、身份或有效期无效。"""


def hash_password(password: str) -> str:
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _PASSWORD_HASH.verify(password, password_hash or _DUMMY_PASSWORD_HASH)
    except UnknownHashError:
        return False


def _timestamp(now: datetime | None) -> int:
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("时间必须带时区")
    return int(current.timestamp())


def _check_secret(secret: str) -> None:
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("JWT 签名密钥至少需要 32 字节")


def issue_access_token(user_id: UUID, secret: str, *, now: datetime | None = None) -> str:
    """只签发用户 ID；工作空间角色始终以数据库当前关系为准。"""
    _check_secret(secret)
    issued_at = _timestamp(now)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": TOKEN_ISSUER,
            "iat": issued_at,
            "exp": issued_at + ACCESS_TOKEN_TTL_SECONDS,
        },
        secret,
        algorithm="HS256",
    )


def decode_access_token(token: str, secret: str, *, now: datetime | None = None) -> UUID:
    """固定算法、签发者与期限；测试可注入时间检查过期边界。"""
    _check_secret(secret)
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=TOKEN_ISSUER,
            options={
                "require": ["sub", "iss", "iat", "exp"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        issued_at = claims["iat"]
        expires_at = claims["exp"]
        subject = claims["sub"]
        current = _timestamp(now)
        if (
            type(issued_at) is not int
            or type(expires_at) is not int
            or not isinstance(subject, str)
            or issued_at > current
            or expires_at <= current
            or not 0 < expires_at - issued_at <= ACCESS_TOKEN_TTL_SECONDS
        ):
            raise InvalidAccessToken("访问令牌无效或已过期")
        user_id = UUID(subject)
        if str(user_id) != subject:
            raise InvalidAccessToken("访问令牌用户身份无效")
        return user_id
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise InvalidAccessToken("访问令牌无效或已过期") from exc
