"""模拟原生模型请求、工具结果回传和故障；所有 HTTP 均由 MockTransport 拦截。"""

from copy import deepcopy
import json
import traceback

import httpx
from pydantic import SecretStr
import pytest

from app.agents.contracts import FinishAction, NativeToolCall, PlannerError, PlannerTimeoutError, ToolAction, ToolExecutionError
from app.agents.engine import run_agent
from app.providers.compatible_chat import JsonChatError, MAX_RESPONSE_BYTES
from app.providers.compatible_tools import CompatibleToolChatClient
from app.providers.native_planner import NativePlannerProvider
from test_agent_engine import ControlledGeneration, ControlledPlanner, hit, setup


def call_payload(name="knowledge_search", raw='{"query":"退款流程"}', identifier="call_1", *, content=None):
    return {"model": "test-model", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": content,
        "tool_calls": [{"id": identifier, "type": "function", "function": {"name": name, "arguments": raw}}]}}]}


def finish_payload(text="结束"):
    return {"model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": text}}]}


def provider(handler, *, base_url="https://example.test/v1", **options):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NativePlannerProvider(base_url, SecretStr("fictional-key"), "test-model", client=client, **options), client


def state():
    registry, _ = setup([])
    return {"question": "如何退款？", "remaining_steps": 4, "tools": registry.definitions(), "tool_messages": []}


def test_native_search_result_and_finish_complete_with_original_citation():
    bodies = []
    original = hit()
    registry, tool = setup([[original]])
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["tool_choice"] == "auto" and "response_format" not in body and body["stream"] is False
        assert body["temperature"] == 0 and body["max_tokens"] == 512 and "thinking" not in body
        assert body["tools"][0]["function"]["parameters"]["additionalProperties"] is False
        assert "不可信" in body["messages"][0]["content"]
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload(raw=' {"query": " 退款流程 "} ', content="先查资料"))
        messages = body["messages"]
        assert messages[2]["role"] == "assistant" and messages[2]["content"] == "先查资料"
        assert messages[2]["tool_calls"][0]["function"]["arguments"] == ' {"query": " 退款流程 "} '
        assert messages[3]["role"] == "tool" and messages[3]["tool_call_id"] == "call_1"
        observation = json.loads(messages[3]["content"])
        assert observation == {"status": "success", "evidence": [{"evidence_id": 1, "content": original.content}]}
        assert json.loads(messages[-1]["content"]) == {"remaining_steps": 3}
        return httpx.Response(200, json=finish_payload("我编造的最终答案，不得直接返回"))
    planner, client = provider(handler)
    generation = ControlledGeneration()
    with client:
        result = run_agent(planner, generation, registry, "如何退款？")
    assert result.status == "completed" and len(bodies) == 2 and len(tool.calls) == len(generation.calls) == 1
    assert result.result.answer == original.content and result.result.citations[0].chunk_id == original.chunk_id
    assert result.steps[0].tool_call_id == "call_1" and result.steps[0].query == "退款流程"


@pytest.mark.parametrize("raw,code", [('{"query":true}', "invalid_arguments"),
    ('{"query":"退款","workspace_id":"越权"}', "invalid_arguments")])
def test_invalid_arguments_are_returned_as_tool_error_before_recovery(raw, code):
    bodies = []
    registry, tool = setup([[hit()]])
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload(raw=raw))
        if len(bodies) == 2:
            assert json.loads(body["messages"][3]["content"]) == {"status": "error", "error_code": code}
            return httpx.Response(200, json=call_payload(identifier="call_2"))
        return httpx.Response(200, json=finish_payload())
    planner, client = provider(handler)
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.status == "completed" and len(tool.calls) == 1 and result.steps[0].error_code == code


def test_unknown_tool_is_not_executed_and_error_is_observed():
    bodies = []
    registry, tool = setup([])
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload(name="delete_files", raw="{}"))
        assert json.loads(body["messages"][3]["content"])["error_code"] == "unknown_tool"
        return httpx.Response(200, json=finish_payload())
    planner, client = provider(handler)
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.status == "failed" and result.stop_reason == "tool_error" and not tool.calls


def test_tool_timeout_is_paired_and_different_query_can_recover():
    bodies = []
    registry, tool = setup([ToolExecutionError("tool_timeout"), [hit()]])
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=call_payload())
        if len(bodies) == 2:
            assert json.loads(body["messages"][3]["content"]) == {"status": "error", "error_code": "tool_timeout"}
            return httpx.Response(200, json=call_payload(identifier="call_2", raw='{"query":"申请入口"}'))
        assert [m["tool_call_id"] for m in body["messages"] if m["role"] == "tool"] == ["call_1", "call_2"]
        return httpx.Response(200, json=finish_payload())
    planner, client = provider(handler)
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.status == "completed" and len(tool.calls) == 2


def test_new_call_id_does_not_bypass_duplicate_action_detection():
    calls = []
    registry, tool = setup([[hit()]])
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=call_payload(identifier=f"call_{len(calls)}"))
    planner, client = provider(handler)
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.stop_reason == "duplicate_action" and len(tool.calls) == 1 and len(calls) == 2


def test_reused_call_id_is_protocol_failure_even_for_new_arguments():
    calls = []
    registry, tool = setup([[hit()]])
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=call_payload(raw=json.dumps({"query": str(len(calls))})))
    planner, client = provider(handler)
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.status == "failed" and result.stop_reason == "planner_error" and len(tool.calls) == 1


def test_parallel_calls_are_rejected_before_any_tool_runs():
    payload = call_payload()
    payload["choices"][0]["message"]["tool_calls"].append(call_payload(identifier="call_2")["choices"][0]["message"]["tool_calls"][0])
    registry, tool = setup([])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)
    planner, client = provider(handler)
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.status == "failed" and result.stop_reason == "planner_error" and not tool.calls and len(calls) == 1


@pytest.mark.parametrize("raw", ['[]', '"text"', '{} junk', '{', '{"query":"甲","query":"乙"}',
    '{"query":NaN}', '{"nested":{"x":1,"x":2}}', '{"query":1e999}', '{"query":Infinity}'])
def test_ambiguous_or_malformed_json_is_protocol_failure_without_retry(raw):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=call_payload(raw=raw))
    planner, client = provider(handler)
    with client, pytest.raises(PlannerError):
        planner.decide(state())
    assert len(calls) == 1


@pytest.mark.parametrize("mutate", [
    lambda p: p["choices"][0].update(finish_reason="length"),
    lambda p: p["choices"][0].update(finish_reason="stop"),
    lambda p: p["choices"][0].update(index=True),
    lambda p: p["choices"][0]["message"].update(tool_calls=[]),
    lambda p: p["choices"][0]["message"].update(role="user"),
    lambda p: p["choices"][0]["message"].update(reasoning_content="不支持的内部思考"),
    lambda p: p["choices"][0]["message"].update(content="文" * 2001),
    lambda p: p["choices"][0]["message"].update(function_call={}),
    lambda p: p["choices"][0]["message"]["tool_calls"][0].update(id="bad\nidentifier"),
    lambda p: p["choices"][0]["message"]["tool_calls"][0].update(type="code"),
    lambda p: p["choices"][0]["message"]["tool_calls"][0]["function"].update(name="bad/name"),
    lambda p: p["choices"][0]["message"]["tool_calls"][0]["function"].update(arguments="x" * 8193),
    lambda p: p["choices"][0]["message"]["tool_calls"][0]["function"].update(arguments={}),
])
def test_malformed_native_envelope_is_safe_error(mutate):
    payload = call_payload()
    mutate(payload)
    planner, client = provider(lambda request: httpx.Response(200, json=payload))
    with client, pytest.raises(PlannerError):
        planner.decide(state())


@pytest.mark.parametrize("failure", ["timeout", "http", "large", "stream"])
def test_transport_failures_are_sanitized_and_not_retried(failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private credential detail")
        if failure == "http":
            return httpx.Response(503, text="private credential detail")
        if failure == "large":
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        return httpx.Response(200, text="private credential detail", headers={"content-type": "text/event-stream"})
    planner, client = provider(handler)
    with client, pytest.raises(PlannerTimeoutError if failure == "timeout" else PlannerError) as caught:
        planner.decide(state())
    assert len(calls) == 1 and "private credential" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("base_url,thinking", [("https://api.deepseek.com", True),
    ("https://api.deepseek.com/v1", True), ("https://api.deepseek.com.example.test", False)])
def test_deepseek_official_host_disables_thinking_without_polluting_other_providers(base_url, thinking):
    def handler(request):
        body = json.loads(request.content)
        assert (body.get("thinking") == {"type": "disabled"}) == thinking
        return httpx.Response(200, json=finish_payload())
    planner, client = provider(handler, base_url=base_url)
    with client:
        assert isinstance(planner.decide(state()), FinishAction)


@pytest.mark.parametrize("problem", ["orphan", "mismatch", "duplicate", "too_large"])
def test_invalid_or_oversized_history_is_rejected_before_http(problem):
    first = call_payload()["choices"][0]["message"]
    messages = [{"role": "system", "content": "规则"}, {"role": "user", "content": "问题"}, first,
                {"role": "tool", "tool_call_id": "call_1", "content": "结果"}]
    if problem == "orphan":
        messages.pop()
    elif problem == "mismatch":
        messages[-1]["tool_call_id"] = "call_wrong"
    elif problem == "duplicate":
        messages.extend(deepcopy(messages[2:]))
    else:
        messages[-1]["content"] = "文" * 180000
    client = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("非法历史不能发出 HTTP")))
    chat = CompatibleToolChatClient("https://example.test", SecretStr("fake"), "test-model", client=client)
    with client, pytest.raises(JsonChatError):
        chat.complete(messages, state()["tools"])


def test_planner_can_be_reused_without_leaking_history_between_runs():
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=call_payload()) if len(bodies) == 1 else httpx.Response(200, json=finish_payload())
    planner, client = provider(handler)
    registry, _ = setup([[hit("第一运行资料")]])
    with client:
        assert run_agent(planner, ControlledGeneration(), registry, "第一题").status == "completed"
        empty_registry, _ = setup([])
        assert run_agent(planner, ControlledGeneration(), empty_registry, "第二题").result.abstained
    assert len(bodies[-1]["messages"]) == 2 and "第一运行资料" not in json.dumps(bodies[-1], ensure_ascii=False)


def test_native_last_decision_does_not_execute_tool():
    registry, tool = setup([])
    planner, client = provider(lambda request: httpx.Response(200, json=call_payload()))
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题", max_steps=1)
    assert result.stop_reason == "step_limit" and not tool.calls


def test_transport_metadata_cannot_disagree_with_execution_arguments():
    registry, tool = setup([])
    decision = ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": "实际执行"},
                          native_call=NativeToolCall(id="call_1", arguments_json='{"query":"别的参数"}'))
    result = run_agent(ControlledPlanner([decision]), ControlledGeneration(), registry, "问题")
    assert result.status == "failed" and result.stop_reason == "planner_error" and not tool.calls


def test_json_protocol_cannot_invent_native_metadata():
    from app.providers.compatible_planner import CompatiblePlannerProvider
    response = {"model": "test-model", "choices": [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps({"action": "tool", "tool_name": "knowledge_search",
            "arguments": {"query": "问题"}, "native_call": {"id": "call_1", "arguments_json": "{}"}})}}]}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
    planner = CompatiblePlannerProvider("https://example.test", SecretStr("fake"), "test-model", client=client)
    with client, pytest.raises(PlannerError):
        planner.decide(state())


def test_native_decode_and_transfer_are_inside_total_deadline(monkeypatch):
    from app.providers import compatible_chat
    clock = [0.0]
    monkeypatch.setattr(compatible_chat, "monotonic", lambda: clock[0])
    def handler(request):
        clock[0] = 21.0
        return httpx.Response(200, json=call_payload())
    planner, client = provider(handler)
    registry, tool = setup([])
    with client:
        result = run_agent(planner, ControlledGeneration(), registry, "问题")
    assert result.stop_reason == "planner_timeout" and not tool.calls


def test_history_passed_to_planner_is_a_copy_owned_by_controller():
    class MutatingPlanner(ControlledPlanner):
        def decide(self, state):
            assert len(state["tool_messages"]) == 2 * len(self.states)
            if len(self.states) == 2:
                assert [m["tool_call_id"] for m in state["tool_messages"] if m["role"] == "tool"] == ["call_1", "call_2"]
            result = super().decide(state)
            state["tool_messages"].clear()
            return result
    original = hit()
    registry, tool = setup([[original], [original]])
    decisions = [ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": query},
                            native_call=NativeToolCall(id=f"call_{index}", arguments_json=json.dumps({"query": query})))
                 for index, query in enumerate(("第一问", "第二问"), 1)]
    planner = MutatingPlanner([*decisions, FinishAction(action="finish")])
    result = run_agent(planner, ControlledGeneration(), registry, "问题")
    # ControlledPlanner 保存的副本被故意清空；最终成功与两次工具结果不受影响。
    assert result.status == "completed" and len(tool.calls) == 2 and result.steps[1].evidence_ids == [1]


@pytest.mark.parametrize("protocol", ["native", "json"])
def test_configured_provider_explicitly_selects_one_protocol_without_fallback(monkeypatch, protocol):
    from app.api import agent_runs
    from app.core.config import Settings
    seen = []
    class Selected:
        def __init__(self, *args):
            seen.append(args)
        def decide(self, state):
            raise PlannerError("服务不支持所选协议")
    monkeypatch.setattr(agent_runs, "Settings", lambda: Settings(_env_file=None, agent_protocol=protocol,
        generation_base_url="https://example.test", generation_api_key=SecretStr("fake"), generation_model="test-model"))
    selected_name, other_name = (("NativePlannerProvider", "CompatiblePlannerProvider") if protocol == "native" else
                                 ("CompatiblePlannerProvider", "NativePlannerProvider"))
    monkeypatch.setattr(agent_runs, selected_name, Selected)
    monkeypatch.setattr(agent_runs, other_name, lambda *args: pytest.fail("不能自动改协议"))
    with pytest.raises(PlannerError):
        agent_runs.ConfiguredPlannerProvider().decide(state())
    assert len(seen) == 1


def test_unknown_protocol_is_a_configuration_error():
    from app.core.config import Settings
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_protocol="automatic")
