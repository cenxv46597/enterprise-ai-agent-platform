"""在事务外生成向量，在短事务中校验快照并整批保存。"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, KnowledgeBase
from app.providers.embeddings import EmbeddingError, EmbeddingProfile, EmbeddingProvider, validate_embeddings


class EmbeddingNotFoundError(RuntimeError):
    """文档或知识库不存在。"""


class EmbeddingConflictError(RuntimeError):
    """文档状态、模型配置或切块快照与请求不匹配。"""


class EmbeddingStateError(RuntimeError):
    """已保存向量与文档状态不一致。"""


class EmbeddingLimitError(ValueError):
    """文档超过当前同步向量生成的处理上限。"""


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    document_id: UUID
    status: str
    model: str
    dimensions: int
    chunk_count: int
    reused: bool


def _check_profile(knowledge_base: KnowledgeBase, profile: EmbeddingProfile) -> None:
    saved = (
        knowledge_base.embedding_provider,
        knowledge_base.embedding_model,
        knowledge_base.embedding_dimensions,
    )
    if saved != (None, None, None) and saved != (profile.provider, profile.model, profile.dimensions):
        raise EmbeddingConflictError("知识库已有向量使用不同的模型配置")


def _check_document(document: Document, rows: list[Chunk], profile: EmbeddingProfile) -> bool:
    if document.status not in {"parsed", "ready"}:
        raise EmbeddingConflictError("文档尚未成功解析")
    if not rows or document.chunking_version is None:
        raise EmbeddingConflictError("请先为文档保存文本块")
    present = [chunk.embedding is not None for chunk in rows]
    if any(present):
        if not all(present) or document.status != "ready":
            raise EmbeddingStateError("向量结果不完整或与文档状态不一致")
        try:
            validate_embeddings(
                [[float(value) for value in chunk.embedding] for chunk in rows],
                expected_count=len(rows), dimensions=profile.dimensions,
            )
        except EmbeddingError:
            raise EmbeddingStateError("数据库已有向量不满足检索要求") from None
        return True
    if document.status == "ready":
        raise EmbeddingStateError("文档已标记可检索，但缺少向量")
    return False


def _result(document_id: UUID, profile: EmbeddingProfile, count: int, *, reused: bool) -> EmbeddingResult:
    return EmbeddingResult(document_id, "ready", profile.model, profile.dimensions, count, reused)


def _rows(session: Session, document_id: UUID) -> list[Chunk]:
    return list(
        session.scalars(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            .execution_options(populate_existing=True)
        )
    )


def embed_document_chunks(
    session: Session,
    provider: EmbeddingProvider,
    document_id: UUID,
    *,
    max_chunks: int = 200,
) -> EmbeddingResult:
    """调用方提供无活动事务的会话；模型失败保留解析状态和文本块。

    数据库结果支持幂等，并发请求仍可能产生重复的外部模型调用。
    当前知识库只允许一种模型身份，原文和切块不支持原地编辑。
    """
    if type(max_chunks) is not int or max_chunks <= 0:
        raise ValueError("向量生成块数上限必须为正整数")
    profile = provider.profile
    if profile.dimensions != 1024:
        raise EmbeddingConflictError("当前向量列固定为 1024 维")

    with session.begin():
        knowledge_base_id = session.scalar(
            select(Document.knowledge_base_id).where(Document.id == document_id)
        )
        if knowledge_base_id is None:
            raise EmbeddingNotFoundError("文档不存在")
        # 短暂锁行，避免 READ COMMITTED 下先读旧状态、再读到另一请求的新向量。
        knowledge_base = session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            .execution_options(populate_existing=True).with_for_update()
        )
        document = session.scalar(
            select(Document).where(Document.id == document_id)
            .execution_options(populate_existing=True).with_for_update()
        )
        if knowledge_base is None or document is None:
            raise EmbeddingNotFoundError("文档或知识库不存在")
        if document.knowledge_base_id != knowledge_base_id:
            raise EmbeddingConflictError("文档归属在读取期间发生变化")
        _check_profile(knowledge_base, profile)
        rows = _rows(session, document_id)
        if _check_document(document, rows, profile):
            if knowledge_base.embedding_model is None:
                raise EmbeddingStateError("已有向量但缺少知识库模型配置")
            return _result(document_id, profile, len(rows), reused=True)
        if len(rows) > max_chunks:
            raise EmbeddingLimitError("文档超过同步向量生成块数上限")
        chunk_config = (document.chunk_size, document.chunk_overlap, document.chunking_version)
        snapshot = [(chunk.id, chunk.chunk_index, chunk.content) for chunk in rows]

    # 不把 ORM 对象当作新鲜快照。下一事务必须重新读取数据库中的状态。
    session.expire_all()
    vectors = validate_embeddings(
        provider.embed_texts([content for _, _, content in snapshot]),
        expected_count=len(snapshot),
        dimensions=profile.dimensions,
    )

    with session.begin():
        # 同一知识库的模型配置需要串行检查；所有调用保持先知识库后文档的锁顺序。
        knowledge_base = session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            .execution_options(populate_existing=True).with_for_update()
        )
        document = session.scalar(
            select(Document).where(Document.id == document_id)
            .execution_options(populate_existing=True).with_for_update()
        )
        if knowledge_base is None or document is None:
            raise EmbeddingNotFoundError("文档或知识库不存在")
        if document.knowledge_base_id != knowledge_base_id:
            raise EmbeddingConflictError("文档归属在生成期间发生变化")
        if (document.chunk_size, document.chunk_overlap, document.chunking_version) != chunk_config:
            raise EmbeddingConflictError("切块配置在向量生成期间发生变化")
        _check_profile(knowledge_base, profile)
        rows = _rows(session, document_id)
        current_snapshot = [(chunk.id, chunk.chunk_index, chunk.content) for chunk in rows]
        if current_snapshot != snapshot:
            raise EmbeddingConflictError("文本块在向量生成期间发生变化")
        if _check_document(document, rows, profile):
            if knowledge_base.embedding_model is None:
                raise EmbeddingStateError("已有向量但缺少知识库模型配置")
            return _result(document_id, profile, len(rows), reused=True)

        for chunk, vector in zip(rows, vectors):
            chunk.embedding = vector
        knowledge_base.embedding_provider = profile.provider
        knowledge_base.embedding_model = profile.model
        knowledge_base.embedding_dimensions = profile.dimensions
        document.status = "ready"
        document.error_code = None
        session.flush()
        return _result(document_id, profile, len(rows), reused=False)
