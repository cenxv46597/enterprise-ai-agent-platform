"""文档上传与处理状态查询接口。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.workspaces import DbSession
from app.core.config import Settings
from app.db.models import Document, KnowledgeBase
from app.knowledge.ingestion import (
    InvalidFilenameError,
    UnsupportedFileTypeError,
    ingest_document,
)
from app.knowledge.storage import EmptyUploadError, LocalDocumentStore, UploadTooLargeError

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents",
    tags=["documents"],
)


def get_document_store() -> LocalDocumentStore:
    return LocalDocumentStore(Settings().upload_root)


def get_max_upload_bytes() -> int:
    return Settings().max_upload_bytes


StoreDep = Annotated[LocalDocumentStore, Depends(get_document_store)]
LimitDep = Annotated[int, Depends(get_max_upload_bytes)]


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
            detail="仅支持文本和 Markdown 文件",
        ) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="文件不能为空") from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="文件超过大小上限") from exc


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DbSession,
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
