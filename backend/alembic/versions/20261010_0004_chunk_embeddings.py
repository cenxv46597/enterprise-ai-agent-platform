"""增加 1024 维文本块向量与知识库向量配置。

Revision ID: 20261010_0004
Revises: 20261010_0003
"""

from alembic import op
from pgvector.sqlalchemy import VECTOR
import sqlalchemy as sa


revision = "20261010_0004"
down_revision = "20261010_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 扩展先于向量字段建立；已有文档和知识库保留空值。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.add_column("chunks", sa.Column("embedding", VECTOR(1024), nullable=True))
    op.add_column(
        "knowledge_bases", sa.Column("embedding_provider", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "knowledge_bases", sa.Column("embedding_model", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "knowledge_bases", sa.Column("embedding_dimensions", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_knowledge_bases_embedding_config_valid"),
        "knowledge_bases",
        "(embedding_provider IS NULL AND embedding_model IS NULL "
        "AND embedding_dimensions IS NULL) OR (embedding_provider IS NOT NULL "
        "AND embedding_model IS NOT NULL AND embedding_dimensions IS NOT NULL "
        "AND char_length(btrim(embedding_provider)) > 0 "
        "AND char_length(btrim(embedding_model)) > 0 AND embedding_dimensions = 1024)",
    )


def downgrade() -> None:
    # 降级删除向量数据，但保留扩展，避免影响数据库中的其他对象。
    op.drop_constraint(
        op.f("ck_knowledge_bases_embedding_config_valid"), "knowledge_bases", type_="check"
    )
    op.drop_column("knowledge_bases", "embedding_dimensions")
    op.drop_column("knowledge_bases", "embedding_model")
    op.drop_column("knowledge_bases", "embedding_provider")
    op.drop_column("chunks", "embedding")
