"""在独立 PostgreSQL 模式中验证切块重试、并发和整批事务边界。"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.documents import get_document_store
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Chunk, Document, KnowledgeBase, Workspace
from app.db.session import get_session
from app.knowledge import chunk_repository
from app.knowledge.chunk_repository import (
    ChunkingConflictError,
    ChunkingLimitError,
    ChunkSourceError,
    ChunkStateError,
    DocumentNotFoundError,
    DocumentNotParsedError,
    persist_document_chunks,
)
from app.knowledge.storage import LocalDocumentStore
from app.main import create_app
from auth_helpers import create_authenticated_user


@dataclass
class ChunkContext:
    client: TestClient
    engine: Engine
    store: LocalDocumentStore
    root: Path
    workspace_id: UUID
    knowledge_base_id: UUID

    def url(self, document_id: UUID) -> str:
        return (
            f"/api/v1/workspaces/{self.workspace_id}"
            f"/knowledge-bases/{self.knowledge_base_id}"
            f"/documents/{document_id}/chunks"
        )

    def document(self, content: str = "重启后恢复服务。" * 10, *, status: str = "parsed") -> UUID:
        document_id = uuid4()
        data = content.encode("utf-8")
        stored = self.store.save(BytesIO(data), document_id, len(data))
        with Session(self.engine) as session, session.begin():
            session.add(
                Document(
                    id=document_id,
                    knowledge_base_id=self.knowledge_base_id,
                    original_filename="产品说明.txt",
                    media_type="text/plain",
                    storage_key=stored.storage_key,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    status=status,
                )
            )
        return document_id

    def persist(self, document_id: UUID, *, chunk_size: int = 8, overlap: int = 0) -> list[Chunk]:
        with Session(self.engine, expire_on_commit=False) as session:
            return persist_document_chunks(
                session, self.store, document_id, chunk_size=chunk_size, overlap=overlap
            )

    def rows(self, document_id: UUID) -> list[Chunk]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(Chunk)
                    .where(Chunk.document_id == document_id)
                    .order_by(Chunk.chunk_index)
                )
            )


@pytest.fixture
def chunk_context(tmp_path: Path) -> Iterator[ChunkContext]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置 EAAP_TEST_DATABASE_URL 后运行文本块集成测试")
    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("测试数据库不能与 EAAP_DATABASE_URL 指向同一数据库")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("文本块集成测试需要独立的 PostgreSQL 数据库")

    # 并发故障测试也有数据库超时，避免锁等待导致测试无限挂起。
    engine = create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=15000 -c lock_timeout=10000",
        },
    )
    schema = f"eaap_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped_engine)

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        store = LocalDocumentStore(tmp_path)
        app = create_app()
        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[get_document_store] = lambda: store

        workspace_id, knowledge_base_id = uuid4(), uuid4()
        with Session(scoped_engine) as session, session.begin():
            session.add(Workspace(id=workspace_id, name="产品团队"))
            session.flush()
            session.add(
                KnowledgeBase(id=knowledge_base_id, workspace_id=workspace_id, name="技术文档")
            )
        with TestClient(app) as client:
            create_authenticated_user(client, scoped_engine, workspace_ids=[workspace_id])
            yield ChunkContext(
                client, scoped_engine, store, tmp_path, workspace_id, knowledge_base_id
            )
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


@pytest.mark.parametrize("status", ["parsed", "ready"])
def test_same_configuration_retry_preserves_ten_chunks_and_ids(
    chunk_context: ChunkContext, status: str
) -> None:
    document_id = chunk_context.document(status=status)
    first = chunk_context.persist(document_id)
    second = chunk_context.persist(document_id)
    assert len(first) == len(second) == len(chunk_context.rows(document_id)) == 10
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert [chunk.chunk_index for chunk in second] == list(range(1, 11))
    with Session(chunk_context.engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert (document.chunk_size, document.chunk_overlap, document.chunking_version) == (
            8, 0, "character-v1"
        )
        assert document.status == status


def test_concurrent_sessions_return_one_complete_batch(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    barrier = Barrier(2, timeout=5)

    def save() -> list[UUID]:
        # 屏障放在调用前；不能在取得文档行锁后等待另一个请求。
        barrier.wait()
        return [chunk.id for chunk in chunk_context.persist(document_id)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future, second_future = executor.submit(save), executor.submit(save)
        first_ids = first_future.result(timeout=20)
        second_ids = second_future.result(timeout=20)
    assert first_ids == second_ids
    assert len(first_ids) == len(chunk_context.rows(document_id)) == 10


def test_commit_failure_rolls_back_batch_and_configuration_then_allows_retry(
    chunk_context: ChunkContext,
) -> None:
    document_id = chunk_context.document()

    def fail_before_commit(session: Session) -> None:
        session.flush()
        assert session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        ) == 10
        document = session.get(Document, document_id)
        assert document is not None and document.chunk_size == 8
        raise RuntimeError("模拟提交前故障")

    with Session(chunk_context.engine) as session:
        event.listen(session, "before_commit", fail_before_commit)
        try:
            with pytest.raises(RuntimeError, match="模拟提交前故障"):
                persist_document_chunks(
                    session, chunk_context.store, document_id, chunk_size=8, overlap=0
                )
        finally:
            event.remove(session, "before_commit", fail_before_commit)
    assert chunk_context.rows(document_id) == []
    with Session(chunk_context.engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.chunk_size is document.chunk_overlap is document.chunking_version is None
    assert len(chunk_context.persist(document_id)) == 10


@pytest.mark.parametrize(("chunk_size", "overlap"), [(9, 0), (8, 1)])
def test_changed_configuration_does_not_replace_existing_chunks(
    chunk_context: ChunkContext, chunk_size: int, overlap: int
) -> None:
    document_id = chunk_context.document()
    existing_ids = [chunk.id for chunk in chunk_context.persist(document_id)]
    with pytest.raises(ChunkingConflictError):
        chunk_context.persist(document_id, chunk_size=chunk_size, overlap=overlap)
    assert [chunk.id for chunk in chunk_context.rows(document_id)] == existing_ids


def test_algorithm_version_change_requires_explicit_rebuild(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    existing_ids = [chunk.id for chunk in chunk_context.persist(document_id)]
    with Session(chunk_context.engine) as session, session.begin():
        document = session.get(Document, document_id)
        assert document is not None
        document.chunking_version = "character-v0"
    with pytest.raises(ChunkingConflictError):
        chunk_context.persist(document_id)
    assert [chunk.id for chunk in chunk_context.rows(document_id)] == existing_ids


@pytest.mark.parametrize("status", ["uploaded", "processing", "failed"])
def test_unparsed_document_cannot_be_chunked(chunk_context: ChunkContext, status: str) -> None:
    document_id = chunk_context.document(status=status)
    with pytest.raises(DocumentNotParsedError):
        chunk_context.persist(document_id)
    assert chunk_context.rows(document_id) == []


def test_missing_document_has_explicit_error(chunk_context: ChunkContext) -> None:
    with pytest.raises(DocumentNotFoundError):
        chunk_context.persist(uuid4())


@pytest.mark.parametrize("configuration_only", [True, False])
def test_partial_state_is_rejected_without_silent_repair(
    chunk_context: ChunkContext, configuration_only: bool
) -> None:
    document_id = chunk_context.document()
    with Session(chunk_context.engine) as session, session.begin():
        if configuration_only:
            document = session.get(Document, document_id)
            assert document is not None
            document.chunk_size, document.chunk_overlap, document.chunking_version = 8, 0, "character-v1"
        else:
            session.add(
                Chunk(document_id=document_id, chunk_index=1, content="重启后恢复服务。", start_char=0, end_char=8)
            )
    with pytest.raises(ChunkStateError):
        chunk_context.persist(document_id)
    assert len(chunk_context.rows(document_id)) == (0 if configuration_only else 1)


def test_missing_file_leaves_no_chunks_or_configuration(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    chunk_context.store.delete(f"documents/{document_id.hex}")
    with pytest.raises(ChunkSourceError) as error:
        chunk_context.persist(document_id)
    assert isinstance(error.value.__cause__, OSError)
    assert chunk_context.rows(document_id) == []
    with Session(chunk_context.engine) as session:
        document = session.get(Document, document_id)
        assert document is not None and document.chunk_size is None


def test_database_rejects_duplicate_document_and_index(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    chunk_context.persist(document_id)
    with Session(chunk_context.engine) as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                session.add(
                    Chunk(document_id=document_id, chunk_index=1, content="重启后恢复服务。", start_char=0, end_char=8)
                )
                session.flush()
    # 编号只在同一文档内唯一，其他文档可以有第 1 块。
    other_id = chunk_context.document()
    assert chunk_context.persist(other_id)[0].chunk_index == 1


@pytest.mark.parametrize(
    ("chunk_index", "start_char", "end_char"),
    [(0, 0, 8), (1, -1, 7), (1, 0, 0), (1, 0, 7)],
)
def test_database_rejects_invalid_index_or_character_range(
    chunk_context: ChunkContext, chunk_index: int, start_char: int, end_char: int
) -> None:
    document_id = chunk_context.document()
    with Session(chunk_context.engine) as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                session.add(
                    Chunk(
                        document_id=document_id,
                        chunk_index=chunk_index,
                        content="重启后恢复服务。",
                        start_char=start_char,
                        end_char=end_char,
                    )
                )
                session.flush()
    assert chunk_context.rows(document_id) == []


def test_chinese_crlf_locations_reconstruct_parsed_text_and_preserve_file(
    chunk_context: ChunkContext,
) -> None:
    original = "快速开始\r\n重启后恢复服务。\r\n故障排查\r\n重启后恢复服务。"
    document_id = chunk_context.document(original)
    storage_key = f"documents/{document_id.hex}"
    parsed = chunk_context.store.read_text_utf8(storage_key)
    chunks = chunk_context.persist(document_id, chunk_size=9, overlap=2)
    assert (chunk_context.root / storage_key).read_bytes() == original.encode("utf-8")
    assert parsed == original.replace("\r\n", "\n")
    assert chunks[0].start_char == 0 and chunks[-1].end_char == len(parsed)
    for chunk in chunks:
        assert chunk.content == parsed[chunk.start_char:chunk.end_char]
    reconstructed = chunks[0].content + "".join(chunk.content[2:] for chunk in chunks[1:])
    assert reconstructed == parsed


@pytest.mark.parametrize(
    "configuration",
    [
        {"chunk_size": 8, "chunk_overlap": None, "chunking_version": "character-v1"},
        {"chunk_size": 0, "chunk_overlap": 0, "chunking_version": "character-v1"},
        {"chunk_size": 8, "chunk_overlap": 8, "chunking_version": "character-v1"},
        {"chunk_size": 8, "chunk_overlap": 0, "chunking_version": " "},
    ],
)
def test_database_rejects_partial_or_invalid_document_configuration(
    chunk_context: ChunkContext, configuration: dict[str, object]
) -> None:
    document_id = chunk_context.document()
    with Session(chunk_context.engine) as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                document = session.get(Document, document_id)
                assert document is not None
                for name, value in configuration.items():
                    setattr(document, name, value)
                session.flush()
    with Session(chunk_context.engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.chunk_size is document.chunk_overlap is document.chunking_version is None


def test_api_create_retry_and_get_return_same_ordered_chunks(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    url = chunk_context.url(document_id)
    assert chunk_context.client.get(url).json() == []
    first = chunk_context.client.post(url, json={"chunk_size": 8, "overlap": 0})
    second = chunk_context.client.post(url, json={"chunk_size": 8, "overlap": 0})
    fetched = chunk_context.client.get(url)
    assert first.status_code == second.status_code == fetched.status_code == 200
    assert first.json() == second.json() == fetched.json()
    assert len(first.json()) == 10
    assert [row["chunk_index"] for row in first.json()] == list(range(1, 11))
    assert all(row["document_id"] == str(document_id) for row in first.json())
    assert len(chunk_context.rows(document_id)) == 10


def test_api_changed_parameters_returns_conflict_without_overwriting(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    url = chunk_context.url(document_id)
    first = chunk_context.client.post(url, json={"chunk_size": 8, "overlap": 0})
    conflict = chunk_context.client.post(url, json={"chunk_size": 12, "overlap": 0})
    assert first.status_code == 200 and conflict.status_code == 409
    assert chunk_context.client.get(url).json() == first.json()


def test_api_unparsed_document_returns_conflict(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document(status="failed")
    response = chunk_context.client.post(chunk_context.url(document_id), json={"chunk_size": 8, "overlap": 0})
    assert response.status_code == 409
    assert chunk_context.rows(document_id) == []


def test_api_missing_file_reports_failure_without_disclosing_path(
    chunk_context: ChunkContext,
) -> None:
    document_id = chunk_context.document()
    storage_key = f"documents/{document_id.hex}"
    chunk_context.store.delete(storage_key)
    response = chunk_context.client.post(
        chunk_context.url(document_id), json={"chunk_size": 8, "overlap": 0}
    )
    assert response.status_code == 500
    assert str(chunk_context.root) not in response.text
    assert storage_key not in response.text
    assert document_id.hex not in response.text
    assert "FileNotFoundError" not in response.text
    assert chunk_context.rows(document_id) == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"chunk_size": 0, "overlap": 0},
        {"chunk_size": 8, "overlap": -1},
        {"chunk_size": 8, "overlap": 8},
        {"chunk_size": True, "overlap": 0},
        {"chunk_size": 8.5, "overlap": 0},
    ],
)
def test_api_invalid_parameters_return_validation_error(
    chunk_context: ChunkContext, parameters: dict[str, object]
) -> None:
    document_id = chunk_context.document()
    response = chunk_context.client.post(chunk_context.url(document_id), json=parameters)
    assert response.status_code == 422
    assert chunk_context.rows(document_id) == []


def test_api_enforces_workspace_knowledge_base_and_document_scope(chunk_context: ChunkContext) -> None:
    document_id = chunk_context.document()
    chunk_context.persist(document_id)
    other_workspace_id, other_base_id = uuid4(), uuid4()
    with Session(chunk_context.engine) as session, session.begin():
        session.add(Workspace(id=other_workspace_id, name="其他团队"))
        session.flush()
        session.add(KnowledgeBase(id=other_base_id, workspace_id=other_workspace_id, name="其他文档"))
    wrong_urls = [
        f"/api/v1/workspaces/{other_workspace_id}/knowledge-bases/{chunk_context.knowledge_base_id}/documents/{document_id}/chunks",
        f"/api/v1/workspaces/{other_workspace_id}/knowledge-bases/{other_base_id}/documents/{document_id}/chunks",
        chunk_context.url(uuid4()),
    ]
    for url in wrong_urls:
        assert chunk_context.client.get(url).status_code == 404
        assert chunk_context.client.post(url, json={"chunk_size": 8, "overlap": 0}).status_code == 404
    assert len(chunk_context.rows(document_id)) == 10


def _assert_no_saved_configuration_or_chunks(context: ChunkContext, document_id: UUID) -> None:
    assert context.rows(document_id) == []
    with Session(context.engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.chunk_size is document.chunk_overlap is document.chunking_version is None


@pytest.mark.parametrize(
    "parameters",
    [
        {"chunk_size": 2_147_483_648, "overlap": 0},
        {"chunk_size": 2_147_483_647, "overlap": 2_147_483_648},
    ],
)
def test_api_configuration_outside_database_integer_range_is_rejected(
    chunk_context: ChunkContext, parameters: dict[str, int]
) -> None:
    document_id = chunk_context.document()
    response = chunk_context.client.post(chunk_context.url(document_id), json=parameters)
    assert response.status_code == 422
    _assert_no_saved_configuration_or_chunks(chunk_context, document_id)


def test_nul_in_source_has_explicit_error_and_safe_api_failure(chunk_context: ChunkContext) -> None:
    text = "产品说明\x00私密内容"
    document_id = chunk_context.document(text)
    with pytest.raises(ChunkSourceError):
        chunk_context.persist(document_id)
    _assert_no_saved_configuration_or_chunks(chunk_context, document_id)

    response = chunk_context.client.post(
        chunk_context.url(document_id), json={"chunk_size": 8, "overlap": 0}
    )
    assert response.status_code == 500
    assert str(chunk_context.root) not in response.text
    assert "私密内容" not in response.text
    assert "ChunkSourceError" not in response.text
    _assert_no_saved_configuration_or_chunks(chunk_context, document_id)
    assert (chunk_context.root / f"documents/{document_id.hex}").read_bytes() == text.encode()


def test_excessive_overlap_is_rejected_before_nonempty_split(
    chunk_context: ChunkContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 500/499 的步长只有 1；10500 字符会形成 10001 块，超过单批上限。
    document_id = chunk_context.document("文" * 10_500)
    original_split = chunk_repository.split_text
    validation_calls: list[str] = []

    def reject_materialization(document_id: UUID, text: str, **parameters):
        if text:
            pytest.fail("超过单批上限时不应先物化文本块列表")
        validation_calls.append(text)
        return original_split(document_id, text, **parameters)

    monkeypatch.setattr(chunk_repository, "split_text", reject_materialization)
    with pytest.raises(ChunkingLimitError):
        chunk_context.persist(document_id, chunk_size=500, overlap=499)
    response = chunk_context.client.post(
        chunk_context.url(document_id), json={"chunk_size": 500, "overlap": 499}
    )
    assert response.status_code == 422
    assert validation_calls == ["", ""]
    _assert_no_saved_configuration_or_chunks(chunk_context, document_id)


@pytest.mark.parametrize(
    ("constant", "limit", "accepted"),
    [
        ("MAX_CHUNKS_PER_DOCUMENT", 2, True),
        ("MAX_CHUNKS_PER_DOCUMENT", 1, False),
        ("MAX_CHUNK_CHARACTERS_PER_DOCUMENT", 16, True),
        ("MAX_CHUNK_CHARACTERS_PER_DOCUMENT", 15, False),
    ],
)
def test_batch_limits_include_exact_boundary_and_account_for_overlap(
    chunk_context: ChunkContext,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    limit: int,
    accepted: bool,
) -> None:
    # 原文 12 字符，块长 8、重叠 4：两块各有 8 字符，总存储量为 16。
    document_id = chunk_context.document("文" * 12)
    monkeypatch.setattr(chunk_repository, constant, limit)
    if accepted:
        chunks = chunk_context.persist(document_id, chunk_size=8, overlap=4)
        assert len(chunks) == 2
        assert sum(len(chunk.content) for chunk in chunks) == 16
    else:
        original_split = chunk_repository.split_text

        def reject_materialization(document_id: UUID, text: str, **parameters):
            if text:
                pytest.fail("超过阈值时不应物化文本块列表")
            return original_split(document_id, text, **parameters)

        monkeypatch.setattr(chunk_repository, "split_text", reject_materialization)
        with pytest.raises(ChunkingLimitError):
            chunk_context.persist(document_id, chunk_size=8, overlap=4)
        _assert_no_saved_configuration_or_chunks(chunk_context, document_id)


def test_uploaded_document_can_be_chunked_using_api_defaults(chunk_context: ChunkContext) -> None:
    documents_url = (
        f"/api/v1/workspaces/{chunk_context.workspace_id}"
        f"/knowledge-bases/{chunk_context.knowledge_base_id}/documents"
    )
    original = "# 产品指南\r\n\r\n重启后恢复服务。".encode("utf-8")
    uploaded = chunk_context.client.post(
        documents_url, files={"file": ("产品指南.md", original, "text/markdown")}
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["status"] == "parsed"
    document_id = UUID(uploaded.json()["id"])
    assert chunk_context.rows(document_id) == []

    response = chunk_context.client.post(chunk_context.url(document_id), json={})
    assert response.status_code == 200
    assert chunk_context.client.get(chunk_context.url(document_id)).json() == response.json()
    assert len(response.json()) == 1
    assert response.json()[0]["content"] == chunk_context.store.read_text_utf8(
        f"documents/{document_id.hex}"
    )
    with Session(chunk_context.engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert (document.chunk_size, document.chunk_overlap, document.chunking_version) == (
            500, 50, "character-v1"
        )
