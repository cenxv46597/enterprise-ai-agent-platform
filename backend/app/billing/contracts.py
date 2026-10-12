"""独立业务契约；单位与公式属于受控流程，而不是模型自由填写的参数。"""

from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.arithmetic import decimal_operation
from app.agents.calculation_contracts import BoundNumber
from app.billing.rules import match_standard_rule


class BillingContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BillingArguments(BillingContract):
    evidence_id: int = Field(ge=1, le=20)
    rule_quote: str = Field(min_length=1, max_length=400)


class BillingRate(BoundNumber):
    unit: Literal["元/个月", "元/次"]


class BillingPlan(BillingContract):
    workflow: Literal["monthly_visit_v1"] = "monthly_visit_v1"
    rule_evidence_id: int = Field(ge=1, le=20)
    rule_quote: str = Field(min_length=1, max_length=400)
    monthly_fee: BillingRate
    visit_fee: BillingRate

    @model_validator(mode="after")
    def standard_units_and_prices(self) -> Self:
        match = match_standard_rule(self.rule_quote)
        for rate, name, unit in ((self.monthly_fee, "monthly", "元/个月"), (self.visit_fee, "visit", "元/次")):
            if (rate.unit != unit or rate.evidence_id != self.rule_evidence_id
                    or rate.quote != match.group(name + "_quote") or Decimal(rate.value) != Decimal(match.group(name))):
                raise ValueError("收费角色、原文数值或单位不匹配")
        if self.monthly_fee.chunk_id != self.visit_fee.chunk_id:
            raise ValueError("标准规则必须来自同一完整原文块")
        return self


class BillingQuantity(BillingContract):
    field_name: Literal["visit_count", "billing_months"]
    value: int = Field(ge=0, le=9999)
    unit: Literal["次", "个月"]
    request_id: UUID
    raw_text: str = Field(min_length=1, max_length=2000)
    provenance: Literal["user_message"] = "user_message"

    @model_validator(mode="after")
    def quantity_role(self) -> Self:
        from app.conversations.counts import parse_count
        if parse_count(self.raw_text, self.unit) != self.value:
            raise ValueError("确认数值与用户原消息不一致")
        if self.field_name == "billing_months":
            if self.unit != "个月" or not 1 <= self.value <= 12:
                raise ValueError("计费月份必须为 1..12 个月")
        elif self.unit != "次":
            raise ValueError("上门次数单位不匹配")
        return self


class BillingStep(BillingContract):
    step: int = Field(ge=1, le=3)
    operation: Literal["multiply", "add"]
    left_ref: str
    right_ref: str
    left_value: str
    right_value: str
    value: str
    unit: Literal["元"] = "元"
    provenance: Literal["program_calculation"] = "program_calculation"


class BillingResult(BillingContract):
    workflow: Literal["monthly_visit_v1"] = "monthly_visit_v1"
    scope: Literal["confirmed_standard_rule_estimate"] = "confirmed_standard_rule_estimate"
    currency: Literal["CNY"] = "CNY"
    plan: BillingPlan
    visit_count: BillingQuantity
    billing_months: BillingQuantity
    confirmation_request_id: UUID
    monthly_subtotal: str
    visit_subtotal: str
    total: str
    steps: list[BillingStep] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def fixed_formula(self) -> Self:
        if self.visit_count.field_name != "visit_count" or self.billing_months.field_name != "billing_months":
            raise ValueError("业务条件角色不匹配")
        if len({self.visit_count.request_id, self.billing_months.request_id, self.confirmation_request_id}) != 3:
            raise ValueError("两个补充条件与适用确认必须来自不同请求")
        monthly = decimal_operation("multiply", self.plan.monthly_fee.value, str(self.billing_months.value))[0]
        visit = decimal_operation("multiply", self.plan.visit_fee.value, str(self.visit_count.value))[0]
        total = decimal_operation("add", monthly, visit)[0]
        expected = [
            (1, "multiply", "monthly_fee", "billing_months", self.plan.monthly_fee.value, str(self.billing_months.value), monthly),
            (2, "multiply", "visit_fee", "visit_count", self.plan.visit_fee.value, str(self.visit_count.value), visit),
            (3, "add", "step_1", "step_2", monthly, visit, total),
        ]
        actual = [(item.step, item.operation, item.left_ref, item.right_ref, item.left_value, item.right_value, item.value)
                  for item in self.steps]
        if (self.monthly_subtotal, self.visit_subtotal, self.total) != (monthly, visit, total) or actual != expected:
            raise ValueError("计算记录与固定公式不一致")
        return self
