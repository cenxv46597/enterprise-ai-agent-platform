"""允许显式算术执行轮次；不改写既有会话或消息。"""

from alembic import op
import sqlalchemy as sa


revision = "20261011_0006"
down_revision = "20261011_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_conversation_turns_mode_valid"), "conversation_turns", type_="check")
    op.create_check_constraint(op.f("ck_conversation_turns_mode_valid"), "conversation_turns",
                               "mode IN ('continue', 'new_task', 'execute')")


def downgrade() -> None:
    # 已有 execute 记录时拒绝降级，不能为兼容旧 schema 删除或伪装历史。
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM conversation_turns WHERE mode = 'execute')")):
        raise RuntimeError("存在显式执行轮次，不能无损降级")
    op.drop_constraint(op.f("ck_conversation_turns_mode_valid"), "conversation_turns", type_="check")
    op.create_check_constraint(op.f("ck_conversation_turns_mode_valid"), "conversation_turns",
                               "mode IN ('continue', 'new_task')")
