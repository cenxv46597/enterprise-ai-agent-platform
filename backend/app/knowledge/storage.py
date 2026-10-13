"""按文档标识保存上传文件，并限制可访问的本地路径。"""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import BinaryIO
from uuid import UUID, uuid4


_CHUNK_SIZE = 64 * 1024
_STORAGE_KEY_PATTERN = re.compile(r"documents/[0-9a-f]{32}\Z", re.ASCII)


class EmptyUploadError(ValueError):
    """上传文件不含任何字节。"""


class UploadTooLargeError(ValueError):
    """上传文件超过允许的字节数。"""


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """保存成功后用于写入文档元数据的文件信息。"""

    storage_key: str
    byte_size: int
    sha256: str


class LocalDocumentStore:
    """将文档放在固定根目录下，文件名由服务端的 UUID 决定。"""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve(strict=True)
        self._documents_dir = self._root / "documents"
        self._documents_dir.mkdir(exist_ok=True)
        self._check_documents_dir()

    def _check_documents_dir(self) -> None:
        """拒绝指向其他目录的符号链接或目录联接。"""

        if self._documents_dir.is_symlink() or self._documents_dir.resolve(strict=True) != self._documents_dir:
            raise ValueError("文档存储目录不能是符号链接或目录联接")

    def _document_path(self, storage_key: str) -> Path:
        """仅接受本服务生成的存储键，并限制最终路径。"""

        if (
            not isinstance(storage_key, str)
            or _STORAGE_KEY_PATTERN.fullmatch(storage_key) is None
        ):
            raise ValueError("无效的文档存储键")

        self._check_documents_dir()
        path = self._documents_dir / storage_key.removeprefix("documents/")
        if path.is_symlink() or path.resolve(strict=False) != path:
            raise ValueError("文档路径不能指向存储目录之外")
        return path

    def save(self, file: BinaryIO, document_id: UUID, max_bytes: int) -> StoredDocument:
        """分块保存文件并计算摘要；失败时删除尚未发布的临时文件。"""

        if not isinstance(document_id, UUID):
            raise TypeError("document_id 必须是 UUID")
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须大于零")

        storage_key = f"documents/{document_id.hex}"
        target = self._document_path(storage_key)
        if target.exists():
            raise FileExistsError(f"文档 {document_id} 已存在")

        temporary = self._documents_dir / f".{document_id.hex}.{uuid4().hex}.tmp"
        digest = sha256()
        byte_size = 0
        try:
            with temporary.open("xb") as output:
                while chunk := file.read(_CHUNK_SIZE):
                    byte_size += len(chunk)
                    if byte_size > max_bytes:
                        raise UploadTooLargeError(f"文件超过 {max_bytes} 字节")
                    output.write(chunk)
                    digest.update(chunk)

                if byte_size == 0:
                    raise EmptyUploadError("不能上传空文件")

                output.flush()
                os.fsync(output.fileno())

            self._check_documents_dir()
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        return StoredDocument(
            storage_key=storage_key,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
        )

    def read_text_utf8(self, storage_key: str) -> str:
        """按 UTF-8 严格解码文档；无效字节会抛出解码异常。"""

        return self._document_path(storage_key).read_text(encoding="utf-8", errors="strict")

    def open_binary(self, storage_key: str) -> BinaryIO:
        """经存储键与路径检查后打开原文件，供 PDF 解析器按需读取。"""

        return self._document_path(storage_key).open("rb")

    def published_files(self) -> dict[str, float]:
        """列出已发布的普通文件与修改时间；跳过临时文件和未知命名。"""

        self._check_documents_dir()
        files: dict[str, float] = {}
        for entry in self._documents_dir.iterdir():
            storage_key = f"documents/{entry.name}"
            if _STORAGE_KEY_PATTERN.fullmatch(storage_key) is None:
                continue
            path = self._document_path(storage_key)
            try:
                if path.is_file():
                    files[storage_key] = path.stat().st_mtime
            except FileNotFoundError:
                # 扫描期间文件可能被其他操作移除，下次对账再核对。
                continue
        return files

    def delete(self, storage_key: str) -> None:
        """删除指定文档；文件已不存在时保持幂等。"""

        self._document_path(storage_key).unlink(missing_ok=True)
