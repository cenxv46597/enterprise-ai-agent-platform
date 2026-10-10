"""在知识库范围内进行精确余弦检索，并返回可追溯的来源。"""

from dataclasses import dataclass
import math
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, KnowledgeBase
from app.providers.embeddings import EmbeddingProfile, EmbeddingProvider, validate_embeddings


MAX_QUERY_CHARACTERS = 2000
MAX_TOP_K = 20


class RetrievalNotFoundError(RuntimeError):
    """知识库不存在或不属于指定工作空间。"""


class RetrievalConflictError(RuntimeError):
    """查询模型与知识库的向量配置不同。"""


class RetrievalStateError(RuntimeError):
    """已有向量数据或模型配置异常。"""


class RetrievalInputError(ValueError):
    """问题或返回条数超出当前检索接口的范围。"""


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    content: str
    start_char: int
    end_char: int
    distance: float


@dataclass(frozen=True, slots=True)
class SearchResult:
    metric: Literal["cosine_distance"]
    model: str | None
    dimensions: int | None
    results: list[SearchHit]


def _knowledge_base(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase:
    # 共享锁允许多个检索并发；短事务内阻止模型配置或归属被另一事务改写。
    knowledge_base = session.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
        .with_for_update(read=True)
    )
    if knowledge_base is None:
        raise RetrievalNotFoundError("知识库不存在")
    return knowledge_base


def _check_profile(knowledge_base: KnowledgeBase, profile: EmbeddingProfile) -> bool:
    saved = (
        knowledge_base.embedding_provider,
        knowledge_base.embedding_model,
        knowledge_base.embedding_dimensions,
    )
    if saved == (None, None, None):
        return False
    if any(value is None for value in saved) or saved[2] != 1024:
        raise RetrievalStateError("知识库向量配置不完整或维度异常")
    if saved != (profile.provider, profile.model, profile.dimensions):
        raise RetrievalConflictError("查询模型与知识库已有向量的模型配置不同")
    return True


def _has_candidates(session: Session, knowledge_base_id: UUID) -> bool:
    candidates = (
        select(Chunk.id)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "ready",
            Chunk.embedding.is_not(None),
        )
    )
    return bool(session.scalar(select(candidates.exists())))


def _result(profile: EmbeddingProfile | None, hits: list[SearchHit]) -> SearchResult:
    return SearchResult(
        metric="cosine_distance",
        model=profile.model if profile else None,
        dimensions=profile.dimensions if profile else None,
        results=hits,
    )


def search_knowledge_base(
    session: Session,
    provider: EmbeddingProvider,
    workspace_id: UUID,
    knowledge_base_id: UUID,
    query: str,
    *,
    top_k: int = 5,
) -> SearchResult:
    """调用方提供无活动事务的会话；模型请求期间不持有数据库事务。

    先在目标知识库内过滤，再排序和限制条数。结果是候选段落，不是答案
    正确率；当前原文与向量没有原地编辑接口，不进行全库损坏扫描。
    """
    if not isinstance(query, str):
        raise RetrievalInputError("检索问题必须是文本")
    query = query.strip()
    if not 1 <= len(query) <= MAX_QUERY_CHARACTERS:
        raise RetrievalInputError("检索问题为空或超过字符上限")
    if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
        raise RetrievalInputError("返回条数必须为 1 至 20 的整数")
    profile = provider.profile
    if profile.dimensions != 1024:
        raise RetrievalConflictError("当前检索向量固定为 1024 维")

    with session.begin():
        knowledge_base = _knowledge_base(session, workspace_id, knowledge_base_id)
        configured = _check_profile(knowledge_base, profile)
        has_candidates = _has_candidates(session, knowledge_base_id)
        if not has_candidates:
            return _result(profile if configured else None, [])
        if not configured:
            raise RetrievalStateError("知识库已有可检索向量，但缺少模型配置")

    session.expire_all()
    query_vector = validate_embeddings(
        provider.embed_texts([query]), expected_count=1, dimensions=profile.dimensions
    )[0]

    with session.begin():
        knowledge_base = _knowledge_base(session, workspace_id, knowledge_base_id)
        if not _check_profile(knowledge_base, profile):
            raise RetrievalConflictError("知识库模型配置在查询期间发生变化")
        distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
        statement = (
            select(
                Chunk.id.label("chunk_id"),
                Document.id.label("document_id"),
                Document.original_filename.label("document_name"),
                Chunk.chunk_index,
                Chunk.content,
                Chunk.start_char,
                Chunk.end_char,
                distance,
            )
            .join(Document, Chunk.document_id == Document.id)
            .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
            .where(
                KnowledgeBase.workspace_id == workspace_id,
                KnowledgeBase.id == knowledge_base_id,
                Document.status == "ready",
                Chunk.embedding.is_not(None),
            )
            .order_by(distance.asc(), Chunk.id.asc())
            .limit(top_k)
        )
        hits: list[SearchHit] = []
        for row in session.execute(statement).mappings():
            value = float(row["distance"])
            if not math.isfinite(value) or not 0 <= value <= 2:
                raise RetrievalStateError("已有向量无法计算有效的余弦距离")
            hits.append(SearchHit(**{**row, "distance": value}))
        return _result(profile, hits)
