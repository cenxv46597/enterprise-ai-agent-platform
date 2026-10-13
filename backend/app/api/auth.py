"""注册、登录和当前用户资料接口。"""

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.security import (
    ACCESS_TOKEN_TTL_SECONDS,
    hash_password,
    issue_access_token,
    verify_password,
)
from app.db.models import User
from app.db.session import get_session


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _normalize_username(value: str) -> str:
    username = value.strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username) is None:
        raise ValueError("用户名须为 3–32 位小写字母、数字或下划线，且以字母开头")
    return username


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: SecretStr

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return _normalize_username(value)

    @field_validator("password")
    @classmethod
    def valid_password_length(cls, value: SecretStr) -> SecretStr:
        if not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("密码长度须为 12–128 个字符")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: SecretStr

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return _normalize_username(value)

    @field_validator("password")
    @classmethod
    def bounded_password_length(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("密码长度无效")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    created_at: datetime


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_TTL_SECONDS


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, session: DbSession) -> User:
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_users_username":
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        raise
    session.refresh(user)
    return user


@router.post("/login", response_model=TokenRead)
def login(payload: LoginRequest, request: Request, session: DbSession) -> TokenRead:
    with session.begin():
        user = session.scalar(select(User).where(User.username == payload.username))
        valid = verify_password(
            payload.password.get_secret_value(), user.password_hash if user else None
        )
        user_id = user.id if user is not None and valid else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    secret = getattr(request.app.state, "jwt_secret_key", None)
    if not isinstance(secret, str):
        raise RuntimeError("应用未配置 JWT 签名密钥")
    return TokenRead(access_token=issue_access_token(user_id, secret))


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user
