"""工作流请求、已核验原文检查点与执行回执。"""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.answers import AnswerRead
from app.conversations.contracts import EvidenceSnapshot
from app.knowledge.retrieval import SearchHit, SearchStrategy


MAX_WORKFLOW_ATTEMPTS = 5
MAX_WORKFLOW_VERSION = 32
WORKFLOW_LEASE_SECONDS = 900
MAX_CHECKPOINT_CHARACTERS = 12000
MAX_CHECKPOINT_BYTES = 100000
WorkflowStatus = Literal["pending", "running", "failed", "completed"]
WorkflowStep = Literal["retrieve", "generate", "done"]
WorkflowErrorCode = Literal[
    "embedding_timeout", "embedding_error", "retrieval_conflict", "retrieval_invalid",
    "generation_configuration", "generation_timeout", "generation_error", "evidence_changed",
    "checkpoint_invalid", "context_limit", "service_error", "execution_interrupted",
]
RETRYABLE_ERRORS = frozenset({"embedding_timeout", "embedding_error", "generation_configuration",
                            "generation_timeout", "generation_error", "service_error", "execution_interrupted"})


class WorkflowNotFoundError(RuntimeError):
    pass


class WorkflowConflictError(RuntimeError):
    pass


class WorkflowStateError(RuntimeError):
    def __init__(self, code: WorkflowErrorCode) -> None:
        self.code = code
        super().__init__(code)


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    question: str = Field(min_length=1, max_length=2000, strict=True)
    strategy: SearchStrategy = "vector"

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class WorkflowExecute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    expected_version: int = Field(ge=0, le=MAX_WORKFLOW_VERSION, strict=True)
    recover_interrupted: bool = Field(default=False, strict=True)


class WorkflowEvidence(EvidenceSnapshot):
    distance: float = Field(ge=0, le=2, allow_inf_nan=False)
    fusion_score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vector_rank: int | None = Field(default=None, ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    lexical_score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    retrieval_rank: int | None = Field(default=None, ge=1)
    rerank_rank: int | None = Field(default=None, ge=1)

    @classmethod
    def from_hit(cls, hit: SearchHit) -> "WorkflowEvidence":
        return cls(**{name: getattr(hit, name) for name in cls.model_fields})

    def to_hit(self) -> SearchHit:
        return SearchHit(**self.model_dump())


class WorkflowCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    workspace_id: UUID
    knowledge_base_id: UUID
    strategy: SearchStrategy
    hits: list[WorkflowEvidence] = Field(max_length=5)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("检查点版本无效")
        return value

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({hit.chunk_id for hit in self.hits}) != len(self.hits):
            raise ValueError("检查点包含重复块")
        if sum(len(hit.content) for hit in self.hits) > MAX_CHECKPOINT_CHARACTERS:
            raise ValueError("检查点正文超限")
        return self


class WorkflowAnswer(AnswerRead):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)
    answer: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def valid_answer(self) -> Self:
        if len(self.citations) > 20 or self.reranking is not None:
            raise ValueError("工作流结果超出首版契约")
        if self.abstained and self.citations or not self.abstained and not self.citations:
            raise ValueError("工作流结果与引用不一致")
        return self


class WorkflowRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    workflow_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    strategy: SearchStrategy
    max_context_characters: int = Field(ge=1, le=MAX_CHECKPOINT_CHARACTERS)
    status: WorkflowStatus
    next_step: WorkflowStep
    version: int = Field(ge=0, le=MAX_WORKFLOW_VERSION)
    attempt_count: int = Field(ge=0, le=MAX_WORKFLOW_ATTEMPTS)
    checkpoint: WorkflowCheckpoint | None
    error_code: WorkflowErrorCode | None
    retryable: bool
    active_request_id: UUID | None
    lease_expires_at: AwareDatetime | None
    result: WorkflowAnswer | None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        running = self.status == "running"
        if running != (self.active_request_id is not None and self.lease_expires_at is not None):
            raise ValueError("执行权状态不一致")
        if not running and (self.active_request_id is not None or self.lease_expires_at is not None):
            raise ValueError("非执行中状态不应持有执行权")
        if (self.status == "completed") != (self.next_step == "done" and self.result is not None):
            raise ValueError("完成状态不一致")
        if self.status != "completed" and (self.next_step == "done" or self.result is not None):
            raise ValueError("未完成状态不应有结果")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("失败状态不一致")
        if (self.next_step in ("generate", "done")) != (self.checkpoint is not None):
            raise ValueError("步骤与检查点不一致")
        if self.checkpoint is not None and (
            self.checkpoint.workspace_id != self.workspace_id
            or self.checkpoint.knowledge_base_id != self.knowledge_base_id
            or self.checkpoint.strategy != self.strategy
        ):
            raise ValueError("检查点范围不一致")
        if self.checkpoint is not None and sum(len(hit.content) for hit in self.checkpoint.hits) > self.max_context_characters:
            raise ValueError("检查点超出本次任务冻结的正文预算")
        if self.status == "pending" and (self.next_step != "retrieve" or self.attempt_count != 0):
            raise ValueError("初始状态无效")
        if self.status != "pending" and self.attempt_count == 0:
            raise ValueError("执行状态缺少尝试记录")
        expected_retryable = (self.status == "failed" and self.error_code in RETRYABLE_ERRORS
                              and self.attempt_count < MAX_WORKFLOW_ATTEMPTS)
        if self.retryable != expected_retryable:
            raise ValueError("重试标记不一致")
        return self


class WorkflowExecutionRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    request_id: UUID
    attempt_number: int = Field(ge=1, le=MAX_WORKFLOW_ATTEMPTS)
    status: Literal["completed", "failed", "interrupted"]
    workflow: WorkflowRead

    @model_validator(mode="after")
    def consistent_receipt(self) -> Self:
        expected = "completed" if self.status == "completed" else "failed"
        if self.workflow.status != expected or self.workflow.attempt_count != self.attempt_number:
            raise ValueError("回执状态或尝试编号不一致")
        return self
