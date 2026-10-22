"""文档上传、状态查询、文本块与向量持久化接口。"""

from datetime import datetime
import logging
from typing import Annotated, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.workspaces import DbSession
from app.core.config import Settings
from app.db.models import Chunk, Document, KnowledgeBase
from app.knowledge.chunk_repository import (
    ChunkingConflictError,
    ChunkingLimitError,
    ChunkSourceError,
    ChunkStateError,
    DocumentNotFoundError,
    DocumentNotParsedError,
    MAX_PERSISTED_CHUNK_SIZE,
    persist_document_chunks,
)
from app.knowledge.chunking import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
from app.knowledge.ingestion import (
    InvalidFilenameError,
    UnsupportedFileTypeError,
    ingest_document,
)
from app.knowledge.embedding_repository import (
    EmbeddingConflictError,
    EmbeddingLimitError,
    EmbeddingNotFoundError,
    EmbeddingResult,
    EmbeddingStateError,
    embed_document_chunks,
)
from app.knowledge.storage import EmptyUploadError, LocalDocumentStore, UploadTooLargeError
from app.providers.compatible_embeddings import OpenAICompatibleEmbeddingProvider
from app.providers.embeddings import EmbeddingError, EmbeddingProvider, EmbeddingTimeoutError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents",
    tags=["documents"],
)


def get_document_store() -> LocalDocumentStore:
    return LocalDocumentStore(Settings().upload_root)


def get_max_upload_bytes() -> int:
    return Settings().max_upload_bytes


def get_embedding_provider() -> EmbeddingProvider:
    try:
        settings = Settings()
        if not settings.embedding_base_url or not settings.embedding_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="请先配置向量服务的接口地址和 API 密钥",
            )
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            timeout_seconds=settings.embedding_timeout_seconds,
            total_timeout_seconds=settings.embedding_total_timeout_seconds,
        )
    except (EmbeddingError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="向量服务配置无效，请检查本机配置",
        ) from None


StoreDep = Annotated[LocalDocumentStore, Depends(get_document_store)]
LimitDep = Annotated[int, Depends(get_max_upload_bytes)]
EmbeddingDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    status: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ChunkingRequest(BaseModel):
    chunk_size: int = Field(
        default=DEFAULT_CHUNK_SIZE, gt=0, le=MAX_PERSISTED_CHUNK_SIZE, strict=True
    )
    overlap: int = Field(
        default=DEFAULT_CHUNK_OVERLAP, ge=0, le=MAX_PERSISTED_CHUNK_SIZE, strict=True
    )

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.overlap >= self.chunk_size:
            raise ValueError("重叠长度必须小于块长度")
        return self


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    start_char: int
    end_char: int


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_chunks: int = Field(gt=0, le=1000, strict=True)


class EmbeddingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    status: str
    model: str
    dimensions: int
    chunk_count: int
    reused: bool


def _find_document_in_scope(
    session: Session, workspace_id: UUID, knowledge_base_id: UUID, document_id: UUID
) -> Document:
    document = session.scalar(
        select(Document)
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return document


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    file: Annotated[UploadFile, File()],
    session: DbSession,
    store: StoreDep,
    max_bytes: LimitDep,
) -> Document:
    parent_id = session.scalar(
        select(KnowledgeBase.id).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if parent_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    session.rollback()

    try:
        return ingest_document(
            session=session,
            store=store,
            source=file.file,
            filename=file.filename,
            knowledge_base_id=knowledge_base_id,
            max_bytes=max_bytes,
        )
    except InvalidFilenameError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="文件名无效") from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="仅支持文本、Markdown 和 PDF 文件",
        ) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="文件不能为空") from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="文件超过大小上限") from exc


@router.get("", response_model=list[DocumentRead])
def list_documents(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    document_status: Annotated[
        Literal["uploaded", "processing", "parsed", "ready", "failed"] | None,
        Query(alias="status"),
    ] = None,
) -> list[Document]:
    parent_id = session.scalar(
        select(KnowledgeBase.id).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if parent_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")

    statement = (
        select(Document)
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if document_status is not None:
        statement = statement.where(Document.status == document_status)
    statement = (
        statement.order_by(Document.created_at, Document.id).offset(offset).limit(limit)
    )
    return list(session.scalars(statement))


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DbSession,
) -> Document:
    return _find_document_in_scope(session, workspace_id, knowledge_base_id, document_id)


@router.post("/{document_id}/chunks", response_model=list[ChunkRead])
def create_document_chunks(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    parameters: ChunkingRequest,
    session: DbSession,
    store: StoreDep,
) -> list[Chunk]:
    _find_document_in_scope(session, workspace_id, knowledge_base_id, document_id)
    # 结束归属检查的只读事务，整批写入事务由服务统一管理。
    session.rollback()
    try:
        return persist_document_chunks(
            session, store, document_id,
            chunk_size=parameters.chunk_size, overlap=parameters.overlap,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在") from exc
    except DocumentNotParsedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="文档尚未成功解析") from exc
    except ChunkingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已有切块使用不同参数或算法版本，当前不支持覆盖重建",
        ) from exc
    except ChunkingLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="切块数量或总文本量超过单次处理上限，请增大块长度或减少重叠",
        ) from exc
    except (ChunkStateError, ChunkSourceError) as exc:
        logger.exception("文档切块失败，文档 ID：%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文档无法切块，请检查文档存储与切块记录",
        ) from exc


@router.get("/{document_id}/chunks", response_model=list[ChunkRead])
def get_document_chunks(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DbSession,
) -> list[Chunk]:
    _find_document_in_scope(session, workspace_id, knowledge_base_id, document_id)
    return list(
        session.scalars(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
        )
    )


@router.get("/{document_id}/chunks/{chunk_id}", response_model=ChunkRead)
def get_document_chunk(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    chunk_id: UUID,
    session: DbSession,
) -> ChunkRead:
    row = session.execute(
        select(Chunk.id, Chunk.document_id, Chunk.chunk_index, Chunk.content, Chunk.start_char, Chunk.end_char)
        .join(Document, Chunk.document_id == Document.id)
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(
            Chunk.id == chunk_id,
            Chunk.document_id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文本块不存在")
    return ChunkRead.model_validate(row)


@router.post("/{document_id}/embeddings", response_model=EmbeddingRead)
def create_document_embeddings(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DbSession,
    provider: EmbeddingDep,
    budget: EmbeddingRequest | None = None,
) -> EmbeddingResult:
    _find_document_in_scope(session, workspace_id, knowledge_base_id, document_id)
    session.rollback()
    try:
        max_chunks = Settings().embedding_max_chunks
        if budget is not None:
            max_chunks = min(max_chunks, budget.max_chunks)
        return embed_document_chunks(
            session, provider, document_id, max_chunks=max_chunks
        )
    except EmbeddingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在") from exc
    except EmbeddingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EmbeddingLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文档超过当前同步向量生成的块数上限",
        ) from exc
    except EmbeddingTimeoutError as exc:
        logger.warning("向量服务超时，文档 ID：%s", document_id)
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="向量服务请求超时") from exc
    except EmbeddingError as exc:
        # 只记录领域错误类型，避免第三方响应、输入文本或凭据进入日志。
        logger.warning("向量生成失败，文档 ID：%s，错误类型：%s", document_id, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="向量服务调用或返回结果异常") from exc
    except EmbeddingStateError as exc:
        logger.error("向量记录与文档状态不一致，文档 ID：%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文档向量状态异常，请检查数据库记录",
        ) from exc
