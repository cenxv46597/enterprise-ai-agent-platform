"""固定请求正文重放的边界测试；全部使用模拟 HTTP。"""

from copy import deepcopy
import json
import traceback

import httpx
from pydantic import SecretStr
import pytest

import app.providers.compatible_chat as chat_module
from app.providers.compatible_chat import CompatibleJsonChatClient, JsonChatError, JsonChatTimeoutError


MODEL = "controlled-generation"
SECRET = "controlled-replay-test-key"
USER_CONTENT = '\n{ "sources" : [{ "content": "健康检查通过后再恢复导入。", "source_id": 1 }],\n "question" : "何时恢复？" }\n'


def request_body():
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "只使用资料回答，输出 JSON。"},
            {"role": "user", "content": USER_CONTENT},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
    }


def response():
    return httpx.Response(200, json={
        "model": "controlled-generation-snapshot",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"answer":"健康检查通过后恢复。"}',
        }}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def configured(handler, **options):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = CompatibleJsonChatClient(
        "https://replay.example/v1", SecretStr(SECRET), MODEL, client=client, **options,
    )
    return adapter, client


@pytest.mark.parametrize("temperature", [None, 0, 0.7])
def test_replay_preserves_user_content_and_exact_explicit_body(temperature):
    body = request_body()
    if temperature is not None:
        body["temperature"] = temperature
    original = deepcopy(body)
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload == original
        assert payload["messages"][1]["content"] == USER_CONTENT
        assert request.headers["Authorization"] == "Bearer " + SECRET
        return response()

    adapter, client = configured(handler)
    with client:
        result = adapter.complete_request(body)
        assert not client.is_closed
    assert len(calls) == 1
    assert body == original
    assert result.model == "controlled-generation-snapshot"
    assert result.usage["total_tokens"] == 15


def test_observer_runs_before_http_and_cannot_mutate_replayed_body():
    body = request_body()
    original = deepcopy(body)
    events = []

    def observe(value):
        events.append("观察")
        assert value == original
        assert SECRET not in json.dumps(value, ensure_ascii=False)
        value["messages"][1]["content"] = "回调修改的资料"
        value["response_format"]["type"] = "text"

    def handler(request):
        events.append("发送")
        assert json.loads(request.content) == original
        return response()

    adapter, client = configured(handler, request_observer=observe)
    with client:
        adapter.complete_request(body)
    assert events == ["观察", "发送"]
    assert body == original


def test_original_input_is_copied_before_observer_mutates_callers_object():
    body = request_body()
    original = deepcopy(body)

    def observe(value):
        body["messages"][1]["content"] = "调用方的并发修改"
        assert value == original

    def handler(request):
        assert json.loads(request.content) == original
        return response()

    adapter, client = configured(handler, request_observer=observe)
    with client:
        adapter.complete_request(body)


INVALID_CHANGES = [
    lambda body: body.update(Authorization=SECRET),
    lambda body: body.update(model="different-model"),
    lambda body: body.update(model=1),
    lambda body: body.update(max_tokens=True),
    lambda body: body.update(max_tokens=1999),
    lambda body: body.update(max_tokens=2000.0),
    lambda body: body.update(stream=True),
    lambda body: body.update(stream=0),
    lambda body: body.update(response_format={"type": "json_object", "extra": SECRET}),
    lambda body: body.update(response_format={"type": "text"}),
    lambda body: body.update(messages=body["messages"][:1]),
    lambda body: body.update(messages=tuple(body["messages"])),
    lambda body: body["messages"][0].update(role="user"),
    lambda body: body["messages"][1].update(role="system"),
    lambda body: body["messages"][0].update(content=" "),
    lambda body: body["messages"][1].update(content=None),
    lambda body: body["messages"][1].update(extra=SECRET),
    lambda body: body.pop("max_tokens"),
    *[lambda body, value=value: body.update(temperature=value) for value in (True, None, "0", -0.1, 2.1, float("nan"), float("inf"))],
]


@pytest.mark.parametrize("change", INVALID_CHANGES)
def test_invalid_replay_does_not_observe_or_send_and_hides_input(change):
    body = request_body()
    change(body)
    original = deepcopy(body)
    adapter, client = configured(
        lambda request: pytest.fail("非法重放不应请求模型"),
        request_observer=lambda value: pytest.fail("非法重放不应进入快照回调"),
    )
    with client, pytest.raises(JsonChatError) as caught:
        adapter.complete_request(body)
    assert str(caught.value) == "模型重放请求不符合约定格式。"
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    # NaN 不与自身相等；逐字段串行化比较避免把这一性质误认为输入被改写。
    assert json.dumps(body, ensure_ascii=False) == json.dumps(original, ensure_ascii=False)


@pytest.mark.parametrize("body", [None, [], "请求正文"])
def test_non_object_replay_is_a_safe_preflight_failure(body):
    adapter, client = configured(lambda request: pytest.fail("非法正文不应发送"))
    with client, pytest.raises(JsonChatError, match="模型重放请求不符合约定格式"):
        adapter.complete_request(body)


def test_observer_failure_prevents_http_and_hides_callback_error():
    def observe(value):
        raise OSError(SECRET)

    adapter, client = configured(
        lambda request: pytest.fail("记录失败必须阻止发送"), request_observer=observe,
    )
    with client, pytest.raises(JsonChatError, match="快照保存失败") as caught:
        adapter.complete_request(request_body())
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_observer_time_is_included_in_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(chat_module, "monotonic", lambda: clock[0])

    def observe(value):
        clock[0] = 61

    adapter, client = configured(
        lambda request: pytest.fail("记录超过期限时不应发送"), request_observer=observe,
    )
    with client, pytest.raises(JsonChatTimeoutError):
        adapter.complete_request(request_body())


def test_replayed_timeout_is_one_attempt_without_retry_or_secret_in_error():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    adapter, client = configured(handler)
    with client, pytest.raises(JsonChatTimeoutError) as caught:
        adapter.complete_request(request_body())
    assert len(calls) == 1
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_regular_complete_uses_same_send_path_and_observer(monkeypatch):
    body = request_body()
    events = []
    adapter, client = configured(
        lambda request: events.append(json.loads(request.content)) or response(),
        request_observer=lambda value: events.append(value),
    )
    original_complete_request = adapter.complete_request
    delegated = []

    def replay(value):
        delegated.append(deepcopy(value))
        return original_complete_request(value)

    monkeypatch.setattr(adapter, "complete_request", replay)
    data = json.loads(USER_CONTENT)
    with client:
        adapter.complete(body["messages"][0]["content"], data, temperature=0)
    assert len(delegated) == 1
    assert len(events) == 2
    assert events[0] == events[1] == delegated[0]
    assert delegated[0]["temperature"] == 0
    assert json.loads(delegated[0]["messages"][1]["content"]) == data
