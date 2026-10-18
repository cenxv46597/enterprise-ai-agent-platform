"""用临时身份验证本机 eaap_test；不读取 .env、既有密码或日常数据库。

从仓库根目录用 .venv 中的 Python 执行，可用 --docker-exe 指定 Desktop 自带 CLI。
仅管理原项目容器内的 eaap_test，测试数据位于本次角色拥有的随机 schema。
默认验证工作流记录；--suite readiness 仅验证数据库就绪探测。
"""

import argparse
from base64 import b64encode
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
import hmac
from io import StringIO
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "enterprise_ai_agent_platform-db-1"
ROLE_PATTERN = re.compile(r"eaap_check_[0-9a-f]{32}")
SCHEMA_PATTERN = re.compile(r"eaap_test_[0-9a-f]{32}")


def scram_verifier(password: str) -> str:
    # Generated hex is ASCII, so SASLprep normalization cannot change it.
    # Format: PostgreSQL 17 catalog-pg-authid; keys: RFC5802 §3 / RFC7677.
    salt = secrets.token_bytes(16)
    salted = pbkdf2_hmac("sha256", password.encode("ascii"), salt, 4096)
    client = hmac.digest(salted, b"Client Key", "sha256")
    stored = sha256(client).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    encode = lambda value: b64encode(value).decode("ascii")
    return f"SCRAM-SHA-256$4096:{encode(salt)}${encode(stored)}:{encode(server)}"


def admin_sql(docker: str, sql: str) -> str:
    result = subprocess.run(
        [docker, "exec", "-i", CONTAINER, "psql", "-X", "-U", "eaap", "-d", "eaap_test",
         "-v", "ON_ERROR_STOP=1", "-Atq"],
        input="SET statement_timeout='10s'; SET lock_timeout='5s'; " + sql,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode:
        # Do not propagate SQL, connection strings, verifier or server diagnostics.
        raise RuntimeError("测试库管理命令未确认")
    return result.stdout.strip()


def cleanup_role(docker: str, role: str) -> None:
    if not ROLE_PATTERN.fullmatch(role):
        raise RuntimeError("临时身份名称不符合本轮范围")
    if admin_sql(docker, f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") == "0":
        return
    admin_sql(docker, f'ALTER ROLE "{role}" NOLOGIN;')
    admin_sql(docker, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
              f"WHERE usename='{role}' AND datname='eaap_test' AND pid<>pg_backend_pid();")
    names = admin_sql(docker, "SELECT n.nspname FROM pg_namespace n JOIN pg_roles r "
                      f"ON n.nspowner=r.oid WHERE r.rolname='{role}' ORDER BY n.nspname;")
    schemas = names.splitlines() if names else []
    if any(not SCHEMA_PATTERN.fullmatch(name) for name in schemas):
        raise RuntimeError("发现范围外归属对象，停止自动清理")
    # Both role ownership and the full random namespace pattern were checked above.
    for schema in schemas:
        admin_sql(docker, f'DROP SCHEMA "{schema}" CASCADE;')
    admin_sql(docker, f'REVOKE CREATE, CONNECT ON DATABASE eaap_test FROM "{role}"; '
              f'DROP ROLE "{role}";')
    if admin_sql(docker, f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") != "0":
        raise RuntimeError("临时身份清理未确认")


class Results:
    collected = 0
    passed = 0
    skipped = 0

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed += 1
        if report.skipped:
            self.skipped += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-exe", default=shutil.which("docker") or "docker")
    parser.add_argument("--suite", choices=("workflow-history", "readiness"), default="workflow-history")
    arguments = parser.parse_args()
    test_file, expected_count = {
        "workflow-history": ("test_answer_workflow_listing_db.py", 14),
        "readiness": ("test_readiness_db.py", 3),
    }[arguments.suite]
    role = f"eaap_check_{uuid4().hex}"
    password = secrets.token_hex(32)
    buffer = StringIO()
    reserved = False
    result = 1
    cleanup_ok = True
    try:
        if admin_sql(arguments.docker_exe, "SELECT current_database();") != "eaap_test":
            raise RuntimeError("服务器实际数据库不是独立测试库")
        if admin_sql(arguments.docker_exe, f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';") != "0":
            raise RuntimeError("临时身份名称已占用")
        # Mark the name before sending DDL: an interrupted response can still have committed.
        reserved = True
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        verifier = scram_verifier(password)
        privileges = "CONNECT, CREATE" if arguments.suite == "workflow-history" else "CONNECT"
        admin_sql(arguments.docker_exe, "BEGIN; "
                  f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                  f"NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 4 PASSWORD '{verifier}' "
                  f"VALID UNTIL '{expires}'; "
                  f'GRANT {privileges} ON DATABASE eaap_test TO "{role}"; COMMIT;')
        print("目标已确认：本机独立 eaap_test，临时测试身份；不读取已有凭据。")
        # Only this process receives its temporary URL. Nothing is saved to a credential file.
        for name in list(os.environ):
            if name.startswith("EAAP_"):
                del os.environ[name]
        sys.path.insert(0, str(ROOT / "backend"))
        from sqlalchemy.engine import URL
        from app.core.config import Settings
        Settings.model_config["env_file"] = None
        os.environ["EAAP_APP_ENV"] = "test"
        os.environ["EAAP_TEST_DATABASE_URL"] = URL.create(
            "postgresql+psycopg", username=role, password=password,
            host="127.0.0.1", port=5432, database="eaap_test",
        ).render_as_string(hide_password=False)
        os.environ.pop("PYTEST_ADDOPTS", None)
        os.environ.pop("PYTEST_PLUGINS", None)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        import pytest
        audit = Results()
        with redirect_stdout(buffer), redirect_stderr(buffer):
            result = int(pytest.main([
                "-q", "-p", "no:cacheprovider", "--tb=short",
                str(ROOT / "backend/tests" / test_file),
            ], plugins=[audit]))
        if audit.collected != expected_count or audit.passed != audit.collected or audit.skipped:
            result = 1
    except (Exception, KeyboardInterrupt) as failure:
        print(f"验证未完成（{type(failure).__name__}）；未输出连接字符串或管理SQL。")
        result = 1
    finally:
        if reserved:
            try:
                cleanup_role(arguments.docker_exe, role)
                print("本次临时身份及其测试区域已清理并核对。")
            except Exception:
                cleanup_ok = False
                print(f"清理未确认，请核查临时身份 {role}；勿删除其他测试区域或重建数据库。")
        os.environ.pop("EAAP_TEST_DATABASE_URL", None)
    print(buffer.getvalue().replace(password, "[REDACTED]"), end="")
    return result if cleanup_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
