"""搜索、确定性计算和回答的业务闭环；派生结果不会伪装成原文。"""

import json
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from app.agents.calculator import CalculatorTool
from app.agents.contracts import FinishAction, ToolAction, ToolContext, ToolOutput
from app.agents.engine import run_agent
from app.agents.tools import SearchArguments, ToolRegistry
from app.api import agent_runs as api
from app.api import answers as answers_api
from app.knowledge.retrieval import SearchHit
from app.providers.native_planner import NativePlannerProvider
from test_agent_engine import ControlledGeneration, ControlledPlanner
from test_hybrid_retrieval import set_contents
from test_native_planner import call_payload, finish_payload
from test_retrieval import retrieval_context, vector


SOURCE = "基础费 120.50 元，附加费 30 元。"
LEFT_QUOTE = "基础费 120.50 元"
RIGHT_QUOTE = "附加费 30 元"


def search():
    return ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": "基础费和附加费是多少？"})


def arguments(*, operation="add", left="120.50", right="30", left_id=1, right_id=1,
              left_quote=LEFT_QUOTE, right_quote=RIGHT_QUOTE):
    return {"operation": operation,
            "left": {"evidence_id": left_id, "quote": left_quote, "value": left},
            "right": {"evidence_id": right_id, "quote": right_quote, "value": right}}


def calculate(**options):
    return ToolAction(action="tool", tool_name="calculator", arguments=arguments(**options))


def source(content=SOURCE, *, start_char=17):
    return SearchHit(uuid4(), uuid4(), "计费说明.txt", 2, content,
                     start_char, start_char + len(content), 0.1)


class SearchTool:
    name = "knowledge_search"
    description = "测试中的只读搜索"
    argument_schema = SearchArguments

    def __init__(self, hits):
        self.hits = tuple(hits)
        self.calls = []

    def execute(self, validated, context: ToolContext):
        self.calls.append((validated, context))
        return ToolOutput(self.hits)


def setup(hits=None):
    search_tool = SearchTool([source()] if hits is None else hits)
    registry = ToolRegistry()
    registry.register(search_tool)
    registry.register(CalculatorTool())
    return registry, search_tool


def assert_original_sources(result, original):
    """答案只能引用输入原文，计算值必须另有记录。"""
    assert result.result is not None and not result.result.abstained
    assert {item.quote for item in result.result.citations} == {LEFT_QUOTE, RIGHT_QUOTE}
    for item in result.result.citations:
        assert item.chunk_id == original.chunk_id and item.document_id == original.document_id
        assert item.document_name == original.document_name and item.chunk_index == original.chunk_index
        offset = item.start_char - original.start_char
        assert original.content[offset:offset + item.end_char - item.start_char] == item.quote
    assert "150.5" in result.result.answer and "150.5" not in original.content


def test_search_calculate_finish_keeps_program_result_separate_from_original_citations():
    original = source()
    registry, tool = setup([original])
    planner = ControlledPlanner([search(), calculate(), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "基础费加附加费是多少？")
    assert result.status == "completed" and result.stop_reason is None
    assert len(tool.calls) == 1 and len(planner.states) == 3 and not generation.calls
    assert [item.tool_name for item in result.steps] == ["knowledge_search", "calculator", None]
    assert result.steps[1].calculation_id == 1 and result.steps[1].evidence_ids == []
    assert len(result.evidence) == len(result.calculations) == 1
    calculation = result.calculations[0]
    assert calculation.calculation_id == 1 and calculation.operation == "add"
    assert calculation.value == "150.5" and not calculation.rounded
    assert calculation.provenance == "program_calculation"
    assert calculation.left.value == "120.5" and calculation.right.value == "30"
    assert calculation.left.chunk_id == calculation.right.chunk_id == original.chunk_id
    assert calculation.left.start_char == original.start_char
    assert calculation.right.start_char == original.start_char + SOURCE.index(RIGHT_QUOTE)
    assert_original_sources(result, original)


@pytest.mark.parametrize("options", [
    {"left": "120.51"},
    {"left_quote": "基础费 999 元", "left": "999"},
    {"left_id": 2},
    {"left": "120.5", "left_quote": SOURCE},
    {"left": True},
])
def test_unbound_or_invalid_number_cannot_be_bypassed_by_finishing(options):
    registry, _ = setup()
    generation = ControlledGeneration()
    planner = ControlledPlanner([search(), calculate(**options), FinishAction(action="finish")])
    result = run_agent(planner, generation, registry, "总费用是多少？")
    assert result.status == "failed" and result.stop_reason == "calculation_error"
    assert result.result is None and not result.calculations and not generation.calls
    assert planner.states[-1]["observations"][-1]["status"] == "error"


def test_calculation_without_search_is_failed_instead_of_no_evidence_abstention():
    registry, tool = setup()
    generation = ControlledGeneration()
    result = run_agent(ControlledPlanner([calculate(), FinishAction(action="finish")]), generation, registry, "总费用？")
    assert result.status == "failed" and result.stop_reason == "calculation_error"
    assert result.result is None and not tool.calls and not generation.calls


def test_failed_calculation_can_recover_only_after_corrected_calculation_succeeds():
    original = source()
    registry, _ = setup([original])
    planner = ControlledPlanner([search(), calculate(left="120.51"), calculate(), FinishAction(action="finish")])
    generation = ControlledGeneration()
    result = run_agent(planner, generation, registry, "总费用？")
    assert result.status == "completed" and len(result.calculations) == 1 and not generation.calls
    assert [step.status for step in result.steps] == ["success", "error", "success", "success"]
    assert result.calculations[0].calculation_id == 1
    assert_original_sources(result, original)


def test_later_calculation_failure_prevents_reporting_earlier_success_as_final_answer():
    registry, _ = setup()
    generation = ControlledGeneration()
    result = run_agent(ControlledPlanner([search(), calculate(), calculate(right="31"), FinishAction(action="finish")]),
                       generation, registry, "总费用？")
    assert result.status == "failed" and result.stop_reason == "calculation_error" and result.result is None
    assert len(result.calculations) == 1 and result.calculations[0].value == "150.5"
    assert not generation.calls


def test_successful_search_does_not_erase_pending_calculation_error():
    registry, _ = setup()
    generation = ControlledGeneration()
    refined = ToolAction(action="tool", tool_name="knowledge_search", arguments={"query": "重新确认计费"})
    result = run_agent(ControlledPlanner([search(), calculate(left="999"), refined, FinishAction(action="finish")]),
                       generation, registry, "总费用？")
    assert result.status == "failed" and result.stop_reason == "calculation_error" and not generation.calls


def test_normalized_decimal_and_quote_whitespace_cannot_bypass_duplicate_calculation():
    registry, _ = setup()
    generation = ControlledGeneration()
    planner = ControlledPlanner([search(), calculate(), calculate(left="120.500", right="30.0",
                                  left_quote=" 基础费 120.50 元 ")])
    result = run_agent(planner, generation, registry, "总费用？")
    assert result.status == "stopped" and result.stop_reason == "duplicate_action" and result.result is None
    assert len(result.calculations) == 1 and not generation.calls


def test_multiple_calculations_have_stable_ids_and_are_visible_without_polluting_evidence():
    original = source()
    registry, _ = setup([original])
    planner = ControlledPlanner([search(), calculate(), calculate(operation="subtract"), FinishAction(action="finish")])
    result = run_agent(planner, ControlledGeneration(), registry, "请计算费用之和与差。")
    assert result.status == "completed"
    assert [item.calculation_id for item in result.calculations] == [1, 2]
    assert [item.value for item in result.calculations] == ["150.5", "90.5"]
    assert [step.calculation_id for step in result.steps] == [None, 1, 2, None]
    observed = planner.states[-1]
    assert observed["evidence"] == [{"evidence_id": 1, "content": SOURCE}]
    assert [item["calculation_id"] for item in observed["calculations"]] == [1, 2]
    assert observed["calculations"][0]["left"]["chunk_id"] == str(original.chunk_id)
    assert observed["observations"][1]["calculation_id"] == 1
    assert "150.5" in result.result.answer and "90.5" in result.result.answer


def test_planner_cannot_modify_calculation_records_owned_by_controller():
    class MutatingPlanner(ControlledPlanner):
        def decide(self, state):
            decision = super().decide(state)
            if state["calculations"]:
                state["calculations"][0]["value"] = "999"
                state["calculations"].clear()
            return decision
    registry, _ = setup()
    result = run_agent(MutatingPlanner([search(), calculate(), FinishAction(action="finish")]),
                       ControlledGeneration(), registry, "总费用？")
    assert result.status == "completed" and result.calculations[0].value == "150.5"
    assert "999" not in result.result.answer


def test_normal_question_still_uses_existing_generation_when_no_calculation_requested():
    registry, _ = setup()
    generation = ControlledGeneration()
    result = run_agent(ControlledPlanner([search(), FinishAction(action="finish")]), generation, registry, "基础费是多少？")
    assert result.status == "completed" and not result.calculations and len(generation.calls) == 1
    assert result.result.answer == SOURCE


def native_handler(bodies, *, erroneous_first_calculation=False):
    """模拟服务按调用编号读原文，再读取独立程序结果。"""
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in body["tools"]}
        assert set(schemas) == {"knowledge_search", "calculator"}
        assert schemas["calculator"]["additionalProperties"] is False
        assert set(schemas["calculator"]["required"]) == {"operation", "left", "right"}
        replies = [message for message in body["messages"] if message["role"] == "tool"]
        if not replies:
            return httpx.Response(200, json=call_payload(identifier="search_1", raw=json.dumps(search().arguments)))
        assert replies[0]["tool_call_id"] == "search_1"
        assert json.loads(replies[0]["content"])["evidence"] == [{"evidence_id": 1, "content": SOURCE}]
        if len(replies) == 1:
            choices = arguments(left="120.51") if erroneous_first_calculation else arguments()
            return httpx.Response(200, json=call_payload(name="calculator", identifier="calculate_1", raw=json.dumps(choices)))
        assert replies[1]["tool_call_id"] == "calculate_1"
        second = json.loads(replies[1]["content"])
        if erroneous_first_calculation and len(replies) == 2:
            assert second["status"] == "error" and "calculation" not in second
            return httpx.Response(200, json=call_payload(name="calculator", identifier="calculate_2", raw=json.dumps(arguments())))
        final = json.loads(replies[-1]["content"])
        assert final["status"] == "success" and final["calculation"]["calculation_id"] == 1
        assert final["calculation"]["value"] == "150.5"
        assert final["calculation"]["provenance"] == "program_calculation"
        assert not final.get("evidence")
        assistant_ids = [message["tool_calls"][0]["id"] for message in body["messages"]
                         if message["role"] == "assistant"]
        assert assistant_ids == [message["tool_call_id"] for message in replies]
        return httpx.Response(200, json=finish_payload("模型伪造的总价 999 元，不得成为回答"))
    return handler


@pytest.mark.parametrize("recover", [False, True])
def test_native_multi_tool_loop_preserves_pairing_and_uses_program_answer(recover):
    original = source()
    registry, tool = setup([original])
    bodies, generation = [], ControlledGeneration()
    with httpx.Client(transport=httpx.MockTransport(native_handler(bodies, erroneous_first_calculation=recover))) as transport:
        planner = NativePlannerProvider("https://example.test/v1", SecretStr("fictional-key"), "test-model", client=transport)
        result = run_agent(planner, generation, registry, "总费用？")
    assert result.status == "completed" and len(bodies) == (4 if recover else 3)
    assert len(tool.calls) == 1 and not generation.calls and "999" not in result.result.answer
    assert_original_sources(result, original)


def test_actual_api_search_calculate_finish_exposes_separate_program_result_and_sources(retrieval_context):
    context = retrieval_context
    document_id, ids = context.document([vector(1)], name="计费说明.txt")
    set_contents(context, ids, [SOURCE])
    bodies, generation = [], ControlledGeneration()
    context.client.app.dependency_overrides[answers_api.get_generation_provider] = lambda: generation
    with httpx.Client(transport=httpx.MockTransport(native_handler(bodies))) as transport:
        planner = NativePlannerProvider("https://example.test/v1", SecretStr("fictional-key"), "test-model", client=transport)
        context.client.app.dependency_overrides[api.get_planner_provider] = lambda: planner
        response = context.client.post(context.url().removesuffix("/search") + "/agent-runs",
                                       json={"question": "基础费加附加费是多少？"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["stop_reason"] is None
    assert len(body["evidence"]) == len(body["calculations"]) == 1
    calculation = body["calculations"][0]
    assert calculation["calculation_id"] == 1 and calculation["value"] == "150.5"
    assert calculation["provenance"] == "program_calculation"
    assert calculation["left"]["chunk_id"] == calculation["right"]["chunk_id"] == str(ids[0])
    assert calculation["left"]["document_id"] == str(document_id)
    assert body["steps"][1]["calculation_id"] == 1 and body["steps"][1]["tool_call_id"] == "calculate_1"
    assert {item["quote"] for item in body["result"]["citations"]} == {LEFT_QUOTE, RIGHT_QUOTE}
    assert all(item["chunk_id"] == str(ids[0]) for item in body["result"]["citations"])
    assert context.provider.calls == 1 and len(bodies) == 3 and not generation.calls


def test_actual_api_invalid_calculation_cannot_fall_back_to_generated_answer(retrieval_context):
    context = retrieval_context
    _, ids = context.document([vector(1)])
    set_contents(context, ids, [SOURCE])
    planner = ControlledPlanner([search(), calculate(left="999"), FinishAction(action="finish")])
    generation = ControlledGeneration()
    context.client.app.dependency_overrides[api.get_planner_provider] = lambda: planner
    context.client.app.dependency_overrides[answers_api.get_generation_provider] = lambda: generation
    response = context.client.post(context.url().removesuffix("/search") + "/agent-runs", json={"question": "总费用？"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed" and response.json()["stop_reason"] == "calculation_error"
    assert response.json()["result"] is None and not response.json()["calculations"]
    assert context.provider.calls == 1 and not generation.calls
