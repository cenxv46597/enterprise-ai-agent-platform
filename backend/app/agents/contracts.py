"""智能体动作与可见状态；不要求模型输出内部思考过程。"""

from dataclasses import dataclass, field
import json
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.knowledge.answers import AnswerResult
from app.knowledge.retrieval import SearchHit
from app.providers.reranking import RerankTrace
from app.agents.calculation_contracts import AgentCalculation, CalculationResult
from app.agents.sourced_contracts import SourcedAgentCalculation, SourcedCalculation
from app.conversations.contracts import Clarification, PendingTask
from app.conversations.contracts import UserCount
from app.billing.contracts import BillingPlan


MAX_AGENT_STEPS = 8
MAX_AGENT_EVIDENCE = 20
MAX_AGENT_CHARACTERS = 12000


class AgentInputError(ValueError):
    """运行参数无效。"""


class PlannerError(RuntimeError):
    """动作决策服务故障或输出无效。"""


class PlannerTimeoutError(PlannerError):
    """动作决策请求超时。"""


class PlannerConfigurationError(PlannerError):
    """动作决策模型尚未配置。"""


class NativeToolCall(BaseModel):
    """只保存公开调用字段，供程序回传配对消息；不保存模型内部思考。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    arguments_json: str = Field(min_length=2, max_length=8192)
    content: str | None = Field(default=None, max_length=2000)


class ToolAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    action: Literal["tool"]
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(max_length=10)
    native_call: NativeToolCall | None = None


class FinishAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    action: Literal["finish"]


AgentDecision = Annotated[ToolAction | FinishAction, Field(discriminator="action")]
DECISION_ADAPTER = TypeAdapter(AgentDecision)


def validate_decision(decision: object) -> ToolAction | FinishAction:
    try:
        if not isinstance(decision, (ToolAction, FinishAction)):
            raise ValueError
        validated = DECISION_ADAPTER.validate_python(decision.model_dump(warnings=False), strict=True)
        if isinstance(validated, ToolAction) and validated.native_call is not None:
            # 防止替换适配器让“执行参数”与“回传调用参数”不一致；编号重复另由运行控制器检查。
            raw_arguments = json.loads(validated.native_call.arguments_json)
            if (json.dumps(raw_arguments, sort_keys=True, allow_nan=False) !=
                    json.dumps(validated.arguments, sort_keys=True, allow_nan=False)):
                raise ValueError
        return validated
    except (ValidationError, TypeError, ValueError, RecursionError):
        raise PlannerError("动作决策输出不符合契约") from None


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    step: int = Field(ge=1, le=MAX_AGENT_STEPS)
    action: Literal["tool", "finish", "decision"]
    tool_name: str | None = None
    tool_call_id: str | None = None
    query: str | None = None
    status: Literal["success", "error", "stopped"]
    error_code: str | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    calculation_id: int | None = None
    planner_ms: float = Field(ge=0, allow_inf_nan=False)
    execution_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    reranking: RerankTrace | None = None


@dataclass(frozen=True, slots=True)
class ToolContext:
    """工具只读取本次运行已取得的不可变快照。"""
    evidence: tuple[SearchHit, ...] = ()
    user_inputs: tuple[UserCount, ...] = ()
    calculations: tuple[AgentCalculation | SourcedAgentCalculation, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolOutput:
    hits: tuple[SearchHit, ...]
    reranking: RerankTrace | None = None
    calculation: CalculationResult | SourcedCalculation | None = None
    clarification: Clarification | None = None
    billing_plan: BillingPlan | None = None
    clarifications: tuple[Clarification, ...] = ()


class ToolExecutionError(RuntimeError):
    """可供模型观察的固定工具故障类型，不携带原始异常。"""
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AgentEvidence(BaseModel):
    """运行内编号与真实块的映射；不返回整个正文或磁盘路径。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    evidence_id: int = Field(ge=1, le=MAX_AGENT_EVIDENCE)
    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: UUID
    status: Literal["completed", "stopped", "failed", "waiting_for_user"]
    stop_reason: str | None
    steps: list[AgentStep]
    result: AnswerResult | None
    evidence: list[AgentEvidence]
    calculations: list[AgentCalculation | SourcedAgentCalculation] = field(default_factory=list)
    pending_task: PendingTask | None = None


class PlannerProvider(Protocol):
    def decide(self, state: dict) -> ToolAction | FinishAction:
        """只提出一个动作；状态是本次运行的有界副本。"""
        ...
