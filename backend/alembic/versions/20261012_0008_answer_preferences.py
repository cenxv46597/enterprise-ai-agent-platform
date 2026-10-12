"""新增显式知识库回答偏好；空值保留版本以阻止陈旧请求复活设置。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261012_0008"
down_revision = "20261011_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_preferences",
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("answer_detail", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("knowledge_base_id", name=op.f("pk_answer_preferences")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"],
                                name=op.f("fk_answer_preferences_knowledge_base_id_knowledge_bases"), ondelete="RESTRICT"),
        sa.CheckConstraint("version >= 1 AND version <= 2147483647", name=op.f("ck_answer_preferences_version_valid")),
        sa.CheckConstraint("answer_detail IS NULL OR answer_detail IN ('concise', 'detailed')",
                           name=op.f("ck_answer_preferences_answer_detail_valid")),
    )


def downgrade() -> None:
    # 与存在检查处于同一事务；禁止检查空表后有并发写入再被删表。
    op.get_bind().execute(sa.text("LOCK TABLE answer_preferences IN ACCESS EXCLUSIVE MODE"))
    # 清除后的空值行仍记录版本；一并保护，不能当作无数据删掉。
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM answer_preferences)")):
        raise RuntimeError("存在回答偏好版本数据，不能无损降级")
    op.drop_table("answer_preferences")
