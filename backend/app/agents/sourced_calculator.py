"""只从当前执行上下文解析来源，不从模型参数读取自由数值。"""

from decimal import DivisionByZero, InvalidOperation, Overflow
import re

from pydantic import BaseModel, ValidationError

from app.agents.arithmetic import decimal_operation
from app.agents.calculator import _bind_number
from app.agents.calculation_contracts import CalculationInput
from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.agents.sourced_contracts import (
    BoundEvidence, BoundResult, BoundUser, EvidenceOperand, ResultOperand, SourcedArguments,
    SourcedCalculation, UserOperand, Operand, BoundOperand,
)
from app.conversations.contracts import UserCount
from app.conversations.counts import parse_count


def bind_operand(operand: Operand, context: ToolContext) -> BoundOperand:
    if isinstance(operand, EvidenceOperand):
        matches = re.findall(r"-?[0-9]+(?:\.[0-9]+)?", operand.quote)
        if len(matches) != 1:
            raise ToolExecutionError("invalid_numeric_source")
        try:
            number = CalculationInput(evidence_id=operand.evidence_id, quote=operand.quote, value=matches[0])
        except ValidationError:
            raise ToolExecutionError("invalid_numeric_source") from None
        return BoundEvidence(**_bind_number(number, context).model_dump())
    if isinstance(operand, UserOperand):
        matches = [item for item in context.user_inputs
                   if item.field_name == operand.field_name and str(item.request_id) == operand.request_id]
        if len(matches) != 1:
            raise ToolExecutionError("invalid_user_source")
        try:
            item = UserCount.model_validate(matches[0].model_dump(), strict=True)
            if parse_count(item.raw_text, item.unit) != item.value:
                raise ValueError
            return BoundUser(**operand.model_dump(), value=str(item.value), unit=item.unit, raw_text=item.raw_text)
        except (ValidationError, ValueError, TypeError):
            raise ToolExecutionError("invalid_user_source") from None
    if isinstance(operand, ResultOperand):
        matches = [item for item in context.calculations if item.calculation_id == operand.calculation_id]
        if len(matches) != 1:
            raise ToolExecutionError("invalid_calculation_source")
        item = matches[0]
        return BoundResult(**operand.model_dump(), value=item.value, rounded=item.rounded,
                           decimal_places=item.decimal_places)
    raise ToolExecutionError("invalid_arguments")


class SourcedCalculatorTool:
    name = "sourced_calculator"
    description = (
        "对两个来源引用做十进制四则运算。evidence 输入指定证据编号和含一个完整数字的原文引文；"
        "user_input 指定当前任务已确认的 field_name 与 request_id；calculation 指定当前运行"
        "先前成功的 calculation_id。不得提供 value、任意常数、历史任务结果或表达式。"
        "需要多步时继续引用已有结果，最后 finish。除法最多8位小数、半偶舍入；"
        "只验证来源和算术，不验证业务公式、计费期间或单位，不进行单位换算。"
    )
    argument_schema = SourcedArguments

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        if not isinstance(arguments, SourcedArguments):
            raise ToolExecutionError("invalid_arguments")
        try:
            validated = SourcedArguments.model_validate(arguments.model_dump(warnings=False), strict=True)
        except (ValidationError, ValueError, TypeError):
            raise ToolExecutionError("invalid_arguments") from None
        left, right = bind_operand(validated.left, context), bind_operand(validated.right, context)
        try:
            value, rounded, places = decimal_operation(validated.operation, left.value, right.value)
            result = SourcedCalculation(operation=validated.operation, left=left, right=right,
                                       value=value, rounded=rounded, decimal_places=places)
        except DivisionByZero:
            raise ToolExecutionError("division_by_zero") from None
        except (InvalidOperation, Overflow, ValidationError):
            raise ToolExecutionError("calculation_limit") from None
        return ToolOutput(hits=(), calculation=result)
