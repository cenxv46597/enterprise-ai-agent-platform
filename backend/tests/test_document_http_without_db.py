"""在数据库停机时验证上传表单与 HTTP 错误映射。"""

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.documents import get_document_store, get_max_upload_bytes
from app.db.models import Document
from app.db.session import get_session
from app.knowledge.storage import LocalDocumentStore
from app.main import create_app


class MemorySession:
    def __init__(self, knowledge_base_id: UUID) -> None:
        self.knowledge_base_id = knowledge_base_id
        self.document: Document | None = None

    def scalar(self, statement: object) -> UUID:
        return self.knowledge_base_id

    def rollback(self) -> None:
        pass

    def add(self, document: Document) -> None:
        self.document = document

    def commit(self) -> None:
        assert self.document is not None
        current = datetime.now(timezone.utc)
        if self.document.created_at is None:
            self.document.created_at = current
        self.document.updated_at = current

    def refresh(self, document: Document) -> None:
        assert document is self.document


def test_multipart_upload_and_size_error_mapping(tmp_path: Path) -> None:
    workspace_id = uuid4()
    knowledge_base_id = uuid4()
    fake_session = MemorySession(knowledge_base_id)
    store = LocalDocumentStore(tmp_path)

    def session_override() -> Iterator[MemorySession]:
        yield fake_session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_document_store] = lambda: store
    app.dependency_overrides[get_max_upload_bytes] = lambda: 16
    url = (
        f"/api/v1/workspaces/{workspace_id}"
        f"/knowledge-bases/{knowledge_base_id}/documents"
    )

    with TestClient(app) as client:
        accepted = client.post(
            url,
            files={"file": ("guide.md", b"# Manual\n", "application/octet-stream")},
        )
        assert accepted.status_code == 201
        body = accepted.json()
        assert body["status"] == "parsed"
        assert body["media_type"] == "text/markdown"
        assert "storage_key" not in body

        too_large = client.post(
            url,
            files={"file": ("large.txt", b"x" * 17, "text/plain")},
        )
        assert too_large.status_code == 413
        assert len(list((tmp_path / "documents").iterdir())) == 1
