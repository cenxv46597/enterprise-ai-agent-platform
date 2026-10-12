"""真实十进制算术、来源边界和故障恢复；无数据库或模型请求。"""

from decimal import (
    Context, Inexact, ROUND_DOWN, getcontext, localcontext,
)
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.calculation_contracts import CalculatorArguments, CalculationInput
from app.agents.calculator import CalculatorTool
from app.agents.contracts import ToolContext, ToolExecutionError
from app.knowledge.retrieval import SearchHit


def evidence(content, start=40):
    return SearchHit(uuid4(), uuid4(), "价格说明.md", 2, content, start, start + len(content), 0.2)


def arguments(operation="add", left="120.50", right="30", *, left_quote=None, right_quote=None):
    return CalculatorArguments(
        operation=operation,
        left=CalculationInput(evidence_id=1, quote=left_quote or f"基础费 {left} 元", value=left),
        right=CalculationInput(evidence_id=2, quote=right_quote or f"附加费 {right} 元", value=right),
    )


def calculate(operation="add", left="120.50", right="30"):
    sources = ToolContext((evidence(f"基础费 {left} 元。"), evidence(f"附加费 {right} 元。")))
    return CalculatorTool().execute(arguments(operation, left, right), sources).calculation


@pytest.mark.parametrize(("operation", "left", "right", "expected"), [
    ("add", "120.50", "30", "150.5"),
    ("add", "-7", "2", "-5"),
    ("subtract", "120.50", "30", "90.5"),
    ("subtract", "-7", "-2", "-5"),
    ("multiply", "0.1", "0.2", "0.02"),
    ("multiply", "999999999999.999999", "999999999999.999999", "999999999999999998000000.000000000001"),
    ("add", "999999999999.999999", "0.000001", "1000000000000"),
    ("multiply", "-0", "2", "0"),
])
def test_exact_arithmetic_preserves_decimal_values(operation, left, right, expected):
    result = calculate(operation, left, right)
    assert result.value == expected
    assert result.rounded is False and result.decimal_places is None
    assert result.provenance == "program_calculation"


@pytest.mark.parametrize(("left", "right", "expected", "rounded"), [
    ("1", "8", "0.125", False),
    ("1", "3", "0.33333333", True),
    ("2", "3", "0.66666667", True),
    ("1", "200000000", "0", True),
    ("3", "200000000", "0.00000002", True),
    ("-1", "200000000", "0", True),
    ("1", "-8", "-0.125", False),
    ("0", "3", "0", False),
    ("999999999999", "0.000001", "999999999999000000", False),
])
def test_division_reports_actual_rounding_and_half_even(left, right, expected, rounded):
    result = calculate("divide", left, right)
    assert result.value == expected and result.rounded is rounded
    assert result.decimal_places == 8


def test_sources_use_first_exact_quote_location_and_real_document_metadata():
    first = evidence("前言。基础费 120.50 元。基础费 120.50 元。", start=300)
    second = evidence("附加费 30 元。", start=900)
    output = CalculatorTool().execute(arguments(), ToolContext((first, second)))
    result = output.calculation
    assert output.hits == () and output.reranking is None
    assert result.left.quote == "基础费 120.50 元" and result.left.value == "120.5"
    assert result.left.chunk_id == first.chunk_id and result.left.document_id == first.document_id
    assert result.left.document_name == "价格说明.md" and result.left.chunk_index == 2
    assert result.left.start_char == 303 and result.left.end_char == 315
    assert result.right.start_char == 900 and result.right.end_char == 908
    assert result.value == "150.5"


@pytest.mark.parametrize(("content", "quote", "value"), [
    ("费用 1999 元", "199", "199"),
    ("费用 -10 元", "10", "10"),
    ("费用 −10 元", "10", "10"),
    ("费用 +10 元", "10", "10"),
    ("费用 - 10 元", "10", "10"),
    ("费用 1e3 元", "1", "1"),
    ("费用 1e3 元", "3", "3"),
    ("费用 1 e 3 元", "1", "1"),
    ("费用 1 e 3 元", "3", "3"),
    ("费用 1,000 元", "1", "1"),
    ("费用 1,000 元", "000", "0"),
    ("费用 1，000 元", "1", "1"),
    ("费用 1 000 元", "1", "1"),
    ("费用 1_000 元", "1", "1"),
    ("比例 10%", "10", "10"),
    ("比例 10 %", "10", "10"),
    ("比例 10％", "10", "10"),
    ("比例 10‰", "10", "10"),
    ("比例 百分之10", "10", "10"),
    ("增长 10个百分点", "10", "10"),
    ("份额 1/2", "1", "1"),
    ("份额 1 / 2", "2", "2"),
    ("份额 1⁄2", "1", "1"),
    ("费用 .5 元", "5", "5"),
    ("费用 1.2345678 元", "1.234567", "1.234567"),
    ("费用 012 元", "12", "12"),
    ("费用 １10 元", "10", "10"),
    ("编号 API_10", "10", "10"),
    ("费用 1000000000000 元", "100000000000", "100000000000"),
])
def test_quote_cannot_extract_supported_number_from_unsupported_expression(content, quote, value):
    request = arguments(left=value, left_quote=quote)
    context = ToolContext((evidence(content), evidence("附加费 30 元。")))
    with pytest.raises(ToolExecutionError) as error:
        CalculatorTool().execute(request, context)
    assert error.value.code == "invalid_numeric_source"


@pytest.mark.parametrize(("content", "quote", "value"), [
    ("基础费 120.50 元", "费用 120.50 元", "120.50"),
    ("基础费 120.50 元", "基础费 120.50 元", "120.51"),
    ("基础费 120.50 元和附加费 30 元", "基础费 120.50 元和附加费 30 元", "120.50"),
    ("基础费按合同确定", "基础费按合同确定", "120.50"),
])
def test_quote_must_exist_have_one_number_and_match_value(content, quote, value):
    with pytest.raises(ToolExecutionError) as error:
        CalculatorTool().execute(arguments(left=value, left_quote=quote),
                                 ToolContext((evidence(content), evidence("附加费 30 元"))))
    assert error.value.code == "invalid_numeric_source"


def test_quote_boundaries_allow_ordinary_sentence_punctuation_and_units():
    request = arguments(left="120.50", left_quote="120.50", right="30", right_quote="30")
    result = CalculatorTool().execute(request, ToolContext((
        evidence("基础费120.50，附加费另计。"), evidence("附加费30元。"),
    ))).calculation
    assert result.value == "150.5"


def test_first_quote_occurrence_is_checked_even_if_later_occurrence_is_valid():
    with pytest.raises(ToolExecutionError) as error:
        CalculatorTool().execute(arguments(left="10", left_quote="10"), ToolContext((
            evidence("首次 -10 元；后来 10 元。"), evidence("附加费 30 元。"),
        )))
    assert error.value.code == "invalid_numeric_source"


def test_no_search_evidence_and_unknown_evidence_id_are_not_free_form_calculations():
    request = arguments()
    for context in (ToolContext(), ToolContext((evidence("基础费 120.50 元。"),))):
        with pytest.raises(ToolExecutionError) as error:
            CalculatorTool().execute(request, context)
        assert error.value.code == "invalid_numeric_source"


@pytest.mark.parametrize("zero", ["0", "-0", "0.000000"])
def test_division_by_zero_is_explicit_and_next_valid_call_succeeds(zero):
    tool = CalculatorTool()
    with pytest.raises(ToolExecutionError) as error:
        tool.execute(arguments("divide", "1", zero), ToolContext((
            evidence("基础费 1 元。"), evidence(f"附加费 {zero} 元。"),
        )))
    assert error.value.code == "division_by_zero"
    assert calculate("divide", "1", "8").value == "0.125"


@pytest.mark.parametrize("field,value", [
    ("value", 10), ("value", 10.0), ("value", True), ("value", "1e3"),
    ("value", "1,000"), ("value", "01"), ("value", "+1"),
    ("value", "1000000000000"), ("value", "0.0000001"),
    ("evidence_id", "1"), ("evidence_id", True), ("quote", 10),
])
def test_input_schema_rejects_coercion_and_unsupported_numbers(field, value):
    payload = {"evidence_id": 1, "quote": "费用10元", "value": "10"}
    payload[field] = value
    with pytest.raises(ValidationError):
        CalculationInput.model_validate(payload, strict=True)


def test_schema_rejects_extra_fields_and_arbitrary_expression():
    payload = arguments().model_dump()
    for changed in (
        {**payload, "expression": "__import__('os').system('echo unsafe')"},
        {**payload, "operation": "eval"},
        {**payload, "left": {**payload["left"], "path": "secret.txt"}},
    ):
        with pytest.raises(ValidationError):
            CalculatorArguments.model_validate(changed, strict=True)


def test_execution_revalidates_constructed_objects_and_has_no_partial_state():
    tool = CalculatorTool()
    invalid_left = CalculationInput.model_construct(evidence_id=True, quote="基础费 120.50 元", value="120.50")
    bad = CalculatorArguments.model_construct(operation="add", left=invalid_left, right=arguments().right)
    with pytest.raises(ToolExecutionError) as error:
        tool.execute(bad, ToolContext((evidence("基础费 120.50 元"), evidence("附加费 30 元"))))
    assert error.value.code == "invalid_arguments"
    assert calculate().value == "150.5"


def test_decimal_results_ignore_global_precision_rounding_traps_and_exponent_limits():
    with localcontext(Context(prec=2, rounding=ROUND_DOWN, Emin=-2, Emax=2, traps=[Inexact])):
        original = getcontext().copy()
        assert calculate("multiply", "999999999999.999999", "999999999999.999999").value == (
            "999999999999999998000000.000000000001"
        )
        result = calculate("divide", "2", "3")
        assert result.value == "0.66666667" and result.rounded
        assert getcontext().prec == original.prec and getcontext().rounding == original.rounding
        assert getcontext().traps == original.traps and getcontext().flags == original.flags
