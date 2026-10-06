"""第 0.3 阶段的元数据实体；文档内容和文本块稍后实现。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(
        back_populates="workspace", passive_deletes=True
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        UniqueConstraint("workspace_id", "name", name="uq_knowledge_bases_workspace_id_name"),
        Index("ix_knowledge_bases_workspace_id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", passive_deletes=True
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("char_length(btrim(original_filename)) > 0", name="original_filename_nonblank"),
        CheckConstraint("char_length(btrim(storage_key)) > 0", name="storage_key_nonblank"),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        # PostgreSQL 正则同时拒绝大写或长度不足的摘要。
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_lower_hex_64"),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed')", name="status_valid"
        ),
        Index("ix_documents_knowledge_base_id", "knowledge_base_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'uploaded'")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
