"""把检索结果作为证据生成答案，并核对模型引用的真实出处。"""

from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.knowledge.context import (
    AnswerContextLimitError,
    DEFAULT_CONTEXT_CHARACTERS,
    MAX_CONTEXT_CHARACTERS,
    select_context as _select_context,
)
from app.knowledge.retrieval import SearchHit, search_knowledge_base
from app.providers.embeddings import EmbeddingProvider
from app.providers.generation import (
    GeneratedAnswer,
    GenerationError,
    GenerationProvider,
    GenerationSource,
)


ABSTENTION_MESSAGE = "目前检索到的资料不足以确认这个问题的答案。"


@dataclass(frozen=True, slots=True)
class AnswerCitation:
    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    quote: str
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: str
    abstained: bool
    citations: list[AnswerCitation]


def _validate_citations(draft: GeneratedAnswer, selected: list[SearchHit]) -> list[AnswerCitation]:
    citations: list[AnswerCitation] = []
    seen: set[tuple[int, str]] = set()
    for citation in draft.citations:
        if citation.source_id > len(selected):
            raise GenerationError("生成结果引用了本次证据之外的来源")
        key = (citation.source_id, citation.quote)
        if key in seen:
            raise GenerationError("生成结果包含重复引用")
        seen.add(key)
        hit = selected[citation.source_id - 1]
        offset = hit.content.find(citation.quote)
        if offset < 0:
            raise GenerationError("生成结果的引文不在对应原文中")
        # 同一块内重复引文选第一次出现；这里只保证该范围确实含有引文。
        start_char = hit.start_char + offset
        citations.append(
            AnswerCitation(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_name=hit.document_name,
                chunk_index=hit.chunk_index,
                quote=citation.quote,
                start_char=start_char,
                end_char=start_char + len(citation.quote),
            )
        )
    return citations


def answer_knowledge_base(
    session: Session,
    embedding_provider: EmbeddingProvider,
    generation_provider: GenerationProvider,
    workspace_id: UUID,
    knowledge_base_id: UUID,
    question: str,
    *,
    top_k: int = 5,
    max_context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
) -> AnswerResult:
    """按本次检索的文本快照回答；模型请求期间没有活动数据库事务。

    引文存在不代表答案语义一定被支持。提示词要求证据不足或冲突时弃答，
    真实模型的证据判断和回答质量仍需使用标注问题评估。
    """
    if type(max_context_characters) is not int or not 1 <= max_context_characters <= MAX_CONTEXT_CHARACTERS:
        raise AnswerContextLimitError("回答上下文预算配置无效")
    search_result = search_knowledge_base(
        session, embedding_provider, workspace_id, knowledge_base_id, question, top_k=top_k
    )
    if not search_result.results:
        return AnswerResult(ABSTENTION_MESSAGE, True, [])
    selected = _select_context(search_result.results, max_context_characters)
    sources = tuple(
        GenerationSource(source_id=index, content=hit.content)
        for index, hit in enumerate(selected, 1)
    )
    draft = generation_provider.generate_answer(question.strip(), sources)
    # 在业务边界再次校验，其他 provider 也必须遵守相同规则。
    try:
        if not isinstance(draft, GeneratedAnswer):
            raise GenerationError("生成服务返回的草稿类型无效")
        draft = GeneratedAnswer.model_validate(draft.model_dump(warnings=False), strict=True)
    except (ValidationError, TypeError, ValueError):
        raise GenerationError("生成服务返回的草稿格式无效") from None
    if draft.abstained:
        return AnswerResult(ABSTENTION_MESSAGE, True, [])
    return AnswerResult(draft.answer, False, _validate_citations(draft, selected))
