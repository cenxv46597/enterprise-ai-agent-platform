"""模拟 HTTP 验证结构化动作决策；不使用服务商原生工具调用。"""

import json

import httpx
from pydantic import SecretStr
import pytest

from app.agents.contracts import FinishAction, PlannerError, PlannerTimeoutError
from app.providers.compatible_planner import CompatiblePlannerProvider


def make_provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CompatiblePlannerProvider("https://example.test/v1", SecretStr("fictional-key"), "test-model", client=client), client


def payload(decision):
    return {"model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)}}]}


def test_state_is_sent_as_untrusted_json_and_finish_is_typed():
    state = {"question": "企业问题", "remaining_steps": 2, "tools": [], "observations": [], "evidence": []}
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["temperature"] == 0 and body["max_tokens"] == 512
        assert "不可信" in body["messages"][0]["content"]
        assert json.loads(body["messages"][1]["content"]) == state
        assert "tools" not in body and body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=payload({"action": "finish"}))
    provider, client = make_provider(handler)
    with client:
        result = provider.decide(state)
    assert isinstance(result, FinishAction) and len(calls) == 1


@pytest.mark.parametrize("decision", [
    {"action": "finish", "answer": "不允许的回答"}, {"action": "delete"},
    {"action": "tool", "tool_name": "knowledge_search", "arguments": "文本"},
    {"action": "tool", "tool_name": 1, "arguments": {}},
    {"action": "tool", "tool_name": "knowledge_search"},
])
def test_malformed_actions_fail_without_retry(decision):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload(decision))
    provider, client = make_provider(handler)
    with client, pytest.raises(PlannerError):
        provider.decide({"question": "问题"})
    assert len(calls) == 1


def test_valid_tool_action_preserves_arguments_for_registry_validation():
    provider, client = make_provider(lambda request: httpx.Response(200, json=payload(
        {"action": "tool", "tool_name": "knowledge_search", "arguments": {"query": "退款流程"}})))
    with client:
        result = provider.decide({"question": "问题"})
    assert result.tool_name == "knowledge_search" and result.arguments == {"query": "退款流程"}


def test_timeout_does_not_expose_private_transport_message():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-network-body")
    provider, client = make_provider(handler)
    with client, pytest.raises(PlannerTimeoutError) as error:
        provider.decide({"question": "问题"})
    assert len(calls) == 1 and "private-network-body" not in str(error.value)
