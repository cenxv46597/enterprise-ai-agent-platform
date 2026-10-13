"""工作空间所有者邀请已注册用户为成员。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.workspaces import CurrentUser, DbSession
from app.db.models import User, WorkspaceMembership


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/members", tags=["workspace-members"])


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[
        str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=3, max_length=32)
    ]


class MemberRead(BaseModel):
    workspace_id: UUID
    user_id: UUID
    username: str
    role: str


@router.post("", response_model=MemberRead, status_code=status.HTTP_201_CREATED)
def add_member(
    workspace_id: UUID, payload: MemberCreate, session: DbSession, current_user: CurrentUser
) -> MemberRead:
    try:
        with session.begin():
            is_owner = session.scalar(select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == current_user.id,
                WorkspaceMembership.role == "owner",
            ))
            if is_owner is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作区不存在")

            target = session.scalar(select(User).where(User.username == payload.username))
            if target is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
            if session.get(WorkspaceMembership, (workspace_id, target.id)) is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户已是工作区成员")

            member = WorkspaceMembership(workspace_id=workspace_id, user_id=target.id, role="member")
            session.add(member)
            session.flush()
            return MemberRead(workspace_id=workspace_id, user_id=target.id,
                              username=target.username, role=member.role)
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户已是工作区成员") from exc
