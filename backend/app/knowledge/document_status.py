"""读取已授权范围内一份文档的处理状态，不读取正文或存储路径。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, KnowledgeBase


class DocumentStatusNotFoundError(LookupError):
    """文档不存在或不属于调用方绑定的工作空间/知识库。"""


class DocumentProcessingStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    document_id: UUID
    original_filename: str
    status: Literal["uploaded", "processing", "parsed", "ready", "failed"]
    error_code: str | None


def get_document_status(
    session: Session, workspace_id: UUID, knowledge_base_id: UUID, document_id: UUID
) -> DocumentProcessingStatus:
    """调用方须先验证成员资格并绑定范围；事务由调用方管理，本函数不写入。"""
    statement = (
        select(
            Document.id.label("document_id"),
            Document.original_filename,
            Document.status,
            Document.error_code,
        )
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    # 查询不能意外把调用方会话中尚未保存的对象自动写入数据库。
    with session.no_autoflush:
        row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise DocumentStatusNotFoundError("文档不存在")
    return DocumentProcessingStatus.model_validate(dict(row))
