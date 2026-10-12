"""有界十进制计算；每个输入都绑定本次运行已取得的逐字证据。"""

from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
import re

from pydantic import BaseModel, ValidationError

from app.agents.calculation_contracts import (
    BoundNumber, CalculationResult, CalculatorArguments, CalculationInput,
)
from app.agents.arithmetic import decimal_operation
from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.knowledge.retrieval import SearchHit


_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_SUPPORTED_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?")
_SIGNS = "+-−﹣－±"


def _surrounding_text(content: str, start: int, end: int) -> tuple[str, str]:
    return content[:start].rstrip(), content[end:].lstrip()


def _has_numeric_boundary(content: str, start: int, end: int) -> bool:
    """检查完整原文，防止引文把数字表达式截成一个合法子串。"""
    before, after = content[:start], content[end:]
    for character in (before[-1:] or " ", after[:1] or " "):
        if character.isdigit() or character in _SIGNS + ".．" or character.isascii() and (
            character.isalpha() or character == "_"
        ):
            return False

    # 空格不能把百分数、分数或数字分组改造成受支持数值。
    left, right = _surrounding_text(content, start, end)
    if (left.endswith(tuple(_SIGNS + "/／⁄∕%％‰‱")) or right.startswith(tuple("/／⁄∕%％‰‱"))
            or left.endswith("百分之") or right.startswith("个百分点")):
        return False
    if left[-1:].isdigit() or right[:1].isdigit():
        return False
    if (re.search(r"[0-9]\s*[eE]$", left)
            or re.match(r"[eE]\s*[+-]?\s*[0-9]", right)):
        return False
    # 逗号后的普通文字可以是句子分隔符；两侧都是数字时才视为分组格式。
    if left.endswith((",", "，")) and left[:-1].rstrip()[-1:].isdigit():
        return False
    if right.startswith((",", "，")) and right[1:].lstrip()[:1].isdigit():
        return False
    return True


def _bind_number(number: CalculationInput, context: ToolContext) -> BoundNumber:
    if (not isinstance(context, ToolContext) or not isinstance(context.evidence, tuple)
            or number.evidence_id > len(context.evidence)):
        raise ToolExecutionError("invalid_numeric_source")
    hit = context.evidence[number.evidence_id - 1]
    if not isinstance(hit, SearchHit):
        raise ToolExecutionError("invalid_numeric_source")
    quote_start = hit.content.find(number.quote)
    matches = list(_NUMBER.finditer(number.quote))
    if quote_start < 0 or len(matches) != 1:
        raise ToolExecutionError("invalid_numeric_source")
    match = matches[0]
    token = match.group()
    if (_SUPPORTED_NUMBER.fullmatch(token) is None
            or not _has_numeric_boundary(hit.content, quote_start + match.start(), quote_start + match.end())
            or Decimal(token) != Decimal(number.value)):
        raise ToolExecutionError("invalid_numeric_source")
    return BoundNumber(
        **number.model_dump(), chunk_id=hit.chunk_id, document_id=hit.document_id,
        document_name=hit.document_name, chunk_index=hit.chunk_index,
        start_char=hit.start_char + quote_start,
        end_char=hit.start_char + quote_start + len(number.quote),
    )


class CalculatorTool:
    name = "calculator"
    description = (
        "对两个有原文来源的十进制数做加、减、乘、除；先搜索取得证据，"
        "每个输入指定证据编号、含一个完整数值的逐字引文和数值字符串。"
        "不支持百分数、千分位、指数或单位换算。除法最多保留8位小数，采用半偶舍入。"
        "结果是程序计算，不能标成原文引用。"
    )
    argument_schema = CalculatorArguments

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        if not isinstance(arguments, CalculatorArguments):
            raise ToolExecutionError("invalid_arguments")
        try:
            # 替换适配器和 model_construct 都不能跳过业务执行边界的严格验证。
            validated = CalculatorArguments.model_validate(arguments.model_dump(warnings=False), strict=True)
        except (ValidationError, TypeError, ValueError):
            raise ToolExecutionError("invalid_arguments") from None
        left = _bind_number(validated.left, context)
        right = _bind_number(validated.right, context)
        try:
            value, rounded, places = decimal_operation(validated.operation, left.value, right.value)
            calculation = CalculationResult(operation=validated.operation, left=left, right=right,
                                            value=value, rounded=rounded, decimal_places=places)
        except DivisionByZero:
            raise ToolExecutionError("division_by_zero") from None
        except (InvalidOperation, Overflow, ValidationError):
            raise ToolExecutionError("calculation_limit") from None
        return ToolOutput(hits=(), calculation=calculation)
