"""本机注册入口的安全输出与前置检查；全部使用替身，不连接数据库。"""

import warnings
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.auth import register_local


PASSWORD = "private-test-password-1234"
TOKEN = "private-test-token"


@pytest.fixture
def local_cli(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        calls=[],
        inputs=iter([PASSWORD, PASSWORD]),
        settings=SimpleNamespace(app_env="local", database_url="postgresql://localhost/eaap"),
        status=201,
        body={"id": str(uuid4()), "username": "demo_a", "password": PASSWORD, "access_token": TOKEN},
        error=None,
    )
    monkeypatch.setattr(register_local, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stderr=SimpleNamespace(isatty=lambda: True),
    ))
    monkeypatch.setattr(register_local, "Settings", lambda: state.settings)
    monkeypatch.setattr(register_local, "getpass", lambda prompt: next(state.inputs))
    monkeypatch.setattr(register_local, "_migration_ready", lambda: True)
    monkeypatch.setattr(register_local, "_create_app", lambda: object())

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, path: str, *, json: dict):
            state.calls.append((path, json))
            if state.error is not None:
                raise state.error
            return SimpleNamespace(status_code=state.status, json=lambda: state.body)

    monkeypatch.setattr(register_local, "TestClient", lambda app: FakeClient())
    return state


def assert_safe_output(capsys: pytest.CaptureFixture) -> str:
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert PASSWORD not in output
    assert TOKEN not in output
    return output


@pytest.mark.parametrize("stream", ["stdin", "stderr"])
def test_non_tty_rejected_before_password_or_request(local_cli, monkeypatch, capsys, stream) -> None:
    getattr(register_local.sys, stream).isatty = lambda: False
    monkeypatch.setattr(register_local, "getpass", lambda prompt: pytest.fail("不能读取密码"))
    assert register_local.main(["--username", "demo_a"]) == 2
    assert local_cli.calls == []
    assert "交互终端" in assert_safe_output(capsys)


def test_getpass_warning_aborts_before_visible_fallback(local_cli, monkeypatch, capsys) -> None:
    def warned_input(prompt):
        warnings.warn("password may be echoed", register_local.GetPassWarning)
        pytest.fail("警告后不能继续读取可见密码")

    monkeypatch.setattr(register_local, "getpass", warned_input)
    assert register_local.main(["--username", "demo_a"]) == 2
    assert local_cli.calls == []
    assert "中止" in assert_safe_output(capsys)


@pytest.mark.parametrize("error", [EOFError(PASSWORD), KeyboardInterrupt()])
def test_interrupted_input_creates_nothing(local_cli, monkeypatch, capsys, error) -> None:
    def interrupted_input(prompt):
        raise error

    monkeypatch.setattr(register_local, "getpass", interrupted_input)
    assert register_local.main(["--username", "demo_a"]) == 2
    assert local_cli.calls == []
    assert_safe_output(capsys)


@pytest.mark.parametrize("passwords", [(PASSWORD, "different-password"), ("short", "short"), ("x" * 129, "x" * 129)])
def test_mismatched_or_invalid_password_prevents_request(local_cli, capsys, passwords) -> None:
    local_cli.inputs = iter(passwords)
    assert register_local.main(["--username", "demo_a"]) == 2
    assert local_cli.calls == []
    assert_safe_output(capsys)


def test_invalid_username_prevents_request(local_cli, capsys) -> None:
    assert register_local.main(["--username", "invalid username"]) == 2
    assert local_cli.calls == []
    assert_safe_output(capsys)


@pytest.mark.parametrize("database_url", [
    "postgresql://localhost/eaap",
    "postgresql+psycopg://127.0.0.1/eaap",
    "postgresql://[::1]/eaap",
])
def test_success_outputs_only_validated_username_and_uuid(local_cli, capsys, database_url) -> None:
    local_cli.settings.database_url = database_url
    assert register_local.main(["--username", " DEMO_A "]) == 0
    assert local_cli.calls == [("/api/v1/auth/register", {"username": "demo_a", "password": PASSWORD})]
    output = assert_safe_output(capsys)
    assert output == f"已注册用户 demo_a，ID：{local_cli.body['id']}\n"


@pytest.mark.parametrize("app_env,database_url", [
    ("production", "postgresql://localhost/eaap"),
    ("test", "postgresql://localhost/eaap"),
    ("local", "postgresql://remote.example/eaap"),
    ("local", "postgresql://localhost/eaap_test"),
    ("local", "sqlite:///eaap"),
    ("local", "postgresql://localhost/eaap?host=remote.example"),
    ("local", "postgresql://localhost/eaap?dbname=another"),
    ("local", None),
])
def test_scope_guard_prevents_password_and_request(local_cli, monkeypatch, capsys, app_env, database_url) -> None:
    local_cli.settings.app_env = app_env
    local_cli.settings.database_url = database_url
    monkeypatch.setattr(register_local, "getpass", lambda prompt: pytest.fail("不能读取密码"))
    assert register_local.main(["--username", "demo_a"]) == 2
    assert local_cli.calls == []
    assert_safe_output(capsys)


def test_unready_migration_prevents_request(local_cli, monkeypatch, capsys) -> None:
    monkeypatch.setattr(register_local, "_migration_ready", lambda: False)
    monkeypatch.setattr(register_local, "getpass", lambda prompt: pytest.fail("不能读取密码"))
    assert register_local.main(["--username", "demo_a"]) == 2
    assert local_cli.calls == []
    assert "0010" in assert_safe_output(capsys)


@pytest.mark.parametrize("status", [409, 422, 500])
def test_error_responses_are_not_printed(local_cli, capsys, status) -> None:
    local_cli.status = status
    assert register_local.main(["--username", "demo_a"]) == 1
    assert len(local_cli.calls) == 1
    assert_safe_output(capsys)


def test_request_exception_is_not_printed(local_cli, capsys) -> None:
    local_cli.error = RuntimeError(f"payload contains {PASSWORD} and {TOKEN}")
    assert register_local.main(["--username", "demo_a"]) == 1
    assert len(local_cli.calls) == 1
    assert_safe_output(capsys)


def test_unexpected_password_argument_is_not_echoed(local_cli, capsys) -> None:
    assert register_local.main(["--username", "demo_a", "--password", PASSWORD]) == 2
    assert local_cli.calls == []
    assert_safe_output(capsys)
