"""按正文字符预算选择完整块；业务回答与离线评测复用同一规则。"""

from collections.abc import Sequence
from typing import Protocol, TypeVar


DEFAULT_CONTEXT_CHARACTERS = 12000
MAX_CONTEXT_CHARACTERS = 50000


class AnswerContextLimitError(ValueError):
    """预算无效，或没有文本块能够完整放入上下文。"""


class ContextItem(Protocol):
    @property
    def content(self) -> str: ...


Item = TypeVar("Item", bound=ContextItem)


def select_context(hits: Sequence[Item], budget: int) -> list[Item]:
    """保留排名和对象身份，不截断；放不下的块跳过，继续尝试后续块。

    budget 仅限制块正文的字符数，不包含问题、提示词或模型 token 开销。
    不读取配置，也不连接数据库或模型。
    """
    if type(budget) is not int or not 1 <= budget <= MAX_CONTEXT_CHARACTERS:
        raise AnswerContextLimitError("回答上下文预算配置无效")
    selected: list[Item] = []
    remaining = budget
    for hit in hits:
        if len(hit.content) <= remaining:
            selected.append(hit)
            remaining -= len(hit.content)
    if not selected:
        raise AnswerContextLimitError("检索文本块超过本次回答的上下文预算")
    return selected
