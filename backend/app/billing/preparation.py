"""只绑定一个完整、无附加条款的标准收费原文；模型不能指定费率。"""

from pydantic import BaseModel, ValidationError

from app.agents.calculation_contracts import CalculationInput
from app.agents.calculator import _bind_number
from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.billing.contracts import BillingArguments, BillingPlan, BillingRate
from app.billing.rules import match_standard_rule
from app.conversations.contracts import Clarification
from app.knowledge.retrieval import SearchHit


def billing_requirements(plan: BillingPlan) -> list[Clarification]:
    common = {"evidence_ids": [plan.rule_evidence_id], "rule_quote": plan.rule_quote}
    return [
        Clarification(field_name="visit_count", label="同一计费期间的上门次数", unit="次", **common),
        Clarification(field_name="billing_months", label="同一计费期间的计费月数", unit="个月",
                      min_value=1, max_value=12, **common),
    ]


class PrepareBillingTool:
    name = "prepare_billing"
    description = (
        "准备受控的月费加上门次费估算。先搜索，指定一个证据编号及该原文块的完整正文。"
        "仅接受‘月费 N 元，每次上门服务费 M 元’标准规则（金额最多两位小数），"
        "若原文块含优惠、套餐或其他条款则拒绝；不能截取片段隐藏条件。"
        "程序固定公式为月费×计费月数＋上门次费×上门次数，逐项询问次数和1..12个月，"
        "再要求用户显式确认规则适用。金额、单位、公式均由程序绑定，不允许模型填写。"
        "这只是所选标准规则估算，不是实际账单或扣费。"
    )
    argument_schema = BillingArguments

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        try:
            if not isinstance(arguments, BillingArguments):
                raise ValueError
            args = BillingArguments.model_validate(arguments.model_dump(warnings=False), strict=True)
        except (ValidationError, ValueError, TypeError):
            raise ToolExecutionError("invalid_arguments") from None
        if (not isinstance(context, ToolContext) or not isinstance(context.evidence, tuple)
                or args.evidence_id > len(context.evidence)):
            raise ToolExecutionError("invalid_billing_source")
        hit = context.evidence[args.evidence_id - 1]
        if not isinstance(hit, SearchHit):
            raise ToolExecutionError("invalid_billing_source")
        if args.rule_quote.strip() != hit.content.strip():
            raise ToolExecutionError("incomplete_billing_rule")
        # 比较可忽略外围空白，保存必须采用真实原文，而不是模型重构的文本。
        if len(hit.content) > 400:
            raise ToolExecutionError("unsupported_billing_rule")
        try:
            match = match_standard_rule(hit.content)
        except ValueError:
            raise ToolExecutionError("unsupported_billing_rule") from None
        rates = []
        for name, unit in (("monthly", "元/个月"), ("visit", "元/次")):
            if len(match.group(name + "_quote")) > 200:
                raise ToolExecutionError("unsupported_billing_rule")
            bound = _bind_number(CalculationInput(evidence_id=args.evidence_id,
                                 quote=match.group(name + "_quote"), value=match.group(name)), context)
            rates.append(BillingRate(**bound.model_dump(), unit=unit))
        plan = BillingPlan(rule_evidence_id=args.evidence_id, rule_quote=hit.content,
                           monthly_fee=rates[0], visit_fee=rates[1])
        requirements = billing_requirements(plan)
        return ToolOutput((), clarification=requirements[0], billing_plan=plan,
                          clarifications=tuple(requirements))
