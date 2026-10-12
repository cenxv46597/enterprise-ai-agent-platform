"""会话数据契约；用户输入与企业原文始终分开记录。"""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import field_validator
from app.billing.contracts import BillingPlan, BillingResult
from app.memory.contracts import PreferenceRead


MAX_TURNS = 200
MEMORY_TURNS = 3
MEMORY_CHARACTERS = 4000
CountUnit = Literal["次", "个月", "人", "件"]


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    field_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=80)
    unit: CountUnit
    evidence_ids: list[int] = Field(min_length=1, max_length=20)
    rule_quote: str = Field(min_length=1, max_length=400, description="已知规则的连续逐字原文；必须存在于指定证据中")
    min_value: int = Field(default=0, ge=0, le=9999)
    max_value: int = Field(default=9999, ge=0, le=9999)

    @model_validator(mode="after")
    def valid_references(self) -> Self:
        if any(type(item) is not int or not 1 <= item <= 20 for item in self.evidence_ids):
            raise ValueError("证据编号无效")
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or not self.label.strip() or self.min_value > self.max_value:
            raise ValueError("待补字段无效")
        return self


class EvidenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    chunk_id: UUID
    document_id: UUID
    document_name: str = Field(min_length=1, max_length=255)
    chunk_index: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=12000)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_span(self) -> Self:
        if self.end_char - self.start_char != len(self.content):
            raise ValueError("原文快照范围无效")
        return self


class UserCount(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    field_name: str
    value: int = Field(ge=0, le=9999)
    unit: CountUnit
    request_id: UUID
    raw_text: str = Field(min_length=1, max_length=2000)
    provenance: Literal["user_message"] = "user_message"


class PendingTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    question: str = Field(min_length=1, max_length=2000)
    clarification: Clarification
    evidence: list[EvidenceSnapshot] = Field(min_length=1, max_length=20)
    confirmed_inputs: list[UserCount] = Field(default_factory=list, max_length=2)
    clarifications: list[Clarification] = Field(default_factory=list, max_length=2)
    billing_plan: BillingPlan | None = None
    billing_result: BillingResult | None = None

    def requirements(self) -> list[Clarification]:
        return self.clarifications or [self.clarification]

    def inputs_complete(self) -> bool:
        return len(self.confirmed_inputs) == len(self.requirements())

    @model_validator(mode="after")
    def bounded_evidence(self) -> Self:
        if sum(len(item.content) for item in self.evidence) > 12000:
            raise ValueError("原文快照超限")
        requirements = self.requirements()
        for requirement in [self.clarification, *requirements]:
            if any(item > len(self.evidence) for item in requirement.evidence_ids):
                raise ValueError("待补条件引用无效")
            if not any(requirement.rule_quote in self.evidence[index - 1].content for index in requirement.evidence_ids):
                raise ValueError("已知规则引文无效")
        if len({item.chunk_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("重复原文快照")
        by_name = {item.field_name: item for item in requirements}
        if len(by_name) != len(requirements) or self.clarification not in requirements:
            raise ValueError("重复或无效的待补字段")
        if len({item.field_name for item in self.confirmed_inputs}) != len(self.confirmed_inputs):
            raise ValueError("重复确认条件")
        if len({item.request_id for item in self.confirmed_inputs}) != len(self.confirmed_inputs):
            raise ValueError("不同待补条件必须分别来自已提交请求")
        for item in self.confirmed_inputs:
            requirement = by_name.get(item.field_name)
            if (requirement is None or item.unit != requirement.unit
                    or not requirement.min_value <= item.value <= requirement.max_value):
                raise ValueError("用户输入与待补字段不匹配")
        remaining = [item for item in requirements if item.field_name not in {value.field_name for value in self.confirmed_inputs}]
        if remaining and self.clarification != remaining[0]:
            raise ValueError("当前待补字段不是下一缺失条件")
        if self.billing_plan is None:
            if self.clarifications or self.billing_result is not None or len(self.confirmed_inputs) > 1:
                raise ValueError("旧单字段任务不能携带计费工作流状态")
        else:
            # 保存状态也核对两个价格的真实块、引文位置及来源元数据。
            plan = self.billing_plan
            if plan.rule_evidence_id > len(self.evidence):
                raise ValueError("计费原文编号无效")
            snapshot = self.evidence[plan.rule_evidence_id - 1]
            if plan.rule_quote.strip() != snapshot.content.strip():
                raise ValueError("计费规则不能截取部分原文块")
            for rate in (plan.monthly_fee, plan.visit_fee):
                offset = snapshot.content.find(rate.quote)
                if (rate.chunk_id, rate.document_id, rate.document_name, rate.chunk_index,
                    rate.start_char, rate.end_char) != (
                        snapshot.chunk_id, snapshot.document_id, snapshot.document_name, snapshot.chunk_index,
                        snapshot.start_char + offset, snapshot.start_char + offset + len(rate.quote)) or offset < 0:
                    raise ValueError("计费价格与原文快照不匹配")
            from app.conversations.counts import parse_count
            if any(parse_count(item.raw_text, item.unit) != item.value for item in self.confirmed_inputs):
                raise ValueError("计费数值与保存的用户原消息不匹配")
            expected = [("visit_count", "次", 0, 9999), ("billing_months", "个月", 1, 12)]
            if [(item.field_name, item.unit, item.min_value, item.max_value) for item in requirements] != expected:
                raise ValueError("计费条件结构不匹配")
            if any(item.rule_quote != self.billing_plan.rule_quote or item.evidence_ids != [self.billing_plan.rule_evidence_id]
                   for item in requirements):
                raise ValueError("计费条件规则来源不匹配")
            if self.billing_result is not None:
                if not self.inputs_complete() or self.billing_result.plan != self.billing_plan:
                    raise ValueError("计费结果与保存任务不匹配")
                values = {item.field_name: item.model_dump() for item in self.confirmed_inputs}
                if any(quantity.model_dump() != values.get(quantity.field_name)
                       for quantity in (self.billing_result.visit_count, self.billing_result.billing_months)):
                    raise ValueError("计费结果与确认输入不匹配")
        return self


class ConversationNotFoundError(ValueError):
    pass


class ConversationConflictError(ValueError):
    pass


class ConversationStateError(RuntimeError):
    pass


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_version: int = Field(ge=0, le=MAX_TURNS, strict=True)
    content: str = Field(min_length=1, max_length=2000, strict=True)
    mode: Literal["continue", "new_task", "execute"] = "continue"
    confirm_billing_rule: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def confirmation_only_on_execution(self) -> Self:
        if self.confirm_billing_rule and self.mode != "execute":
            raise ValueError("收费规则确认只用于显式执行")
        return self

    @field_validator("content", mode="before")
    @classmethod
    def strip_content(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RecentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    start_char: int
    end_char: int


class TaskRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    clarification: Clarification
    confirmed_inputs: list[UserCount]
    evidence: list[EvidenceReference]
    clarifications: list[Clarification] = Field(default_factory=list)
    billing_plan: BillingPlan | None = None


class ConversationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    version: int
    status: Literal["idle", "waiting_for_user", "ready", "completed"]
    task: TaskRead | None
    recent_messages: list[RecentMessage]
    billing_result: BillingResult | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    request_id: UUID
    version: int
    status: Literal["idle", "waiting_for_user", "ready", "completed"]
    reply: str
    task: TaskRead | None
    # 保留旧值以读取阶段 4.1 已提交请求的历史响应。
    execution_deferred: Literal["user_numeric_calculation_not_supported", "explicit_execution_required"] | None = None
    # 原有公开运行响应的 JSON 快照；原文正文和内部待补状态不在其中。
    run: dict | None = None
    billing_result: BillingResult | None = None
    # 旧轮次缺省为空；仅保存最终生成调用路径所用版本，不代表真实模型遵守。
    answer_preference: PreferenceRead | None = None
