"""本地单用户版本的工作空间资源接口。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])

ResourceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
DbSession = Annotated[Session, Depends(get_session)]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ResourceName


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, session: DbSession) -> Workspace:
    workspace = Workspace(name=payload.name)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(workspace_id: UUID, session: DbSession) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作区不存在")
    return workspace
