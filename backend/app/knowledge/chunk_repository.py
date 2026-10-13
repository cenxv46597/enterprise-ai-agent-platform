"""在一个事务中保存文档的整批切块，同配置重试复用已有结果。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document
from app.knowledge.chunking import (
    CHUNKING_VERSION,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    split_text,
)
from app.knowledge.document_text import read_document_text
from app.knowledge.storage import LocalDocumentStore


MAX_PERSISTED_CHUNK_SIZE = 2_147_483_647
MAX_CHUNKS_PER_DOCUMENT = 10_000
MAX_CHUNK_CHARACTERS_PER_DOCUMENT = 10 * 1024 * 1024


class DocumentNotFoundError(RuntimeError):
    """文档不存在。"""


class DocumentNotParsedError(RuntimeError):
    """文档尚未成功解析。"""


class ChunkingConflictError(RuntimeError):
    """已有结果使用不同参数或算法版本，需要显式的重建流程。"""


class ChunkStateError(RuntimeError):
    """保存的切块配置与文本块不一致。"""


class ChunkSourceError(RuntimeError):
    """无法读取可切块的解析文本。"""


class ChunkingLimitError(ValueError):
    """请求参数超过可持久化范围或单批处理上限。"""


def persist_document_chunks(
    session: Session,
    store: LocalDocumentStore,
    document_id: UUID,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """调用方提供无活动事务的会话；本函数负责整批提交或回滚。

    同一文档只保留一套配置。文件由上传流程保存后保持不变；字符位置
    对应 read_document_text 返回的解析文本，不对应原始文件的字节位置。
    """
    # 复用纯函数的参数验证，在获取数据库锁前拒绝无效参数。
    split_text(document_id, "", chunk_size=chunk_size, overlap=overlap)
    if chunk_size > MAX_PERSISTED_CHUNK_SIZE:
        raise ChunkingLimitError("块长度超过数据库整数范围")

    with session.begin():
        # 即使尚无 Chunk 行，也能用已存在的文档行串行化两个首次请求。
        document = session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None:
            raise DocumentNotFoundError("文档不存在")
        if document.status not in {"parsed", "ready"}:
            raise DocumentNotParsedError("文档尚未成功解析")

        existing = list(
            session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            )
        )
        saved_config = (document.chunking_version, document.chunk_size, document.chunk_overlap)
        requested_config = (CHUNKING_VERSION, chunk_size, overlap)
        if saved_config != (None, None, None):
            if saved_config != requested_config:
                raise ChunkingConflictError("已有切块使用不同参数或算法版本")
            if not existing:
                raise ChunkStateError("存在切块配置但没有文本块")
            return existing
        if existing:
            raise ChunkStateError("存在文本块但缺少切块配置")

        try:
            text = read_document_text(store, document.storage_key, document.media_type)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ChunkSourceError("无法读取文档的解析文本") from exc
        if "\x00" in text:
            raise ChunkSourceError("解析文本包含数据库不支持的空字符")
        # 先估算再构造列表；高重叠既可能增加块数，也可能放大总文本量。
        step = chunk_size - overlap
        chunk_count = 1 + max(0, (len(text) - chunk_size + step - 1) // step)
        total_characters = len(text) + (chunk_count - 1) * overlap
        if (
            chunk_count > MAX_CHUNKS_PER_DOCUMENT
            or total_characters > MAX_CHUNK_CHARACTERS_PER_DOCUMENT
        ):
            raise ChunkingLimitError("切块数量或总文本量超过单次处理上限")
        text_chunks = split_text(document_id, text, chunk_size=chunk_size, overlap=overlap)
        if not text_chunks:
            raise ChunkSourceError("解析文本为空，无法切块")

        chunks = [
            Chunk(
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
            )
            for chunk in text_chunks
        ]
        session.add_all(chunks)
        document.chunk_size = chunk_size
        document.chunk_overlap = overlap
        document.chunking_version = CHUNKING_VERSION
        session.flush()
        # 离开 begin 上下文后才提交成功；异常会撤销配置和全部文本块。
        return chunks
