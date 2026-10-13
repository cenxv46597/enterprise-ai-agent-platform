"""仅向当前成员展示工作空间，创建者自动成为所有者。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.workspace_access import require_workspace_member
from app.db.models import User, Workspace, WorkspaceMembership
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])

ResourceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
DbSession = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ResourceName


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, session: DbSession, current_user: CurrentUser) -> Workspace:
    workspace = Workspace(name=payload.name)
    with session.begin():
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMembership(
            workspace_id=workspace.id, user_id=current_user.id, role="owner"
        ))
        session.flush()
        session.refresh(workspace)
    return workspace


@router.get("", response_model=list[WorkspaceRead])
def list_workspaces(
    session: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
) -> list[Workspace]:
    statement = (
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == current_user.id)
        .order_by(Workspace.created_at, Workspace.id)
        .offset(offset)
        .limit(limit)
    )
    with session.begin():
        return list(session.scalars(statement))


@router.get("/{workspace_id}", response_model=WorkspaceRead,
            dependencies=[Depends(require_workspace_member)])
def get_workspace(workspace_id: UUID, session: DbSession) -> Workspace:
    with session.begin():
        workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作区不存在")
    return workspace
