"""候选重排契约；模型只返回本次局部编号，不能修改或新增原文。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_RERANK_CANDIDATES = 20
MAX_RERANK_CHARACTERS = 20000


class RerankError(RuntimeError):
    """重排服务调用失败。"""


class RerankTimeoutError(RerankError):
    """重排等待超时，不自动重试。"""


class RerankOutputError(RerankError):
    """排序包含遗漏、重复或不存在的候选编号。"""


class RerankConfigurationError(RerankError):
    """重排服务未配置或配置无效。"""


class RerankInputError(ValueError):
    """输入超过有界重排契约；不能截断后假装完整处理。"""


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    candidate_id: int
    content: str


class RerankOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    ordered_ids: list[int] = Field(min_length=1, max_length=MAX_RERANK_CANDIDATES)


@dataclass(frozen=True, slots=True)
class RerankResult:
    order: RerankOrder
    response_model: str


class RerankTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    status: Literal["applied", "degraded", "skipped_empty"]
    reason: Literal["timeout", "invalid_output", "service_error"] | None = None
    candidate_count: int = Field(ge=0, le=MAX_RERANK_CANDIDATES)
    response_model: str | None = None
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)


def validate_candidates(question: str, candidates: Sequence[RerankCandidate]) -> None:
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        raise RerankInputError("重排问题必须是 1 至 2000 字符的非空文本")
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence) or not 1 <= len(candidates) <= MAX_RERANK_CANDIDATES:
        raise RerankInputError("重排候选必须为 1 至 20 段")
    for index, item in enumerate(candidates, 1):
        if (
            not isinstance(item, RerankCandidate) or type(item.candidate_id) is not int
            or item.candidate_id != index or not isinstance(item.content, str) or not item.content.strip()
        ):
            raise RerankInputError("候选必须含非空原文，并按顺序使用局部编号")
    if sum(len(item.content) for item in candidates) > MAX_RERANK_CHARACTERS:
        raise RerankInputError("重排候选原文总长度不能超过 20000 字符")


def validate_order(order: RerankOrder, count: int) -> RerankOrder:
    # 不信任其他适配器或通过 model_construct 绕过校验的对象。
    try:
        if not isinstance(order, RerankOrder):
            raise ValueError
        checked = RerankOrder.model_validate(order.model_dump(warnings=False), strict=True)
        if len(checked.ordered_ids) != count or set(checked.ordered_ids) != set(range(1, count + 1)):
            raise ValueError
        return checked
    except (ValidationError, ValueError, TypeError):
        raise RerankOutputError("重排输出必须是本次全部候选编号的完整排列") from None


class RerankProvider(Protocol):
    def rerank(self, question: str, candidates: Sequence[RerankCandidate]) -> RerankResult:
        """一次请求，返回全部候选的排列；不能凭空添加资料。"""
        ...
