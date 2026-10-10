"""工作空间、文档元数据与可追溯的文本块实体。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
            "status IN ('uploaded', 'processing', 'parsed', 'ready', 'failed')",
            name="status_valid",
        ),
        # 未切块的旧文档保留空配置；切块后配置必须完整且有效。
        CheckConstraint(
            "(chunk_size IS NULL AND chunk_overlap IS NULL AND chunking_version IS NULL) "
            "OR (chunk_size IS NOT NULL AND chunk_overlap IS NOT NULL "
            "AND chunking_version IS NOT NULL AND chunk_size > 0 "
            "AND chunk_overlap >= 0 AND chunk_overlap < chunk_size "
            "AND char_length(btrim(chunking_version)) > 0)",
            name="chunking_config_valid",
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
    chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_overlap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunking_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        # 此唯一约束也为按文档查询提供索引，不再建立重复索引。
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_id_chunk_index"),
        CheckConstraint("chunk_index > 0", name="chunk_index_positive"),
        CheckConstraint("start_char >= 0", name="start_char_nonnegative"),
        CheckConstraint("end_char > start_char", name="end_char_after_start"),
        CheckConstraint(
            "char_length(content) = end_char - start_char", name="content_length_matches_span"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks", passive_deletes=True)
