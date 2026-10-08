"""同步保存并解析首批文本文件。"""

import logging
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import Document
from app.knowledge.storage import LocalDocumentStore

logger = logging.getLogger(__name__)

MEDIA_TYPES = {".txt": "text/plain", ".md": "text/markdown"}


class InvalidFilenameError(ValueError):
    pass


class UnsupportedFileTypeError(ValueError):
    pass


def validate_filename(filename: str | None) -> tuple[str, str]:
    name = (filename or "").strip()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or any(char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise InvalidFilenameError

    media_type = MEDIA_TYPES.get(Path(name).suffix.lower())
    if media_type is None:
        raise UnsupportedFileTypeError
    return name, media_type


def ingest_document(
    *,
    session: Session,
    store: LocalDocumentStore,
    source: BinaryIO,
    filename: str | None,
    knowledge_base_id: UUID,
    max_bytes: int,
) -> Document:
    name, media_type = validate_filename(filename)
    document_id = uuid4()
    source.seek(0)
    saved = store.save(source, document_id, max_bytes)

    try:
        document = Document(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            original_filename=name,
            media_type=media_type,
            storage_key=saved.storage_key,
            byte_size=saved.byte_size,
            sha256=saved.sha256,
            status="uploaded",
        )
        session.add(document)
        session.commit()
    except Exception:
        session.rollback()
        try:
            store.delete(saved.storage_key)
        except OSError:
            logger.exception("数据库保存失败后，清理上传文件也失败")
        raise

    document.status = "processing"
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    try:
        content = store.read_text_utf8(saved.storage_key)
        if not content.strip():
            document.status = "failed"
            document.error_code = "EMPTY_TEXT"
        else:
            document.status = "parsed"
            document.error_code = None
    except UnicodeError:
        document.status = "failed"
        document.error_code = "INVALID_UTF8"
    except (OSError, ValueError):
        logger.exception("读取已保存文件失败")
        document.status = "failed"
        document.error_code = "STORAGE_READ_FAILED"

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(document)
    return document
