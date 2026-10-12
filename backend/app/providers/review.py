"""离线评审契约：模型建议与人工验收分别记录。"""

from dataclasses import dataclass
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.answers import CaseReview, CheckReview, RecordedAnswer, RubricCheck


class ReviewError(RuntimeError):
    """评审未完成；不能把技术失败记成答案错误。"""


class ReviewTimeoutError(ReviewError):
    """评审模型超时。"""


class ReviewConfigurationError(ReviewError):
    """独立的评审配置缺失或无效。"""


class ReviewTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str = Field(min_length=1, max_length=80)
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str = Field(min_length=1, max_length=2000)
    response: RecordedAnswer
    public_text: str = Field(min_length=1, max_length=50000)
    checks: list[RubricCheck] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if not self.question.strip() or not self.public_text.strip():
            raise ValueError("问题与评审资料不能为空")
        if len({item.id for item in self.checks}) != len(self.checks):
            raise ValueError("检查项编号不能重复")
        if any(item.evidence not in self.public_text for item in self.checks):
            raise ValueError("检查项依据必须出现在公开原文中")
        return self


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    checks: dict[str, CheckReview] = Field(min_length=1, max_length=20)


class TokenUsage(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def check_total(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("服务报告的 token 总数不一致")
        return self


@dataclass(frozen=True, slots=True)
class ReviewResult:
    review: CaseReview
    response_model: str
    usage: TokenUsage | None


class ReviewProvider(Protocol):
    def review_answer(self, task: ReviewTask) -> ReviewResult:
        """返回一次模型辅助建议；调用方再次验证对象绑定和对应片段。"""
        ...
