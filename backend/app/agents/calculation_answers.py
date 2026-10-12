"""确定性展示计算结果与输入出处；不把计算结果包装为文档原文。"""

from collections.abc import Sequence

from app.agents.calculation_contracts import AgentCalculation
from app.agents.sourced_contracts import BoundResult, BoundUser, SourcedAgentCalculation
from app.knowledge.answers import AnswerCitation, AnswerResult


SYMBOLS = {"add": "+", "subtract": "−", "multiply": "×", "divide": "÷"}


def answer_from_calculations(calculations: Sequence[AgentCalculation | SourcedAgentCalculation]) -> AnswerResult:
    """仅陈述给定原文数值的算术，不推断业务政策、单位换算或任务语义。"""
    sourced = any(isinstance(item, SourcedAgentCalculation) for item in calculations)
    lines = (["以下仅为已绑定来源的算术结果，不验证单位、计费期间或业务公式，也不代表原业务任务已完成："]
             if sourced else ["按以下原文数值计算："])
    citations: list[AnswerCitation] = []
    seen: set[tuple] = set()
    seen_users: set[tuple] = set()
    for calculation in calculations:
        lines.append(f"计算 {calculation.calculation_id}：{calculation.left.value} "
                     f"{SYMBOLS[calculation.operation]} {calculation.right.value} = {calculation.value}。")
        if calculation.rounded:
            lines.append(f"该除法结果保留最多 {calculation.decimal_places} 位小数，采用四舍六入五成双。")
        for operand in (calculation.left, calculation.right):
            if isinstance(operand, BoundUser):
                key = (operand.field_name, operand.request_id)
                if key not in seen_users:
                    seen_users.add(key)
                    lines.append(f"用户输入来源：{operand.field_name}，请求 {operand.request_id}，"
                                 f"原始回复“{operand.raw_text}”，确认值 {operand.value}{operand.unit}；不是企业原文。")
                continue
            if isinstance(operand, BoundResult):
                lines.append(f"派生输入来源：本次运行的计算 {operand.calculation_id}，"
                             f"值 {operand.value}；不是企业原文。")
                if operand.rounded:
                    lines.append("该输入已由前一步舍入，本步使用保存的舍入值，不恢复精确分数。")
                continue
            key = (operand.chunk_id, operand.quote, operand.start_char, operand.end_char)
            if key in seen:
                continue
            seen.add(key)
            citations.append(AnswerCitation(operand.chunk_id, operand.document_id, operand.document_name,
                                           operand.chunk_index, operand.quote, operand.start_char, operand.end_char))
    return AnswerResult("\n".join(lines), False, citations)
