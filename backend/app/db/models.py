"""工作空间、文档元数据、可追溯文本块与向量实体。"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
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
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    """登录身份；用户名使用规范化的小写形式保存。"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("username ~ '^[a-z][a-z0-9_]{2,31}$'", name="username_valid"),
        CheckConstraint("char_length(password_hash) > 0", name="password_hash_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        back_populates="user", passive_deletes=True
    )


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
    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        back_populates="workspace", passive_deletes=True
    )


class WorkspaceMembership(Base):
    """用户在一个工作空间的成员资格和有限角色。"""

    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="role_valid"),
        Index("ix_workspace_memberships_user_id", "user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        # 同一知识库只采用一套向量模型；旧知识库可暂时没有向量配置。
        CheckConstraint(
            "(embedding_provider IS NULL AND embedding_model IS NULL "
            "AND embedding_dimensions IS NULL) OR (embedding_provider IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_dimensions IS NOT NULL "
            "AND char_length(btrim(embedding_provider)) > 0 "
            "AND char_length(btrim(embedding_model)) > 0 AND embedding_dimensions = 1024)",
            name="embedding_config_valid",
        ),
        UniqueConstraint("workspace_id", "name", name="uq_knowledge_bases_workspace_id_name"),
        Index("ix_knowledge_bases_workspace_id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 接口身份用于校验向量空间一致性，不存放密钥。
    embedding_provider: Mapped[str | None] = mapped_column(String(512), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks", passive_deletes=True)


class Conversation(Base):
    """一个知识库内的会话；version 控制跨请求的状态更新。"""
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("version >= 0 AND version <= 200", name="version_valid"),
        CheckConstraint("status IN ('idle', 'waiting_for_user', 'ready', 'completed')", name="status_valid"),
        CheckConstraint(
            "(status = 'idle' AND task IS NULL) OR (status IN ('waiting_for_user', 'ready', 'completed') AND task IS NOT NULL)",
            name="task_status_valid",
        ),
        Index("ix_conversations_knowledge_base_id", "knowledge_base_id"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'idle'"))
    task: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ConversationTurn(Base):
    """一轮请求、公开回复和结果一起提交；请求编号只在该会话内唯一。"""
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id", name="uq_conversation_turns_request"),
        UniqueConstraint("conversation_id", "version", name="uq_conversation_turns_version"),
        CheckConstraint("version > 0 AND version <= 200", name="version_valid"),
        CheckConstraint("char_length(content) BETWEEN 1 AND 2000", name="content_length_valid"),
        CheckConstraint("char_length(reply) BETWEEN 1 AND 12000", name="reply_length_valid"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="request_digest_valid"),
        CheckConstraint("mode IN ('continue', 'new_task', 'execute')", name="mode_valid"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'continue'"))
    reply: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AnswerPreference(Base):
    """一个知识库的有限回答偏好；空值保留版本，避免旧请求复活已清除内容。"""
    __tablename__ = "answer_preferences"
    __table_args__ = (
        CheckConstraint("version >= 1 AND version <= 2147483647", name="version_valid"),
        CheckConstraint("answer_detail IS NULL OR answer_detail IN ('concise', 'detailed')", name="answer_detail_valid"),
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    answer_detail: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )


class AnswerWorkflow(Base):
    """固定检索/生成工作流；运行前领取执行权，检查点和结果分步提交。"""
    __tablename__ = "answer_workflows"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "creation_request_id", name="uq_answer_workflows_creation_request"),
        Index("ix_answer_workflows_knowledge_base_id", "knowledge_base_id"),
        CheckConstraint("char_length(btrim(question)) BETWEEN 1 AND 2000", name="question_valid"),
        CheckConstraint("creation_digest ~ '^[0-9a-f]{64}$'", name="creation_digest_valid"),
        CheckConstraint("strategy IN ('vector', 'hybrid')", name="strategy_valid"),
        CheckConstraint("max_context_characters BETWEEN 1 AND 12000", name="context_valid"),
        CheckConstraint("version BETWEEN 0 AND 32 AND attempt_count BETWEEN 0 AND 5", name="counters_valid"),
        CheckConstraint("status IN ('pending', 'running', 'failed', 'completed')", name="status_valid"),
        CheckConstraint("next_step IN ('retrieve', 'generate', 'done')", name="step_valid"),
        CheckConstraint(
            "(status = 'running' AND active_request_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND active_request_id IS NULL AND lease_expires_at IS NULL)", name="owner_valid"),
        CheckConstraint(
            "(next_step = 'retrieve' AND checkpoint IS NULL) OR "
            "(next_step IN ('generate', 'done') AND checkpoint IS NOT NULL)", name="checkpoint_valid"),
        CheckConstraint(
            "(status = 'completed' AND next_step = 'done' AND result IS NOT NULL) OR "
            "(status <> 'completed' AND next_step <> 'done' AND result IS NULL)", name="result_valid"),
        CheckConstraint("(status = 'failed' AND error_code IS NOT NULL) OR "
                        "(status <> 'failed' AND error_code IS NULL)", name="error_valid"),
        CheckConstraint("(status = 'pending' AND next_step = 'retrieve' AND attempt_count = 0) OR "
                        "(status <> 'pending' AND attempt_count > 0)", name="initial_valid"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False)
    creation_request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    creation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'vector'"))
    max_context_characters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("12000"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    next_step: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'retrieve'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    checkpoint: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active_request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AnswerWorkflowAttempt(Base):
    """有界执行尝试与不可变终态回执；请求重放不等于重新执行。"""
    __tablename__ = "answer_workflow_attempts"
    __table_args__ = (
        UniqueConstraint("workflow_id", "request_id", name="uq_answer_workflow_attempts_request"),
        UniqueConstraint("workflow_id", "attempt_number", name="uq_answer_workflow_attempts_number"),
        CheckConstraint("attempt_number BETWEEN 1 AND 5", name="number_valid"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="digest_valid"),
        CheckConstraint("status IN ('running', 'failed', 'completed', 'interrupted')", name="status_valid"),
        CheckConstraint("(status = 'running' AND response IS NULL AND finished_at IS NULL) OR "
                        "(status <> 'running' AND response IS NOT NULL AND finished_at IS NOT NULL)", name="receipt_valid"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("answer_workflows.id", ondelete="RESTRICT"), nullable=False)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'running'"))
    response: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
