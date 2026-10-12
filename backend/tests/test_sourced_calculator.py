"""来源引用、链式运算和保护边界；无数据库、无真实模型。"""

from decimal import Context, Inexact, ROUND_DOWN, localcontext
import json
from uuid import uuid4

import httpx
from pydantic import SecretStr, ValidationError
import pytest

from app.agents.calculator import CalculatorTool
from app.agents.contracts import AgentInputError, FinishAction, ToolAction, ToolContext, ToolExecutionError
from app.agents.engine import run_agent
from app.agents.sourced_calculator import SourcedCalculatorTool
from app.agents.sourced_contracts import SourcedAgentCalculation, SourcedArguments
from app.agents.tools import ToolRegistry
from app.conversations.contracts import UserCount
from app.providers.native_planner import NativePlannerProvider
from test_agent_engine import ControlledGeneration, ControlledPlanner, hit
from test_native_planner import call_payload, finish_payload


RULE = "每次上门服务费 30 元，其他原文数值 120 元。"


def user_count(value=3, raw_text=None, **changes):
    return UserCount(field_name="visit_count", value=value, unit="次", request_id=uuid4(),
                     raw_text=raw_text if raw_text is not None else f"{value}次", **changes)


def doc(quote="服务费 30 元", evidence_id=1):
    return {"source": "evidence", "evidence_id": evidence_id, "quote": quote}


def user(item):
    return {"source": "user_input", "field_name": item.field_name, "request_id": str(item.request_id)}


def previous(identifier=1):
    return {"source": "calculation", "calculation_id": identifier}


def action(left, right, operation="multiply"):
    return ToolAction(action="tool", tool_name="sourced_calculator",
                      arguments={"operation": operation, "left": left, "right": right})


def registry():
    result = ToolRegistry()
    result.register(SourcedCalculatorTool())
    result.register(CalculatorTool())
    return result


def run(decisions, item=None, **options):
    inputs = () if item is None else (item,)
    planner, generation = ControlledPlanner(decisions), ControlledGeneration()
    outcome = run_agent(planner, generation, registry(), "执行已确认来源的算术", initial_evidence=(hit(RULE),),
                        user_inputs=inputs, max_steps=8, **options)
    return outcome, planner, generation


@pytest.mark.parametrize("count", [0, 3, 9999])
def test_user_operand_uses_confirmed_value_and_is_not_a_document_citation(count):
    item = user_count(count)
    outcome, planner, generation = run([action(doc(), user(item)), FinishAction(action="finish")], item)
    assert outcome.status == "completed" and not generation.calls
    result = outcome.calculations[0]
    assert result.value == str(30 * count) and result.right.value == str(count)
    assert result.right.source == "user_input" and result.right.raw_text == item.raw_text
    assert result.right.provenance == "user_message" and result.calculator_kind == "sourced"
    assert len(outcome.result.citations) == 1 and outcome.result.citations[0].quote == "服务费 30 元"
    assert "不是企业原文" in outcome.result.answer and "不代表原业务任务已完成" in outcome.result.answer
    assert planner.states[0]["confirmed_inputs"][0]["request_id"] == str(item.request_id)


def test_three_step_chain_retains_flat_references_and_original_sources():
    item = user_count()
    outcome, planner, generation = run([
        action(doc(), user(item)),  # 30 × 3 = 90
        action(doc("原文数值 120 元"), user(item)),  # 120 × 3 = 360（仅算术，不是年度费用）
        action(previous(1), previous(2), "add"), FinishAction(action="finish"),
    ], item)
    assert outcome.status == "completed" and not generation.calls
    assert [result.value for result in outcome.calculations] == ["90", "360", "450"]
    assert [result.calculation_id for result in outcome.calculations] == [1, 2, 3]
    final = outcome.calculations[-1]
    assert final.left.calculation_id == 1 and final.right.calculation_id == 2
    assert final.left.value == "90" and final.left.source == "calculation"
    assert "left" not in final.left.model_dump()  # 不递归展开整棵公式树。
    assert len(outcome.result.citations) == 2
    assert planner.states[-1]["calculations"][-1]["value"] == "450"
    assert "450" not in RULE and "计算 1" in outcome.result.answer


@pytest.mark.parametrize("change", [
    {"value": "4"}, {"source": "constant", "value": "12"}, {"request_id": str(uuid4())},
    {"field_name": "other_count"}, {"request_id": "not-a-uuid"},
])
def test_cannot_override_confirmed_value_or_reference_other_task(change):
    item = user_count()
    outcome, _, generation = run([action(doc(), {**user(item), **change}), FinishAction(action="finish")], item)
    assert outcome.status == "failed" and outcome.stop_reason == "calculation_error"
    assert not outcome.calculations and outcome.result is None and not generation.calls


@pytest.mark.parametrize("identifier", [1, 2, 7])
def test_future_self_and_missing_result_references_are_rejected(identifier):
    outcome, _, generation = run([action(previous(identifier), doc()), FinishAction(action="finish")])
    assert outcome.status == "failed" and outcome.steps[0].error_code == "invalid_calculation_source"
    assert not outcome.calculations and not generation.calls


def test_history_does_not_authorize_user_or_previous_result_inputs():
    item = user_count()
    outcome, _, _ = run([action(doc(), user(item)), FinishAction(action="finish")],
                        conversation_context={"confirmed_inputs": [item.model_dump(mode="json")],
                                              "calculations": [{"calculation_id": 1, "value": "90"}]})
    assert outcome.status == "failed" and outcome.steps[0].error_code == "invalid_user_source"


def test_invalid_user_snapshot_is_rejected_even_if_constructed_without_validation():
    item = user_count().model_copy(update={"value": 4, "raw_text": "三次"})
    outcome, _, _ = run([action(doc(), user(item)), FinishAction(action="finish")], item)
    assert outcome.status == "failed" and outcome.steps[0].error_code == "invalid_user_source"


@pytest.mark.parametrize("quote", ["服务费 3 元", RULE, "-30", "30%", "数值 12", "0.0000001"])
def test_document_reference_keeps_exact_quote_and_numeric_boundary_validation(quote):
    item = user_count()
    outcome, _, _ = run([action(doc(quote), user(item)), FinishAction(action="finish")], item)
    assert outcome.status == "failed" and not outcome.calculations


@pytest.mark.parametrize("operation,expected", [("add", "33"), ("subtract", "27"),
                                               ("multiply", "90"), ("divide", "10")])
def test_four_operations_share_decimal_arithmetic(operation, expected):
    item = user_count()
    outcome, _, _ = run([action(doc(), user(item), operation), FinishAction(action="finish")], item)
    assert outcome.calculations[0].value == expected


def test_rounded_result_is_reused_with_explicit_rounding_lineage():
    item = user_count(7)
    with localcontext(Context(prec=2, rounding=ROUND_DOWN, traps=[Inexact])):
        outcome, _, _ = run([action(doc(), user(item), "divide"), action(previous(), user(item)),
                             FinishAction(action="finish")], item)
    assert [result.value for result in outcome.calculations] == ["4.28571429", "30.00000003"]
    assert outcome.calculations[1].left.rounded and not outcome.calculations[1].rounded
    assert "本步使用保存的舍入值" in outcome.result.answer


def test_zero_division_does_not_publish_a_previous_partial_success():
    item = user_count(0)
    outcome, _, generation = run([action(doc(), user(item)), action(doc(), user(item), "divide"),
                                  FinishAction(action="finish")], item)
    assert outcome.status == "failed" and outcome.stop_reason == "calculation_error"
    assert len(outcome.calculations) == 1 and outcome.result is None and not generation.calls


def test_corrected_reference_recovers_without_using_failed_result_id():
    item = user_count()
    outcome, _, _ = run([action(previous(), user(item)), action(doc(), user(item)),
                         action(previous(), doc(), "add"), FinishAction(action="finish")], item)
    assert outcome.status == "completed"
    assert [result.value for result in outcome.calculations] == ["90", "120"]
    assert outcome.steps[0].status == "error" and outcome.calculations[0].calculation_id == 1


def test_legacy_result_can_be_referenced_by_new_tool_without_changing_old_contract():
    old = ToolAction(action="tool", tool_name="calculator", arguments={"operation": "add",
                     "left": {"evidence_id": 1, "quote": "服务费 30 元", "value": "30"},
                     "right": {"evidence_id": 1, "quote": "原文数值 120 元", "value": "120"}})
    item = user_count()
    outcome, _, _ = run([old, action(previous(), user(item)), FinishAction(action="finish")], item)
    assert outcome.status == "completed" and [result.value for result in outcome.calculations] == ["150", "450"]
    assert "calculator_kind" not in outcome.calculations[0].model_dump()
    assert len(outcome.result.citations) == 2


def test_repeated_action_and_last_step_tool_protections_apply_to_reference_tool():
    item = user_count()
    first = action(doc(), user(item))
    outcome, _, _ = run([first, first], item)
    assert outcome.stop_reason == "duplicate_action" and len(outcome.calculations) == 1
    decisions = [first] + [action(previous(index), user(item)) for index in range(1, 8)]
    outcome, _, _ = run(decisions, item)
    assert outcome.stop_reason == "step_limit" and len(outcome.calculations) == 7
    assert outcome.result is None


def test_chained_result_size_limit_fails_instead_of_rounding_or_truncating():
    original = hit("数值 999999999999 元。")
    decisions = [action(doc("999999999999"), doc("999999999999")),
                 action(previous(), previous()), FinishAction(action="finish")]
    outcome = run_agent(ControlledPlanner(decisions), ControlledGeneration(), registry(), "算术",
                        initial_evidence=(original,))
    assert outcome.status == "failed" and outcome.steps[1].error_code == "calculation_limit"
    assert len(outcome.calculations) == 1


def test_required_calculation_cannot_be_bypassed_by_plain_finish():
    outcome, _, generation = run([FinishAction(action="finish")], require_calculation=True)
    assert outcome.status == "failed" and outcome.stop_reason == "calculation_required"
    assert not generation.calls and outcome.result is None


def test_recovery_budget_is_checked_before_model_calls():
    planner = ControlledPlanner([])
    with pytest.raises(AgentInputError):
        run_agent(planner, ControlledGeneration(), registry(), "算术", initial_evidence=(hit(RULE),),
                  max_evidence_characters=1)
    assert not planner.states


def test_constructed_arguments_cannot_smuggle_value_or_expression():
    item = user_count()
    valid = SourcedArguments.model_validate(action(doc(), user(item)).arguments, strict=True)
    bad = valid.model_copy(update={"right": {**user(item), "value": "4"}})
    with pytest.raises(ToolExecutionError) as error:
        SourcedCalculatorTool().execute(bad, ToolContext((hit(RULE),), (item,)))
    assert error.value.code == "invalid_arguments"
    with pytest.raises(ValidationError):
        SourcedArguments.model_validate({**valid.model_dump(), "expression": "30*3"}, strict=True)


def test_native_recovery_sends_sources_and_returns_paired_chain_results():
    item = user_count()
    bodies = []
    decisions = [action(doc(), user(item)), action(previous(), doc(), "add")]
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        initial = json.loads(body["messages"][1]["content"])
        assert initial["confirmed_inputs"][0]["value"] == 3
        assert initial["evidence"] == [{"evidence_id": 1, "content": RULE}]
        if len(bodies) <= 2:
            decision = decisions[len(bodies) - 1]
            return httpx.Response(200, json=call_payload("sourced_calculator", json.dumps(decision.arguments),
                                                       f"call_{len(bodies)}"))
        tools = [message for message in body["messages"] if message["role"] == "tool"]
        assert [message["tool_call_id"] for message in tools] == ["call_1", "call_2"]
        assert json.loads(tools[-1]["content"])["calculation"]["value"] == "120"
        return httpx.Response(200, json=finish_payload("伪造的全年费用答案不得返回"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        planner = NativePlannerProvider("https://example.test/v1", SecretStr("fictional-key"), "test-model", client=client)
        outcome = run_agent(planner, ControlledGeneration(), registry(), "算术", initial_evidence=(hit(RULE),),
                            user_inputs=(item,))
    assert outcome.status == "completed" and len(bodies) == 3
    assert "伪造" not in outcome.result.answer
