"""允许经过受控标准规则计算的完成状态；不迁移或覆盖旧任务。"""

from alembic import op
import sqlalchemy as sa


revision = "20261011_0007"
down_revision = "20261011_0006"
branch_labels = None
depends_on = None


def _constraints(completed: bool) -> None:
    states = "'waiting_for_user', 'ready', 'completed'" if completed else "'waiting_for_user', 'ready'"
    for name in ("status_valid", "task_status_valid"):
        op.drop_constraint(op.f("ck_conversations_" + name), "conversations", type_="check")
    op.create_check_constraint(op.f("ck_conversations_status_valid"), "conversations", f"status IN ('idle', {states})")
    op.create_check_constraint(op.f("ck_conversations_task_status_valid"), "conversations",
                               f"(status = 'idle' AND task IS NULL) OR (status IN ({states}) AND task IS NOT NULL)")


def upgrade() -> None:
    _constraints(True)


def downgrade() -> None:
    # 即使 new_task 已清除当前任务，历史结果仍是数据；拒绝不兼容的无损降级。
    exists = op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM conversations WHERE status = 'completed'
                       OR task->'billing_plan' IS NOT NULL AND task->'billing_plan' <> 'null'::jsonb)
            OR EXISTS (SELECT 1 FROM conversation_turns
                       WHERE response->'billing_result' IS NOT NULL AND response->'billing_result' <> 'null'::jsonb
                          OR response->'task'->'billing_plan' IS NOT NULL
                             AND response->'task'->'billing_plan' <> 'null'::jsonb)
    """))
    if exists:
        raise RuntimeError("存在标准计费状态或历史，不能无损降级")
    _constraints(False)
