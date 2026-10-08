"""为文档增加已解析状态。

Revision ID: 20261008_0002
Revises: 20261006_0001
"""

from alembic import op


revision = "20261008_0002"
down_revision = "20261006_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"),
        "documents",
        "status IN ('uploaded', 'processing', 'parsed', 'ready', 'failed')",
    )


def downgrade() -> None:
    # 旧版没有 parsed 状态；降级会把已解析文档退回 uploaded，丢失已解析语义。
    op.execute(
        "UPDATE documents SET status = 'uploaded', updated_at = now() "
        "WHERE status = 'parsed'"
    )
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"),
        "documents",
        "status IN ('uploaded', 'processing', 'ready', 'failed')",
    )
