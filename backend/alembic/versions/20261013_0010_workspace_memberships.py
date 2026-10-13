"""增加登录用户与工作空间成员关系；既有工作空间需要显式分配成员。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261013_0010"
down_revision = "20261012_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("username ~ '^[a-z][a-z0-9_]{2,31}$'", name=op.f("ck_users_username_valid")),
        sa.CheckConstraint("char_length(password_hash) > 0", name=op.f("ck_users_password_hash_nonblank")),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_memberships")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name=op.f("fk_workspace_memberships_workspace_id_workspaces"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_workspace_memberships_user_id_users"), ondelete="RESTRICT",
        ),
        sa.CheckConstraint("role IN ('owner', 'member')", name=op.f("ck_workspace_memberships_role_valid")),
    )
    op.create_index(
        "ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE workspace_memberships, users IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM workspace_memberships) OR EXISTS (SELECT 1 FROM users)"
    )):
        raise RuntimeError("存在用户或工作空间成员关系，不能无损降级")
    op.drop_index("ix_workspace_memberships_user_id", table_name="workspace_memberships")
    op.drop_table("workspace_memberships")
    op.drop_table("users")
