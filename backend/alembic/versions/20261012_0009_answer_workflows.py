"""增加可恢复的只读问答工作流与执行回执；保留已有知识和会话数据。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261012_0009"
down_revision = "20261012_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_digest", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(16), server_default=sa.text("'vector'"), nullable=False),
        sa.Column("max_context_characters", sa.Integer(), server_default=sa.text("12000"), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("next_step", sa.String(16), server_default=sa.text("'retrieve'"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("active_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_workflows")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"],
                                name=op.f("fk_answer_workflows_knowledge_base_id_knowledge_bases"), ondelete="RESTRICT"),
        sa.UniqueConstraint("knowledge_base_id", "creation_request_id", name="uq_answer_workflows_creation_request"),
        sa.CheckConstraint("char_length(btrim(question)) BETWEEN 1 AND 2000", name=op.f("ck_answer_workflows_question_valid")),
        sa.CheckConstraint("creation_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_answer_workflows_creation_digest_valid")),
        sa.CheckConstraint("strategy IN ('vector', 'hybrid')", name=op.f("ck_answer_workflows_strategy_valid")),
        sa.CheckConstraint("max_context_characters BETWEEN 1 AND 12000", name=op.f("ck_answer_workflows_context_valid")),
        sa.CheckConstraint("version BETWEEN 0 AND 32 AND attempt_count BETWEEN 0 AND 5", name=op.f("ck_answer_workflows_counters_valid")),
        sa.CheckConstraint("status IN ('pending', 'running', 'failed', 'completed')", name=op.f("ck_answer_workflows_status_valid")),
        sa.CheckConstraint("next_step IN ('retrieve', 'generate', 'done')", name=op.f("ck_answer_workflows_step_valid")),
        sa.CheckConstraint("(status = 'running' AND active_request_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
                           "(status <> 'running' AND active_request_id IS NULL AND lease_expires_at IS NULL)",
                           name=op.f("ck_answer_workflows_owner_valid")),
        sa.CheckConstraint("(next_step = 'retrieve' AND checkpoint IS NULL) OR "
                           "(next_step IN ('generate', 'done') AND checkpoint IS NOT NULL)",
                           name=op.f("ck_answer_workflows_checkpoint_valid")),
        sa.CheckConstraint("(status = 'completed' AND next_step = 'done' AND result IS NOT NULL) OR "
                           "(status <> 'completed' AND next_step <> 'done' AND result IS NULL)",
                           name=op.f("ck_answer_workflows_result_valid")),
        sa.CheckConstraint("(status = 'failed' AND error_code IS NOT NULL) OR "
                           "(status <> 'failed' AND error_code IS NULL)", name=op.f("ck_answer_workflows_error_valid")),
        sa.CheckConstraint("(status = 'pending' AND next_step = 'retrieve' AND attempt_count = 0) OR "
                           "(status <> 'pending' AND attempt_count > 0)", name=op.f("ck_answer_workflows_initial_valid")),
    )
    op.create_index("ix_answer_workflows_knowledge_base_id", "answer_workflows", ["knowledge_base_id"])
    op.create_table(
        "answer_workflow_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("response", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_workflow_attempts")),
        sa.ForeignKeyConstraint(["workflow_id"], ["answer_workflows.id"],
                                name=op.f("fk_answer_workflow_attempts_workflow_id_answer_workflows"), ondelete="RESTRICT"),
        sa.UniqueConstraint("workflow_id", "request_id", name="uq_answer_workflow_attempts_request"),
        sa.UniqueConstraint("workflow_id", "attempt_number", name="uq_answer_workflow_attempts_number"),
        sa.CheckConstraint("attempt_number BETWEEN 1 AND 5", name=op.f("ck_answer_workflow_attempts_number_valid")),
        sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_answer_workflow_attempts_digest_valid")),
        sa.CheckConstraint("status IN ('running', 'failed', 'completed', 'interrupted')", name=op.f("ck_answer_workflow_attempts_status_valid")),
        sa.CheckConstraint("(status = 'running' AND response IS NULL AND finished_at IS NULL) OR "
                           "(status <> 'running' AND response IS NOT NULL AND finished_at IS NOT NULL)",
                           name=op.f("ck_answer_workflow_attempts_receipt_valid")),
    )


def downgrade() -> None:
    # 同一事务先锁两表，再检查数据；避免检查空表后并发写入被随后删表。
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE answer_workflows, answer_workflow_attempts IN ACCESS EXCLUSIVE MODE"))
    # pending 工作流也保存原始请求；不能因为尚未执行就当作无历史。
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM answer_workflows) OR "
                           "EXISTS (SELECT 1 FROM answer_workflow_attempts)")):
        raise RuntimeError("存在问答工作流或执行回执数据，不能无损降级")
    op.drop_table("answer_workflow_attempts")
    op.drop_index("ix_answer_workflows_knowledge_base_id", table_name="answer_workflows")
    op.drop_table("answer_workflows")
