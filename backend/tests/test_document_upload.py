"""使用独立 PostgreSQL 模式和临时目录验证同步文档上传。"""

import os
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.documents import get_max_upload_bytes
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Document
from app.db.session import get_session
from app.knowledge.audit import audit_document_storage
from app.knowledge.storage import LocalDocumentStore
from app.main import create_app

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@dataclass
class UploadContext:
    client: TestClient
    engine: Engine
    root: Path
    workspace_id: str
    knowledge_base_id: str

    @property
    def documents_url(self) -> str:
        return (
            f"/api/v1/workspaces/{self.workspace_id}"
            f"/knowledge-bases/{self.knowledge_base_id}/documents"
        )

    def rows(self) -> list[Document]:
        with Session(self.engine) as session:
            return list(session.scalars(select(Document)))

    def files(self) -> list[Path]:
        return [path for path in self.root.rglob("*") if path.is_file()]


@pytest.fixture
def upload_context(tmp_path: Path) -> Iterator[UploadContext]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置 EAAP_TEST_DATABASE_URL 后运行上传集成测试")

    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("测试数据库不能与 EAAP_DATABASE_URL 指向同一数据库")

    engine = create_engine(database_url, poolclass=NullPool)
    schema = f"eaap_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    try:
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped_engine)

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        # 每项测试有自己的存储根目录，不能写入项目日常使用的 uploads 目录。
        store = LocalDocumentStore(tmp_path)

        def test_store() -> LocalDocumentStore:
            return store

        from app.api.documents import get_document_store

        app = create_app()
        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[get_document_store] = test_store
        app.dependency_overrides[get_max_upload_bytes] = lambda: MAX_UPLOAD_BYTES

        with TestClient(app) as client:
            workspace_response = client.post(
                "/api/v1/workspaces", json={"name": "Product"}
            )
            assert workspace_response.status_code == 201
            workspace_id = workspace_response.json()["id"]
            base_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/knowledge-bases",
                json={"name": "Guides"},
            )
            assert base_response.status_code == 201
            knowledge_base_id = base_response.json()["id"]

            yield UploadContext(
                client=client,
                engine=scoped_engine,
                root=tmp_path,
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
            )
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def _upload(
    context: UploadContext, filename: str, content: bytes, media_type: str
):
    return context.client.post(
        context.documents_url,
        files={"file": (filename, content, media_type)},
    )


@pytest.mark.parametrize(
    ("filename", "content", "submitted_media_type", "expected_media_type"),
    [
        ("guide.txt", "你好，产品指南。\n".encode(), "text/plain", "text/plain"),
        ("README.md", b"# Guide\n\nUse the API.\n", "application/octet-stream", "text/markdown"),
    ],
)
def test_upload_parses_and_preserves_original_bytes(
    upload_context: UploadContext,
    filename: str,
    content: bytes,
    submitted_media_type: str,
    expected_media_type: str,
) -> None:
    response = _upload(upload_context, filename, content, submitted_media_type)
    assert response.status_code == 201
    body = response.json()
    assert UUID(body["id"])
    assert body["knowledge_base_id"] == upload_context.knowledge_base_id
    assert body["original_filename"] == filename
    assert body["media_type"] == expected_media_type
    assert body["byte_size"] == len(content)
    assert body["sha256"] == sha256(content).hexdigest()
    assert body["status"] == "parsed"
    assert body["error_code"] is None
    assert "storage_key" not in body

    rows = upload_context.rows()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == body["id"]
    assert str(row.knowledge_base_id) == upload_context.knowledge_base_id
    assert row.byte_size == len(content)
    assert row.sha256 == sha256(content).hexdigest()
    stored_path = (upload_context.root / row.storage_key).resolve(strict=True)
    assert stored_path.is_relative_to(upload_context.root.resolve(strict=True))
    assert stored_path.read_bytes() == content
    assert upload_context.files() == [stored_path]

    get_response = upload_context.client.get(
        f"{upload_context.documents_url}/{body['id']}"
    )
    assert get_response.status_code == 200
    assert get_response.json() == body


def test_repeated_upload_creates_independent_documents(upload_context: UploadContext) -> None:
    content = b"# Identical content\n"
    first = _upload(upload_context, "same.md", content, "text/markdown")
    second = _upload(upload_context, "same.md", content, "text/markdown")
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    rows = upload_context.rows()
    assert len(rows) == 2
    assert rows[0].storage_key != rows[1].storage_key
    assert len(upload_context.files()) == 2
    for row in rows:
        assert (upload_context.root / row.storage_key).read_bytes() == content


def test_storage_audit_reads_real_records_without_mutating_them(upload_context: UploadContext) -> None:
    assert _upload(upload_context, "guide.txt", b"guide", "text/plain").status_code == 201
    assert _upload(upload_context, "broken.txt", b"\xff", "text/plain").status_code == 201
    rows = upload_context.rows()
    original = {(row.id, row.storage_key, row.status) for row in rows}
    store = LocalDocumentStore(upload_context.root)
    with Session(upload_context.engine) as session:
        report = audit_document_storage(session, store)
        assert report.orphan_candidates == report.recent_untracked == report.missing_files == ()

    missing = rows[0].storage_key
    store.delete(missing)
    orphan = store.save(BytesIO(b"orphan"), uuid4(), 1024).storage_key
    with Session(upload_context.engine) as session:
        report = audit_document_storage(session, store, grace_seconds=0)
        assert report.missing_files == (missing,)
        assert report.orphan_candidates == (orphan,)
    assert {(row.id, row.storage_key, row.status) for row in upload_context.rows()} == original
    assert (upload_context.root / orphan).read_bytes() == b"orphan"


@pytest.mark.parametrize(
    ("filename", "content", "media_type", "expected_status"),
    [
        ("empty.txt", b"", "text/plain", 422),
        ("manual.pdf", b"PDF", "application/pdf", 415),
        ("../outside.txt", b"hello", "text/plain", 422),
        (r"..\outside.txt", b"hello", "text/plain", 422),
        ("line\nbreak.txt", b"hello", "text/plain", 422),
    ],
)
def test_rejected_upload_leaves_no_row_or_file(
    upload_context: UploadContext,
    filename: str,
    content: bytes,
    media_type: str,
    expected_status: int,
) -> None:
    response = _upload(upload_context, filename, content, media_type)
    assert response.status_code == expected_status
    assert upload_context.rows() == []
    assert upload_context.files() == []


def test_file_field_is_required(upload_context: UploadContext) -> None:
    response = upload_context.client.post(upload_context.documents_url, data={})
    assert response.status_code == 422
    assert upload_context.rows() == []
    assert upload_context.files() == []


def test_upload_limit_is_inclusive(upload_context: UploadContext) -> None:
    at_limit = b"x" * MAX_UPLOAD_BYTES
    accepted = _upload(upload_context, "limit.txt", at_limit, "text/plain")
    assert accepted.status_code == 201
    assert accepted.json()["status"] == "parsed"

    too_large = _upload(upload_context, "oversized.txt", at_limit + b"x", "text/plain")
    assert too_large.status_code == 413
    assert len(upload_context.rows()) == 1
    assert len(upload_context.files()) == 1


def test_invalid_utf8_retains_failed_document(upload_context: UploadContext) -> None:
    content = b"private:\xff\xfe"
    response = _upload(upload_context, "broken.txt", content, "text/plain")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "INVALID_UTF8"
    assert body["byte_size"] == len(content)
    assert body["sha256"] == sha256(content).hexdigest()
    assert "storage_key" not in body
    assert "private" not in response.text
    assert "UnicodeDecodeError" not in response.text

    rows = upload_context.rows()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error_code == "INVALID_UTF8"
    assert (upload_context.root / rows[0].storage_key).read_bytes() == content

    get_response = upload_context.client.get(
        f"{upload_context.documents_url}/{body['id']}"
    )
    assert get_response.status_code == 200
    assert get_response.json() == body


def test_document_routes_enforce_parent_scope(upload_context: UploadContext) -> None:
    other_workspace = upload_context.client.post(
        "/api/v1/workspaces", json={"name": "Other"}
    )
    assert other_workspace.status_code == 201
    other_workspace_id = other_workspace.json()["id"]
    other_base = upload_context.client.post(
        f"/api/v1/workspaces/{other_workspace_id}/knowledge-bases",
        json={"name": "Other Guides"},
    )
    assert other_base.status_code == 201
    other_base_id = other_base.json()["id"]

    wrong_parent = upload_context.client.post(
        f"/api/v1/workspaces/{other_workspace_id}"
        f"/knowledge-bases/{upload_context.knowledge_base_id}/documents",
        files={"file": ("guide.txt", b"secret", "text/plain")},
    )
    assert wrong_parent.status_code == 404
    missing_base = upload_context.client.post(
        f"/api/v1/workspaces/{upload_context.workspace_id}"
        f"/knowledge-bases/{uuid4()}/documents",
        files={"file": ("guide.txt", b"secret", "text/plain")},
    )
    assert missing_base.status_code == 404
    assert upload_context.rows() == []
    assert upload_context.files() == []

    valid = _upload(upload_context, "guide.txt", b"secret", "text/plain")
    assert valid.status_code == 201
    document_id = valid.json()["id"]
    assert (
        upload_context.client.get(
            f"/api/v1/workspaces/{other_workspace_id}"
            f"/knowledge-bases/{upload_context.knowledge_base_id}/documents/{document_id}"
        ).status_code
        == 404
    )
    assert (
        upload_context.client.get(
            f"/api/v1/workspaces/{other_workspace_id}"
            f"/knowledge-bases/{other_base_id}/documents/{document_id}"
        ).status_code
        == 404
    )
    assert (
        upload_context.client.get(
            f"{upload_context.documents_url}/{uuid4()}"
        ).status_code
        == 404
    )
