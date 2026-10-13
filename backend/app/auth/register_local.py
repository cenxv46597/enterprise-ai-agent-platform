"""在本机交互终端隐藏输入密码，调用现有 HTTP 路由注册用户。"""

import argparse
import sys
import warnings
from getpass import GetPassWarning, getpass
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.api.auth import RegisterRequest
from app.core.config import Settings
from app.db.session import get_engine


def _local_database(settings: Settings) -> bool:
    url = make_url(settings.database_url or "")
    return (
        settings.app_env == "local"
        and url.get_backend_name() == "postgresql"
        and (url.host or "").lower() in {"localhost", "127.0.0.1", "::1"}
        and url.database == "eaap"
        # 查询参数可能覆盖 PostgreSQL 的 host/dbname，不允许绕过本机范围。
        and not url.query
    )


def _migration_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            versions = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
        return versions == ["20261013_0010"]
    except Exception:
        return False


def _create_app() -> FastAPI:
    # app.main 导入时也会创建应用，配置异常必须处于 main 的安全捕获范围内。
    from app.main import create_app

    return create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机隐藏输入密码注册用户", allow_abbrev=False)
    parser.add_argument("--username", required=True)
    args, extra = parser.parse_known_args(argv)
    if extra:
        print("参数无效：仅接受 --username。")
        return 2
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("请在本机交互终端运行；不接受可见或重定向的密码输入。")
        return 2
    try:
        allowed = _local_database(Settings())
    except Exception:
        allowed = False
    if not allowed:
        print("仅允许 local 环境连接本机 loopback PostgreSQL 的 eaap 数据库。")
        return 2
    try:
        if not _migration_ready():
            print("无法确认 0010 已就绪；请先核对受控迁移和本机数据库连接。")
            return 2
    except KeyboardInterrupt:
        print("操作已中止；没有注册用户。")
        return 2

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("密码（12–128 个字符，输入隐藏）：")
            confirmation = getpass("再次输入密码：")
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        print("输入已中止；没有注册用户。")
        return 2
    except Exception:
        print("无法安全读取密码；没有注册用户。")
        return 2
    if password != confirmation:
        print("两次密码不一致；没有注册用户。")
        return 2
    try:
        payload = RegisterRequest(username=args.username, password=password)
    except ValidationError:
        print("用户名或密码格式无效；用户名为 3–32 位，密码为 12–128 个字符。")
        return 2
    del password, confirmation

    try:
        with TestClient(_create_app()) as client:
            response = client.post("/api/v1/auth/register", json={
                "username": payload.username,
                "password": payload.password.get_secret_value(),
            })
        if response.status_code == 201:
            account = response.json()
            user_id = UUID(account["id"])
            if account["username"] != payload.username:
                raise ValueError("unexpected registration response")
            print(f"已注册用户 {payload.username}，ID：{user_id}")
            return 0
        if response.status_code == 409:
            print("用户名已存在；没有创建新用户。")
        elif response.status_code == 422:
            print("注册参数未通过校验；没有创建新用户。")
        else:
            print("注册未确认完成；请检查本机应用状态。")
    except (EOFError, KeyboardInterrupt):
        print("操作已中止；请核对用户是否已经创建。")
    except Exception:
        # 请求或配置错误可能带有密码、连接地址，绝不输出异常详情或响应全文。
        print("注册未确认完成；请检查本机应用状态。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
