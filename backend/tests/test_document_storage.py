"""验证本地文档存储的字节边界与路径隔离。"""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from app.knowledge.storage import (
    EmptyUploadError,
    LocalDocumentStore,
    UploadTooLargeError,
)


def test_store_preserves_bytes_and_digest(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)
    content = "# 使用指南\n".encode("utf-8")
    saved = store.save(BytesIO(content), uuid4(), max_bytes=len(content))

    assert saved.byte_size == len(content)
    assert saved.sha256 == sha256(content).hexdigest()
    assert (tmp_path / saved.storage_key).read_bytes() == content
    assert store.read_text_utf8(saved.storage_key) == "# 使用指南\n"

    store.delete(saved.storage_key)
    store.delete(saved.storage_key)
    assert not (tmp_path / saved.storage_key).exists()


@pytest.mark.parametrize(
    ("content", "error"),
    [(b"", EmptyUploadError), (b"12345", UploadTooLargeError)],
)
def test_rejected_file_leaves_no_bytes(
    tmp_path: Path, content: bytes, error: type[Exception]
) -> None:
    store = LocalDocumentStore(tmp_path)

    with pytest.raises(error):
        store.save(BytesIO(content), uuid4(), max_bytes=4)

    assert list((tmp_path / "documents").iterdir()) == []


def test_read_failure_cleans_temporary_file(tmp_path: Path) -> None:
    class BrokenStream:
        def __init__(self) -> None:
            self.read_count = 0

        def read(self, size: int) -> bytes:
            self.read_count += 1
            if self.read_count == 1:
                return b"ok"
            raise OSError("模拟读取失败")

    store = LocalDocumentStore(tmp_path)
    with pytest.raises(OSError):
        store.save(BrokenStream(), uuid4(), max_bytes=4)

    assert list((tmp_path / "documents").iterdir()) == []


def test_storage_key_cannot_escape_root(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)

    with pytest.raises(ValueError):
        store.read_text_utf8("../outside.txt")
    with pytest.raises(ValueError):
        store.delete("documents/../../outside.txt")
