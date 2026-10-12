"""待补动作的原文约束、停止边界和模拟原生协议。"""

import json

import httpx
from pydantic import SecretStr
import pytest

from app.agents.clarification import ClarificationTool
from app.agents.contracts import ToolAction
from app.agents.engine import run_agent
from app.providers.native_planner import NativePlannerProvider
from test_agent_engine import ControlledGeneration, ControlledPlanner, hit, search, setup
from test_native_planner import call_payload


RULE = "月费 120 元，每次上门服务费 30 元。"


def ask(**overrides):
    return ToolAction(action="tool", tool_name="ask_user", arguments={
        "field_name": "visit_count", "label": "全年上门次数", "unit": "次", "evidence_ids": [1],
        "rule_quote": RULE, **overrides,
    })


def test_search_then_ask_stops_without_generation_and_keeps_original_task():
    original = hit("月费 120 元，每次上门服务费 30 元。")
    registry, tool = setup([[original]])
    registry.register(ClarificationTool())
    generation = ControlledGeneration()
    planner = ControlledPlanner([search("收费标准"), ask()])
    run = run_agent(planner, generation, registry, "全年费用是多少？")
    assert run.status == "waiting_for_user" and run.result is None and not generation.calls
    assert run.pending_task.question == "全年费用是多少？"
    assert run.pending_task.clarification.field_name == "visit_count"
    assert run.pending_task.evidence[0].content == original.content
    assert run.pending_task.evidence[0].chunk_id == original.chunk_id
    assert len(tool.calls) == 1 and len(run.steps) == 2


@pytest.mark.parametrize("overrides,code", [
    ({"evidence_ids": [2]}, "invalid_clarification_source"),
    ({"evidence_ids": [1, 1]}, "invalid_arguments"),
    ({"unit": "元"}, "invalid_arguments"),
    ({"field_name": "../other"}, "invalid_arguments"),
    ({"rule_quote": "月费999元"}, "invalid_clarification_source"),
])
def test_ask_requires_valid_field_and_current_evidence(overrides, code):
    registry, _ = setup([[hit(RULE)]])
    registry.register(ClarificationTool())
    run = run_agent(ControlledPlanner([search(), ask(**overrides), ask()]), ControlledGeneration(), registry, "费用？")
    assert run.steps[1].error_code == code
    assert run.status == "waiting_for_user"


def test_ask_without_search_cannot_fabricate_a_pending_task():
    from app.agents.contracts import FinishAction
    registry, _ = setup([])
    registry.register(ClarificationTool())
    run = run_agent(ControlledPlanner([ask(), FinishAction(action="finish")]), ControlledGeneration(), registry, "费用？")
    assert run.status == "failed" and run.stop_reason == "tool_error" and run.pending_task is None


def test_existing_single_run_registry_does_not_gain_ask_capability():
    from app.agents.contracts import FinishAction
    registry, _ = setup([])
    run = run_agent(ControlledPlanner([ask(), FinishAction(action="finish")]), ControlledGeneration(), registry, "费用？")
    assert run.status == "failed" and run.steps[0].error_code == "unknown_tool"


def test_context_is_a_copy_and_not_added_to_original_evidence():
    registry, _ = setup([[hit(RULE)]])
    registry.register(ClarificationTool())
    context = {"recent_messages": [{"role": "user", "content": "以前谈过上门"}]}
    class MutatingPlanner(ControlledPlanner):
        def decide(self, state):
            choice = super().decide(state)
            state["conversation_context"]["recent_messages"].clear()
            return choice
    run = run_agent(MutatingPlanner([search(), ask()]), ControlledGeneration(), registry, "费用？",
                    conversation_context=context)
    assert context["recent_messages"] and len(run.pending_task.evidence) == 1


def test_native_ask_after_paired_search_preserves_short_term_context():
    registry, _ = setup([[hit(RULE)]])
    registry.register(ClarificationTool())
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        data = json.loads(body["messages"][1]["content"])
        assert data["conversation_context"]["recent_messages"][0]["content"] == "服务收费"
        if len(requests) == 1:
            return httpx.Response(200, json=call_payload(raw='{"query":"收费标准"}'))
        assert body["messages"][2]["tool_calls"][0]["id"] == body["messages"][3]["tool_call_id"]
        return httpx.Response(200, json=call_payload(name="ask_user", identifier="ask_2",
                                                    raw=json.dumps(ask().arguments, ensure_ascii=False)))
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        planner = NativePlannerProvider("https://example.test", SecretStr("fake"), "test-model", client=transport)
        run = run_agent(planner, ControlledGeneration(), registry, "全年费用？", conversation_context={
            "recent_messages": [{"role": "user", "content": "服务收费"}],
        })
    assert run.status == "waiting_for_user" and len(requests) == 2 and run.steps[1].tool_call_id == "ask_2"
