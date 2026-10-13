"""检查请求人是否属于 URL 指定的工作空间。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.models import User, WorkspaceMembership
from app.db.session import get_session


def require_workspace_member(
    workspace_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """资源路由的统一入口；短事务结束后再进入业务服务。"""
    with session.begin():
        member_id = session.scalar(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == current_user.id,
            )
        )
    if member_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="工作区不存在",
        )
