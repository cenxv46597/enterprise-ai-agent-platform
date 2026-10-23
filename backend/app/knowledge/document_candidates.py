"""在调用方已授权并绑定的范围内，按文件名线索查找候选文档。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, KnowledgeBase


MAX_DOCUMENT_CANDIDATES = 5


def _reject_null_character(value: str) -> str:
    if "\x00" in value:
        raise ValueError("文件名线索不能包含空字符")
    return value


DocumentFilenameClue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    AfterValidator(_reject_null_character),
]


class DocumentFilenameQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    filename_query: DocumentFilenameClue


class DocumentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    document_id: UUID
    original_filename: str
    created_at: datetime
    byte_size: int


class DocumentCandidateList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    candidates: tuple[DocumentCandidate, ...]
    has_more: bool


def find_document_candidates(
    session: Session, workspace_id: UUID, knowledge_base_id: UUID, filename_query: str,
) -> DocumentCandidateList:
    """调用方须先验证成员资格并绑定范围；本函数不选择文档、不管理事务。"""
    query = DocumentFilenameQuery(filename_query=filename_query)
    statement = (
        select(
            Document.id.label("document_id"),
            Document.original_filename,
            Document.created_at,
            Document.byte_size,
        )
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
            Document.original_filename.icontains(query.filename_query, autoescape=True),
        )
        # 排序只用于展示，不代表自动选中第一份或最新一份。
        .order_by(Document.created_at, Document.id)
        .limit(MAX_DOCUMENT_CANDIDATES + 1)
    )
    # 多查一条用于识别截断；不能把一页候选说成全部匹配。
    with session.no_autoflush:
        rows = session.execute(statement).mappings().all()
    return DocumentCandidateList(
        candidates=tuple(
            DocumentCandidate.model_validate(dict(row))
            for row in rows[:MAX_DOCUMENT_CANDIDATES]
        ),
        has_more=len(rows) > MAX_DOCUMENT_CANDIDATES,
    )
