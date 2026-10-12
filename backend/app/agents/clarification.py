"""仅在会话入口注册的待补条件工具；停止本轮并交给持久会话层。"""

from pydantic import BaseModel, ValidationError

from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.conversations.contracts import Clarification


class ClarificationTool:
    name = "ask_user"
    description = (
        "已取得收费或业务规则原文，但缺少用户才能提供的次数、月数、人数或件数时，"
        "请求补充一个非负整数（0..9999）。field_name 用英文标识，label 说明对象和时间范围，"
        "unit 指明单位，evidence_ids 指向本轮已搜索的规则原文，rule_quote 是其中一段连续逐字规则。"
        "程序先展示该规则，再保存待办并结束本轮。"
        "不能用追问掩盖搜索故障或原文缺失；不会自动计算总额。"
    )
    argument_schema = Clarification

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        try:
            if not isinstance(arguments, Clarification):
                raise ValueError
            clarification = Clarification.model_validate(arguments.model_dump(warnings=False), strict=True)
        except (ValidationError, ValueError, TypeError):
            raise ToolExecutionError("invalid_arguments") from None
        if any(index > len(context.evidence) for index in clarification.evidence_ids):
            raise ToolExecutionError("invalid_clarification_source")
        if not any(clarification.rule_quote in context.evidence[index - 1].content
                   for index in clarification.evidence_ids):
            raise ToolExecutionError("invalid_clarification_source")
        return ToolOutput((), clarification=clarification)
