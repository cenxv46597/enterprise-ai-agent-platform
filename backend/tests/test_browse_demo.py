"""本机浏览 CLI 的认证、资源范围和安全输出；HTTP 全部由 MockTransport 拦截。"""

import importlib.util
import json
from getpass import GetPassWarning
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID
import warnings

import httpx
import pytest


PASSWORD = "private-cli-test-password"
TOKEN = "private.cli-test-token_123"
PRIVATE = "PRIVATE-RAW-RESPONSE"
USER_ID = str(UUID(int=1))
WORKSPACE_A, WORKSPACE_B = str(UUID(int=11)), str(UUID(int=12))
KB_ONE, KB_TWO = str(UUID(int=21)), str(UUID(int=22))
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
WORKSPACES = "/api/v1/workspaces"
BASES = f"{WORKSPACES}/{WORKSPACE_B}/knowledge-bases"
DOCUMENTS = f"{BASES}/{KB_TWO}/documents"


@pytest.fixture
def browse(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    path = Path(__file__).resolve().parents[1] / "scripts/browse_demo.py"
    spec = importlib.util.spec_from_file_location("controlled_browse_demo", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    state = SimpleNamespace(
        module=module, requests=[], client_options=[], choices=iter(["2", "2"]),
        password_calls=0, input_calls=0, failure=None,
        payloads={
            LOGIN: {"access_token": TOKEN, "token_type": "bearer", "expires_in": 1800},
            ME: {"id": USER_ID, "username": "demo_a"},
            WORKSPACES: [{"id": WORKSPACE_A, "name": "甲公司"}, {"id": WORKSPACE_B, "name": "乙公司"}],
            BASES: [
                {"id": KB_ONE, "workspace_id": WORKSPACE_B, "name": "第一个知识库"},
                {"id": KB_TWO, "workspace_id": WORKSPACE_B, "name": "第二个知识库"},
            ],
            DOCUMENTS: [
                {"id": str(UUID(int=100 + index)), "knowledge_base_id": KB_TWO,
                 "original_filename": f"资料{index}.txt", "status": status}
                for index, status in enumerate(["uploaded", "processing", "parsed", "ready", "failed"])
            ],
        },
    )
    monkeypatch.setattr(module, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stderr=SimpleNamespace(isatty=lambda: True),
    ))

    def hidden_input(prompt):
        state.password_calls += 1
        return PASSWORD

    def choice_input(prompt):
        state.input_calls += 1
        return next(state.choices)

    monkeypatch.setattr(module, "getpass", hidden_input)
    monkeypatch.setattr(module, "input", choice_input, raising=False)

    def handler(request):
        state.requests.append(request)
        if request.url.path not in state.payloads:
            pytest.fail("请求了未经选择的范围或额外接口")
        if request.url.path == LOGIN:
            if state.failure == "401":
                return httpx.Response(401, text=f"{PRIVATE} {PASSWORD} {TOKEN}")
            if state.failure == "redirect":
                return httpx.Response(307, headers={"Location": "http://remote.example/unsafe"}, text=PRIVATE)
            if state.failure == "network":
                raise httpx.ReadError(f"{PRIVATE} {PASSWORD} {TOKEN}", request=request)
            if state.failure == "timeout":
                raise httpx.ReadTimeout(f"{PRIVATE} {PASSWORD} {TOKEN}", request=request)
            if state.failure == "connect":
                raise httpx.ConnectError(f"{PRIVATE} {PASSWORD} {TOKEN}", request=request)
            if state.failure == "bad_json":
                return httpx.Response(200, text=f"{PRIVATE} {PASSWORD} {TOKEN}")
            if state.failure == "bad_structure":
                return httpx.Response(200, json={"unexpected": f"{PRIVATE} {PASSWORD} {TOKEN}"})
        return httpx.Response(200, json=state.payloads[request.url.path])

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        state.client_options.append(kwargs.copy())
        assert kwargs["timeout"] == 10
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real_client(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module.httpx, "Client", client_factory)
    return state


def safe_output(capsys: pytest.CaptureFixture) -> str:
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert PASSWORD not in output and TOKEN not in output and PRIVATE not in output
    assert "\x1b" not in output
    return output


def test_number_two_selection_uses_http_identity_scope_and_pagination(browse, capsys) -> None:
    browse.payloads[WORKSPACES][1]["name"] = "乙\n公司\x1b"
    browse.payloads[DOCUMENTS][0]["original_filename"] = f"资料{PASSWORD}\n{TOKEN}\x1b.txt"
    assert browse.module.main(["--username", " DEMO_A "]) == 0
    assert [(request.method, request.url.path) for request in browse.requests] == [
        ("POST", LOGIN), ("GET", ME), ("GET", WORKSPACES), ("GET", BASES), ("GET", DOCUMENTS),
    ]
    login, *business = browse.requests
    assert json.loads(login.content) == {"username": "demo_a", "password": PASSWORD}
    assert "Authorization" not in login.headers
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in business)
    assert all(dict(request.url.params) == {"limit": "20", "offset": "0"} for request in business[1:])
    assert browse.client_options[0]["base_url"] == "http://127.0.0.1:8000"
    assert browse.input_calls == 2
    output = safe_output(capsys)
    assert "乙\n公司" not in output and "乙\\n公司" in output
    assert "[已隐藏]" in output
    assert all(label in output for label in ("已上传", "处理中", "已解析", "可检索", "处理失败"))


@pytest.mark.parametrize("path,request_count,input_count", [(WORKSPACES, 3, 0), (BASES, 4, 1), (DOCUMENTS, 5, 2)])
def test_empty_resource_lists_finish_normally(browse, capsys, path, request_count, input_count) -> None:
    browse.payloads[path] = []
    assert browse.module.main(["--username", "demo_a"]) == 0
    assert len(browse.requests) == request_count and browse.input_calls == input_count
    assert "列表为空" in safe_output(capsys)


def test_full_first_page_is_not_claimed_as_total_and_is_not_followed(browse, capsys) -> None:
    browse.payloads[WORKSPACES].extend(
        {"id": str(UUID(int=200 + index)), "name": f"其他空间{index}"} for index in range(18)
    )
    assert browse.module.main(["--username", "demo_a"]) == 0
    assert len(browse.requests) == 5
    assert all(request.url.params.get("offset", "0") == "0" for request in browse.requests)
    output = safe_output(capsys)
    assert "第一页" in output and "可能还有更多" in output


@pytest.mark.parametrize("failure", ["401", "redirect", "network", "timeout", "connect", "bad_json", "bad_structure"])
def test_http_and_response_failures_stop_without_private_output(browse, capsys, failure) -> None:
    browse.failure = failure
    assert browse.module.main(["--username", "demo_a"]) == 1
    assert len(browse.requests) == 1 and browse.input_calls == 0
    output = safe_output(capsys)
    if failure == "timeout":
        assert output.strip() == "登录等待后端响应超时；请检查 Docker 数据库是否已启动，并查看后端日志。"
    elif failure == "connect":
        assert output.strip() == "登录无法连接后端；请核对后端地址、端口和启动状态。"


@pytest.mark.parametrize("problem,request_count", [("identity", 2), ("knowledge_parent", 4), ("document_parent", 5)])
def test_identity_or_parent_mismatch_stops_browsing(browse, capsys, problem, request_count) -> None:
    if problem == "identity":
        browse.payloads[ME]["username"] = "another_user"
    elif problem == "knowledge_parent":
        browse.payloads[BASES][0]["workspace_id"] = WORKSPACE_A
    else:
        browse.payloads[DOCUMENTS][0]["knowledge_base_id"] = KB_ONE
    assert browse.module.main(["--username", "demo_a"]) == 1
    assert len(browse.requests) == request_count
    safe_output(capsys)


def test_each_non_tty_stream_is_rejected_before_password_or_network(browse, capsys) -> None:
    for stream in ("stdin", "stderr"):
        browse.module.sys.stdin.isatty = lambda: True
        browse.module.sys.stderr.isatty = lambda: True
        getattr(browse.module.sys, stream).isatty = lambda: False
        assert browse.module.main(["--username", "demo_a"]) == 2
    assert browse.password_calls == 0 and browse.client_options == [] and browse.requests == []
    safe_output(capsys)


def test_getpass_warning_aborts_before_visible_fallback(browse, monkeypatch, capsys) -> None:
    def warned_input(prompt):
        warnings.warn("input may be visible", GetPassWarning)
        pytest.fail("警告后不得继续可见输入")

    monkeypatch.setattr(browse.module, "getpass", warned_input)
    assert browse.module.main(["--username", "demo_a"]) == 2
    assert browse.client_options == [] and browse.requests == []
    safe_output(capsys)


@pytest.mark.parametrize("interruption", [EOFError(PASSWORD), KeyboardInterrupt()])
def test_password_input_cancellation_makes_no_request(browse, monkeypatch, capsys, interruption) -> None:
    def interrupted_input(prompt):
        raise interruption

    monkeypatch.setattr(browse.module, "getpass", interrupted_input)
    assert browse.module.main(["--username", "demo_a"]) == 2
    assert browse.client_options == [] and browse.requests == []
    safe_output(capsys)


def test_unsafe_origin_is_rejected_without_echo_or_password_input(browse, capsys) -> None:
    unsafe = f"http://user:{PASSWORD}@127.0.0.1:8000"
    assert browse.module.main(["--username", "demo_a", "--base-url", unsafe]) == 2
    assert browse.password_calls == 0 and browse.client_options == [] and browse.requests == []
    safe_output(capsys)


def test_unknown_password_argument_is_not_echoed_or_consumed(browse, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        browse.module.main(["--username", "demo_a", "--password", PASSWORD])
    assert error.value.code == 2
    assert browse.password_calls == 0 and browse.client_options == [] and browse.requests == []
    safe_output(capsys)


def test_invalid_selection_stops_before_child_request(browse, capsys) -> None:
    browse.choices = iter(["0"])
    assert browse.module.main(["--username", "demo_a"]) == 1
    assert len(browse.requests) == 3
    safe_output(capsys)
