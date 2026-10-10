"""增加文本块以及文档切块配置。

Revision ID: 20261010_0003
Revises: 20261008_0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261010_0003"
down_revision = "20261008_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 新字段允许全空，已有文档无需在迁移时重新解析与切块。
    op.add_column("documents", sa.Column("chunk_size", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("chunk_overlap", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("chunking_version", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_chunking_config_valid"),
        "documents",
        "(chunk_size IS NULL AND chunk_overlap IS NULL AND chunking_version IS NULL) "
        "OR (chunk_size IS NOT NULL AND chunk_overlap IS NOT NULL "
        "AND chunking_version IS NOT NULL AND chunk_size > 0 "
        "AND chunk_overlap >= 0 AND chunk_overlap < chunk_size "
        "AND char_length(btrim(chunking_version)) > 0)",
    )
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.CheckConstraint("chunk_index > 0", name=op.f("ck_chunks_chunk_index_positive")),
        sa.CheckConstraint("start_char >= 0", name=op.f("ck_chunks_start_char_nonnegative")),
        sa.CheckConstraint("end_char > start_char", name=op.f("ck_chunks_end_char_after_start")),
        sa.CheckConstraint(
            "char_length(content) = end_char - start_char",
            name=op.f("ck_chunks_content_length_matches_span"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name=op.f("uq_chunks_document_id_chunk_index")
        ),
    )


def downgrade() -> None:
    # 降级会删除已保存的文本块与切块配置。
    op.drop_table("chunks")
    op.drop_constraint(op.f("ck_documents_chunking_config_valid"), "documents", type_="check")
    op.drop_column("documents", "chunking_version")
    op.drop_column("documents", "chunk_overlap")
    op.drop_column("documents", "chunk_size")
