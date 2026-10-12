"""显式确认之后的固定三步计算；此阶段不调用模型、不执行外部写操作。"""

from decimal import Decimal
from uuid import UUID

from app.agents.arithmetic import decimal_operation
from app.billing.contracts import BillingQuantity, BillingResult, BillingStep
from app.conversations.contracts import PendingTask


def execute_billing(task: PendingTask, confirmation_request_id: UUID) -> BillingResult:
    # 再验证保存状态，不能依赖调用者传入的 model_construct 对象。
    task = PendingTask.model_validate_json(task.model_dump_json())
    if task.billing_plan is None or not task.inputs_complete() or task.billing_result is not None:
        raise ValueError("标准计费任务尚未就绪或已经完成")
    plan = task.billing_plan
    values = {item.field_name: BillingQuantity.model_validate_json(item.model_dump_json())
              for item in task.confirmed_inputs}
    months, visits = values["billing_months"], values["visit_count"]
    monthly = decimal_operation("multiply", plan.monthly_fee.value, str(months.value))[0]
    visit = decimal_operation("multiply", plan.visit_fee.value, str(visits.value))[0]
    total = decimal_operation("add", monthly, visit)[0]
    steps = [
        BillingStep(step=1, operation="multiply", left_ref="monthly_fee", right_ref="billing_months",
                    left_value=plan.monthly_fee.value, right_value=str(months.value), value=monthly),
        BillingStep(step=2, operation="multiply", left_ref="visit_fee", right_ref="visit_count",
                    left_value=plan.visit_fee.value, right_value=str(visits.value), value=visit),
        BillingStep(step=3, operation="add", left_ref="step_1", right_ref="step_2",
                    left_value=monthly, right_value=visit, value=total),
    ]
    return BillingResult(plan=plan, visit_count=visits, billing_months=months,
                         confirmation_request_id=confirmation_request_id, monthly_subtotal=monthly,
                         visit_subtotal=visit, total=total, steps=steps)


def billing_reply(result: BillingResult) -> str:
    plan = result.plan
    return (
        f"已按你确认适用的标准规则估算：{plan.monthly_fee.value} 元/个月 × {result.billing_months.value} 个月"
        f" = {Decimal(result.monthly_subtotal):.2f} 元；{plan.visit_fee.value} 元/次 × {result.visit_count.value} 次"
        f" = {Decimal(result.visit_subtotal):.2f} 元；合计 {Decimal(result.total):.2f} 元。"
        f"收费原文：“{plan.rule_quote}”（{plan.monthly_fee.document_name}，第 {plan.monthly_fee.chunk_index} 块）。"
        "次数和月数来自你的补充，金额合计由程序计算，不是原文直接给出的数值。"
        "本结果仅为所选标准规则估算，不覆盖优惠、套餐、税费或其他条款，不是实际账单，未产生扣费。"
    )
