"""增加会话状态与原子提交的请求轮次；保留已有知识数据。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261011_0005"
down_revision = "20261010_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'idle'"), nullable=False),
        sa.Column("task", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"],
                                name=op.f("fk_conversations_knowledge_base_id_knowledge_bases"), ondelete="RESTRICT"),
        sa.CheckConstraint("version >= 0 AND version <= 200", name=op.f("ck_conversations_version_valid")),
        sa.CheckConstraint("status IN ('idle', 'waiting_for_user', 'ready')", name=op.f("ck_conversations_status_valid")),
        sa.CheckConstraint("(status = 'idle' AND task IS NULL) OR (status IN ('waiting_for_user', 'ready') AND task IS NOT NULL)",
                           name=op.f("ck_conversations_task_status_valid")),
    )
    op.create_index("ix_conversations_knowledge_base_id", "conversations", ["knowledge_base_id"])
    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(16), server_default=sa.text("'continue'"), nullable=False),
        sa.Column("reply", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_turns")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"],
                                name=op.f("fk_conversation_turns_conversation_id_conversations"), ondelete="RESTRICT"),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_conversation_turns_request"),
        sa.UniqueConstraint("conversation_id", "version", name="uq_conversation_turns_version"),
        sa.CheckConstraint("version > 0 AND version <= 200", name=op.f("ck_conversation_turns_version_valid")),
        sa.CheckConstraint("char_length(content) BETWEEN 1 AND 2000", name=op.f("ck_conversation_turns_content_length_valid")),
        sa.CheckConstraint("char_length(reply) BETWEEN 1 AND 12000", name=op.f("ck_conversation_turns_reply_length_valid")),
        sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_conversation_turns_request_digest_valid")),
        sa.CheckConstraint("mode IN ('continue', 'new_task')", name=op.f("ck_conversation_turns_mode_valid")),
    )


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_index("ix_conversations_knowledge_base_id", table_name="conversations")
    op.drop_table("conversations")
