"""限定在工作空间内的知识库接口。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.workspaces import DbSession, ResourceName
from app.db.models import KnowledgeBase, Workspace

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases",
    tags=["knowledge-bases"],
)

_NAME_CONSTRAINT = "uq_knowledge_bases_workspace_id_name"


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ResourceName


class KnowledgeBaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    created_at: datetime


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    workspace_id: UUID, payload: KnowledgeBaseCreate, session: DbSession
) -> KnowledgeBase:
    if session.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作区不存在")

    knowledge_base = KnowledgeBase(workspace_id=workspace_id, name=payload.name)
    session.add(knowledge_base)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == _NAME_CONSTRAINT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该工作区中已存在同名知识库",
            ) from exc
        raise

    session.refresh(knowledge_base)
    return knowledge_base


@router.get("", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(
    workspace_id: UUID,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
) -> list[KnowledgeBase]:
    if session.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作区不存在")

    statement = (
        select(KnowledgeBase)
        .where(KnowledgeBase.workspace_id == workspace_id)
        .order_by(KnowledgeBase.created_at, KnowledgeBase.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(statement))


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def get_knowledge_base(
    workspace_id: UUID, knowledge_base_id: UUID, session: DbSession
) -> KnowledgeBase:
    knowledge_base = session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在"
        )
    return knowledge_base
