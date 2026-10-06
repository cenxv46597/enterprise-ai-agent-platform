"""建立工作空间、知识库和文档元数据表。

Revision ID: 20261006_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261006_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name=op.f("ck_workspaces_name_nonblank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name=op.f("ck_knowledge_bases_name_nonblank")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_knowledge_bases_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_bases")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_knowledge_bases_workspace_id_name")
        ),
    )
    op.create_index("ix_knowledge_bases_workspace_id", "knowledge_bases", ["workspace_id"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=127), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'uploaded'"), nullable=False
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(original_filename)) > 0",
            name=op.f("ck_documents_original_filename_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(storage_key)) > 0",
            name=op.f("ck_documents_storage_key_nonblank"),
        ),
        sa.CheckConstraint("byte_size > 0", name=op.f("ck_documents_byte_size_positive")),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_documents_sha256_lower_hex_64")
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_documents_knowledge_base_id_knowledge_bases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index("ix_documents_knowledge_base_id", "documents", ["knowledge_base_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_knowledge_base_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_knowledge_bases_workspace_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_table("workspaces")
