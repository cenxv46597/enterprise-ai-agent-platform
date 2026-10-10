"""在数据库不可用时验证入库状态和文件补偿。"""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models import Document
from app.knowledge.ingestion import InvalidFilenameError, ingest_document
from app.knowledge.storage import LocalDocumentStore


class RecordingSession:
    def __init__(self, fail_on_commit: int | None = None, fail_on_add: bool = False) -> None:
        self.document: Document | None = None
        self.committed_states: list[str] = []
        self.rollback_count = 0
        self.fail_on_commit = fail_on_commit
        self.fail_on_add = fail_on_add

    def add(self, document: Document) -> None:
        if self.fail_on_add:
            raise RuntimeError("模拟数据库写入失败")
        self.document = document

    def commit(self) -> None:
        assert self.document is not None
        self.committed_states.append(self.document.status)
        if self.fail_on_commit == len(self.committed_states):
            raise RuntimeError("模拟数据库提交失败")

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, document: Document) -> None:
        assert document is self.document


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x7f"])
def test_control_character_filename_is_rejected_before_any_write(tmp_path: Path, control: str) -> None:
    session = RecordingSession()
    store = LocalDocumentStore(tmp_path)
    with pytest.raises(InvalidFilenameError):
        ingest_document(
            session=session,
            store=store,
            source=BytesIO(b"guide"),
            filename=f"line{control}break.txt",
            knowledge_base_id=uuid4(),
            max_bytes=1024,
        )
    assert session.document is None
    assert session.committed_states == []
    assert list((tmp_path / "documents").iterdir()) == []


def test_successful_text_follows_parsed_status(tmp_path: Path) -> None:
    session = RecordingSession()
    store = LocalDocumentStore(tmp_path)
    document = ingest_document(
        session=session,
        store=store,
        source=BytesIO("产品说明".encode()),
        filename="manual.txt",
        knowledge_base_id=uuid4(),
        max_bytes=1024,
    )

    assert session.committed_states == ["uploaded", "processing", "parsed"]
    assert document.status == "parsed"
    assert document.error_code is None
    assert store.read_text_utf8(document.storage_key) == "产品说明"


def test_invalid_utf8_is_recorded_without_leaking_original_error(tmp_path: Path) -> None:
    session = RecordingSession()
    store = LocalDocumentStore(tmp_path)
    document = ingest_document(
        session=session,
        store=store,
        source=BytesIO(b"secret:\xff"),
        filename="broken.txt",
        knowledge_base_id=uuid4(),
        max_bytes=1024,
    )

    assert session.committed_states == ["uploaded", "processing", "failed"]
    assert document.status == "failed"
    assert document.error_code == "INVALID_UTF8"
    assert (tmp_path / document.storage_key).read_bytes() == b"secret:\xff"


def test_whitespace_only_text_is_not_marked_parsed(tmp_path: Path) -> None:
    session = RecordingSession()
    store = LocalDocumentStore(tmp_path)
    document = ingest_document(
        session=session,
        store=store,
        source=BytesIO(b" \n\t"),
        filename="blank.txt",
        knowledge_base_id=uuid4(),
        max_bytes=1024,
    )

    assert session.committed_states == ["uploaded", "processing", "failed"]
    assert document.error_code == "EMPTY_TEXT"


def test_first_database_commit_failure_removes_saved_file(tmp_path: Path) -> None:
    session = RecordingSession(fail_on_commit=1)
    store = LocalDocumentStore(tmp_path)

    with pytest.raises(RuntimeError, match="模拟数据库提交失败"):
        ingest_document(
            session=session,
            store=store,
            source=BytesIO(b"guide"),
            filename="guide.md",
            knowledge_base_id=uuid4(),
            max_bytes=1024,
        )

    assert session.rollback_count == 1
    assert list((tmp_path / "documents").iterdir()) == []


def test_database_add_failure_removes_saved_file(tmp_path: Path) -> None:
    session = RecordingSession(fail_on_add=True)
    store = LocalDocumentStore(tmp_path)

    with pytest.raises(RuntimeError, match="模拟数据库写入失败"):
        ingest_document(
            session=session,
            store=store,
            source=BytesIO(b"guide"),
            filename="guide.md",
            knowledge_base_id=uuid4(),
            max_bytes=1024,
        )

    assert session.rollback_count == 1
    assert list((tmp_path / "documents").iterdir()) == []
