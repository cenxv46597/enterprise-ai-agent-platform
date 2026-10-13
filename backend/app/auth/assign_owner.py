"""为迁移前创建的无成员工作空间显式指定首位所有者。"""

import argparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, Workspace, WorkspaceMembership
from app.db.session import get_session_factory


class OwnerAssignmentNotFoundError(ValueError):
    """指定的工作空间或已注册用户不存在。"""


class OwnerAssignmentConflictError(ValueError):
    """工作空间已经有成员，不能再次用本地命令接管。"""


def assign_owner(session: Session, workspace_id: UUID, username: str) -> WorkspaceMembership:
    """只有完全没有成员的旧工作空间可接管；行锁防止并发双重接管。"""
    with session.begin():
        workspace = session.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        )
        user = session.scalar(select(User).where(User.username == username.strip().lower()))
        if workspace is None or user is None:
            raise OwnerAssignmentNotFoundError("工作空间或用户不存在")
        already_assigned = session.scalar(
            select(WorkspaceMembership.user_id)
            .where(WorkspaceMembership.workspace_id == workspace_id)
            .limit(1)
        )
        if already_assigned is not None:
            raise OwnerAssignmentConflictError("工作空间已有成员")
        membership = WorkspaceMembership(
            workspace_id=workspace_id, user_id=user.id, role="owner"
        )
        session.add(membership)
        session.flush()
        session.expunge(membership)
        return membership


def main() -> int:
    parser = argparse.ArgumentParser(description="给无成员的旧工作空间指定已注册用户为所有者")
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    with get_session_factory()() as session:
        try:
            membership = assign_owner(session, args.workspace_id, args.username)
        except (OwnerAssignmentNotFoundError, OwnerAssignmentConflictError) as exc:
            parser.exit(2, f"无法指定所有者：{exc}\n")
    print(f"已指定工作空间 {membership.workspace_id} 的所有者")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
