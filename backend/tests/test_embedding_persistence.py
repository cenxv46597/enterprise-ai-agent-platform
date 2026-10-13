"""真实 pgvector 事务测试；模型依赖替身仅用于控制结果和故障，不代表检索质量。"""

import os
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import documents as document_api
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Chunk, Document, KnowledgeBase, Workspace
from app.db.session import get_session
from app.knowledge.chunk_repository import persist_document_chunks
from app.knowledge.embedding_repository import (
    EmbeddingConflictError,
    EmbeddingLimitError,
    embed_document_chunks,
)
from app.knowledge.storage import LocalDocumentStore
from app.main import create_app
from auth_helpers import create_authenticated_user
from app.providers.embeddings import (
    EmbeddingError,
    EmbeddingProfile,
    EmbeddingTimeoutError,
)


DIMENSIONS = 1024
DEFAULT_PROFILE = EmbeddingProfile("https://example.test/v1", "contract-test-model", DIMENSIONS)


class ControlledProvider:
    """生成可精确断言的测试数据，并记录调用次数；不用于真实检索。"""

    def __init__(
        self,
        *,
        profile: EmbeddingProfile = DEFAULT_PROFILE,
        vectors: list[list[float]] | None = None,
        error: EmbeddingError | None = None,
        on_call: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self.profile = profile
        self.vectors = vectors
        self.error = error
        self.on_call = on_call
        self.calls = 0
        self.inputs: list[tuple[str, ...]] = []
        self._lock = Lock()

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            self.calls += 1
            self.inputs.append(tuple(texts))
        if self.on_call:
            self.on_call(texts)
        if self.error:
            raise self.error
        if self.vectors is not None:
            return [list(vector) for vector in self.vectors]
        return [[float(index + 1)] + [0.0] * (DIMENSIONS - 1) for index in range(len(texts))]


@dataclass
class EmbeddingContext:
    client: TestClient
    engine: Engine
    store: LocalDocumentStore
    workspace_id: UUID
    knowledge_base_id: UUID
    provider: ControlledProvider

    def url(self, document_id: UUID) -> str:
        return (
            f"/api/v1/workspaces/{self.workspace_id}"
            f"/knowledge-bases/{self.knowledge_base_id}"
            f"/documents/{document_id}/embeddings"
        )

    def document(self, *, block_count: int = 3, with_chunks: bool = True) -> UUID:
        document_id = uuid4()
        content = ("重启后恢复服务。" * block_count).encode("utf-8")
        stored = self.store.save(BytesIO(content), document_id, len(content))
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
                    status="parsed",
                )
            )
        if with_chunks:
            with Session(self.engine, expire_on_commit=False) as session:
                chunks = persist_document_chunks(
                    session, self.store, document_id, chunk_size=8, overlap=0
                )
                assert len(chunks) == block_count
        return document_id

    def embed(self, document_id: UUID, provider: ControlledProvider | None = None, **options):
        with Session(self.engine, expire_on_commit=False) as session:
            return embed_document_chunks(session, provider or self.provider, document_id, **options)

    def rows(self, document_id: UUID) -> list[Chunk]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(Chunk)
                    .where(Chunk.document_id == document_id)
                    .order_by(Chunk.chunk_index)
                )
            )

    def assert_unembedded(self, document_id: UUID, *, empty_kb_profile: bool = True) -> None:
        with Session(self.engine) as session:
            document = session.get(Document, document_id)
            assert document is not None and document.status == "parsed"
            assert all(chunk.embedding is None for chunk in self.rows(document_id))
            if empty_kb_profile:
                knowledge_base = session.get(KnowledgeBase, self.knowledge_base_id)
                assert knowledge_base is not None
                assert (
                    knowledge_base.embedding_provider,
                    knowledge_base.embedding_model,
                    knowledge_base.embedding_dimensions,
                ) == (None, None, None)


@pytest.fixture
def embedding_context(tmp_path: Path) -> Iterator[EmbeddingContext]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置 EAAP_TEST_DATABASE_URL 后运行向量持久化集成测试")
    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("测试数据库不能与 EAAP_DATABASE_URL 指向同一数据库")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("向量持久化集成测试需要独立的 PostgreSQL 数据库")

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
        store = LocalDocumentStore(tmp_path)
        provider = ControlledProvider()

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[document_api.get_document_store] = lambda: store
        app.dependency_overrides[document_api.get_embedding_provider] = lambda: provider
        workspace_id, knowledge_base_id = uuid4(), uuid4()
        with Session(scoped_engine) as session, session.begin():
            session.add(Workspace(id=workspace_id, name="产品团队"))
            session.flush()
            session.add(
                KnowledgeBase(id=knowledge_base_id, workspace_id=workspace_id, name="技术文档")
            )
        with TestClient(app) as client:
            create_authenticated_user(client, scoped_engine, workspace_ids=[workspace_id])
            yield EmbeddingContext(
                client, scoped_engine, store, workspace_id, knowledge_base_id, provider
            )
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_roundtrip_retry_and_model_call_without_open_database_transaction(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document()
    original_ids = [chunk.id for chunk in context.rows(document_id)]
    with Session(context.engine, expire_on_commit=False) as session:
        def check_transaction(texts: Sequence[str]) -> None:
            assert not session.in_transaction()
            assert len(texts) == 3

        provider = ControlledProvider(on_call=check_transaction)
        first = embed_document_chunks(session, provider, document_id)
    second = context.embed(document_id, provider)
    assert provider.calls == 1
    assert first.document_id == second.document_id == document_id
    assert first.status == second.status == "ready"
    assert first.model == second.model == DEFAULT_PROFILE.model
    assert first.dimensions == second.dimensions == DIMENSIONS
    assert first.chunk_count == second.chunk_count == 3
    assert first.reused is False and second.reused is True
    rows = context.rows(document_id)
    assert [chunk.id for chunk in rows] == original_ids
    for index, chunk in enumerate(rows):
        assert len(chunk.embedding) == DIMENSIONS
        assert list(chunk.embedding) == pytest.approx([index + 1.0] + [0.0] * (DIMENSIONS - 1))
    with Session(context.engine) as session:
        document = session.get(Document, document_id)
        knowledge_base = session.get(KnowledgeBase, context.knowledge_base_id)
        assert document is not None and document.status == "ready"
        assert knowledge_base is not None
        assert (
            knowledge_base.embedding_provider,
            knowledge_base.embedding_model,
            knowledge_base.embedding_dimensions,
        ) == (DEFAULT_PROFILE.provider, DEFAULT_PROFILE.model, DIMENSIONS)


@pytest.mark.parametrize("wrong_dimensions", [False, True])
def test_invalid_model_batch_keeps_every_chunk_unembedded(
    embedding_context: EmbeddingContext, wrong_dimensions: bool
) -> None:
    context = embedding_context
    document_id = context.document()
    vectors = [[1.0] + [0.0] * (DIMENSIONS - 2)] * 3 if wrong_dimensions else [[1.0] + [0.0] * (DIMENSIONS - 1)] * 2
    provider = ControlledProvider(vectors=vectors)
    with pytest.raises(EmbeddingError):
        context.embed(document_id, provider)
    assert provider.calls == 1
    context.assert_unembedded(document_id)


def test_commit_failure_rolls_back_vectors_profile_and_ready_status(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document()
    provider = ControlledProvider()

    def fail_write_commit(session: Session) -> None:
        # 读取快照的事务可能也提交；仅在模型调用后的写入事务注入失败。
        if provider.calls == 0:
            return
        session.flush()
        assert session.scalar(
            select(func.count()).select_from(Chunk).where(
                Chunk.document_id == document_id, Chunk.embedding.is_not(None)
            )
        ) == 3
        document = session.get(Document, document_id)
        assert document is not None and document.status == "ready"
        raise RuntimeError("模拟向量提交失败")

    with Session(context.engine, expire_on_commit=False) as session:
        event.listen(session, "before_commit", fail_write_commit)
        try:
            with pytest.raises(RuntimeError, match="模拟向量提交失败"):
                embed_document_chunks(session, provider, document_id)
        finally:
            event.remove(session, "before_commit", fail_write_commit)
    context.assert_unembedded(document_id)
    assert context.embed(document_id, provider).status == "ready"


def test_concurrent_sessions_write_one_complete_vector_batch(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document()
    barrier = Barrier(2, timeout=5)
    provider = ControlledProvider(on_call=lambda texts: barrier.wait())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(context.embed, document_id, provider)
        second = executor.submit(context.embed, document_id, provider)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert provider.calls == 2
    assert sorted(result.reused for result in results) == [False, True]
    rows = context.rows(document_id)
    assert len(rows) == 3 and all(chunk.embedding is not None for chunk in rows)
    assert all(len(chunk.embedding) == DIMENSIONS for chunk in rows)


@pytest.mark.parametrize(
    "other_profile",
    [
        EmbeddingProfile(DEFAULT_PROFILE.provider, "different-test-model", DIMENSIONS),
        EmbeddingProfile("https://other.example.test/v1", DEFAULT_PROFILE.model, DIMENSIONS),
    ],
)
def test_same_knowledge_base_cannot_mix_model_or_provider_identity(
    embedding_context: EmbeddingContext, other_profile: EmbeddingProfile
) -> None:
    context = embedding_context
    first_id, second_id = context.document(), context.document()
    context.embed(first_id)
    with pytest.raises(EmbeddingConflictError):
        context.embed(second_id, ControlledProvider(profile=other_profile))
    context.assert_unembedded(second_id, empty_kb_profile=False)
    assert context.embed(first_id).reused is True


def test_chunk_change_during_model_call_prevents_stale_vector_write(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document()

    def change_chunk(texts: Sequence[str]) -> None:
        with Session(context.engine) as session, session.begin():
            chunk = session.scalar(
                select(Chunk).where(Chunk.document_id == document_id, Chunk.chunk_index == 1)
            )
            assert chunk is not None
            chunk.content = "更新后恢复服务。"

    provider = ControlledProvider(on_call=change_chunk)
    with pytest.raises(EmbeddingConflictError):
        context.embed(document_id, provider)
    context.assert_unembedded(document_id)
    assert context.rows(document_id)[0].content == "更新后恢复服务。"


def test_document_without_chunks_is_rejected_before_model_call(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document(with_chunks=False)
    with pytest.raises(EmbeddingConflictError):
        context.embed(document_id)
    assert context.client.post(context.url(document_id)).status_code == 409
    assert context.provider.calls == 0
    context.assert_unembedded(document_id)


def test_api_returns_summary_and_reuses_successful_result(embedding_context: EmbeddingContext) -> None:
    context = embedding_context
    document_id = context.document()
    first = context.client.post(context.url(document_id))
    second = context.client.post(context.url(document_id))
    assert first.status_code == second.status_code == 200
    for response in (first, second):
        body = response.json()
        assert body["status"] == "ready"
        assert body["model"] == DEFAULT_PROFILE.model
        assert body["dimensions"] == DIMENSIONS
        assert body["chunk_count"] == 3
    assert first.json()["reused"] is False and second.json()["reused"] is True
    assert context.provider.calls == 1


def test_api_parent_scope_is_checked_before_model_call(embedding_context: EmbeddingContext) -> None:
    context = embedding_context
    document_id = context.document()
    other_workspace_id, other_base_id = uuid4(), uuid4()
    with Session(context.engine) as session, session.begin():
        session.add(Workspace(id=other_workspace_id, name="其他团队"))
        session.flush()
        session.add(KnowledgeBase(id=other_base_id, workspace_id=other_workspace_id, name="其他文档"))
    wrong_urls = [
        f"/api/v1/workspaces/{other_workspace_id}/knowledge-bases/{context.knowledge_base_id}/documents/{document_id}/embeddings",
        f"/api/v1/workspaces/{other_workspace_id}/knowledge-bases/{other_base_id}/documents/{document_id}/embeddings",
        context.url(uuid4()),
    ]
    for url in wrong_urls:
        assert context.client.post(url).status_code == 404
    assert context.provider.calls == 0
    context.assert_unembedded(document_id)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(EmbeddingError("secret-model-details"), 502), (EmbeddingTimeoutError("secret-model-details"), 504)],
)
def test_api_model_failure_is_safe_and_keeps_document_retryable(
    embedding_context: EmbeddingContext, error: EmbeddingError, status_code: int
) -> None:
    context = embedding_context
    document_id = context.document()
    context.provider.error = error
    response = context.client.post(context.url(document_id))
    assert response.status_code == status_code
    assert "secret-model-details" not in response.text
    assert DEFAULT_PROFILE.provider not in response.text
    context.assert_unembedded(document_id)


def test_api_batch_limit_is_rejected_before_model_call(embedding_context: EmbeddingContext) -> None:
    context = embedding_context
    document_id = context.document(block_count=201)
    with pytest.raises(EmbeddingLimitError):
        context.embed(document_id, max_chunks=200)
    response = context.client.post(context.url(document_id))
    assert response.status_code == 422
    assert context.provider.calls == 0
    context.assert_unembedded(document_id)


def test_api_unparsed_document_is_rejected_before_model_call(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document()
    with Session(context.engine) as session, session.begin():
        document = session.get(Document, document_id)
        assert document is not None
        document.status = "failed"
    response = context.client.post(context.url(document_id))
    assert response.status_code == 409
    assert context.provider.calls == 0
    assert all(chunk.embedding is None for chunk in context.rows(document_id))


def test_api_missing_provider_configuration_reports_unavailable(
    embedding_context: EmbeddingContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = embedding_context
    document_id = context.document()
    settings = Settings(_env_file=None, embedding_base_url=None, embedding_api_key=None)
    monkeypatch.setattr(document_api, "Settings", lambda: settings)
    del context.client.app.dependency_overrides[document_api.get_embedding_provider]
    response = context.client.post(context.url(document_id))
    assert response.status_code == 503
    assert context.provider.calls == 0
    context.assert_unembedded(document_id)


def test_initial_snapshot_locks_prevent_mixed_document_and_vector_state(
    embedding_context: EmbeddingContext,
) -> None:
    context = embedding_context
    document_id = context.document()
    first_document_locked = Event()
    release_first_snapshot = Event()
    second_lock_attempted = Event()
    second_lock_acquired = Event()
    model_calls_aligned = Barrier(2, timeout=10)
    provider = ControlledProvider(on_call=lambda texts: model_calls_aligned.wait())

    def before_cursor_execute(connection, cursor, statement, parameters, execution_context, executemany):
        role = execution_context.execution_options.get("embedding_snapshot_race_role")
        if role == "second" and "knowledge_bases" in statement and "FOR UPDATE" in statement:
            second_lock_attempted.set()

    def after_cursor_execute(connection, cursor, statement, parameters, execution_context, executemany):
        role = execution_context.execution_options.get("embedding_snapshot_race_role")
        if role == "first" and "documents" in statement and "FOR UPDATE" in statement:
            if not first_document_locked.is_set():
                # SQL 已执行并持有锁，暂停在 ORM 继续读取状态和文本块之前。
                first_document_locked.set()
                assert release_first_snapshot.wait(timeout=15), "首个快照没有及时释放"
        if role == "second" and "knowledge_bases" in statement and "FOR UPDATE" in statement:
            second_lock_acquired.set()

    def run(role: str):
        with context.engine.connect() as connection:
            connection = connection.execution_options(embedding_snapshot_race_role=role)
            with Session(connection, expire_on_commit=False) as session:
                return embed_document_chunks(session, provider, document_id)

    event.listen(context.engine, "before_cursor_execute", before_cursor_execute)
    event.listen(context.engine, "after_cursor_execute", after_cursor_execute)
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        first = executor.submit(run, "first")
        assert first_document_locked.wait(timeout=5), "初始快照必须锁住文档行"
        second = executor.submit(run, "second")
        assert second_lock_attempted.wait(timeout=5), "第二个请求没有尝试获取知识库锁"

        # 以数据库 NOWAIT 的确定性错误证明锁仍被持有，避免靠等待时间猜测阻塞。
        with Session(context.engine) as probe:
            with pytest.raises(OperationalError) as error:
                probe.scalar(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.id == context.knowledge_base_id)
                    .with_for_update(nowait=True)
                )
            assert error.value.orig.sqlstate == "55P03"
        assert not second_lock_acquired.is_set()

        release_first_snapshot.set()
        results = [first.result(timeout=20), second.result(timeout=20)]
        assert second_lock_acquired.is_set()
        assert sorted(result.reused for result in results) == [False, True]
        assert provider.calls == 2
        rows = context.rows(document_id)
        assert len(rows) == 3 and all(chunk.embedding is not None for chunk in rows)
        assert context.embed(document_id, provider).reused is True
        assert provider.calls == 2
    finally:
        release_first_snapshot.set()
        executor.shutdown(wait=True, cancel_futures=True)
        event.remove(context.engine, "before_cursor_execute", before_cursor_execute)
        event.remove(context.engine, "after_cursor_execute", after_cursor_execute)


@pytest.mark.parametrize("damage", ["one_missing", "all_missing", "zero_vector"])
def test_existing_corrupt_vectors_report_database_state_failure_without_model_call(
    embedding_context: EmbeddingContext, damage: str
) -> None:
    context = embedding_context
    document_id = context.document()
    assert context.embed(document_id).status == "ready"
    with Session(context.engine) as session, session.begin():
        rows = list(
            session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            )
        )
        if damage == "one_missing":
            rows[0].embedding = None
        elif damage == "all_missing":
            for row in rows:
                row.embedding = None
        else:
            rows[0].embedding = [0.0] * DIMENSIONS

    def snapshot():
        return [
            (row.id, None if row.embedding is None else tuple(float(value) for value in row.embedding))
            for row in context.rows(document_id)
        ]

    damaged_snapshot = snapshot()
    assert context.provider.calls == 1
    response = context.client.post(context.url(document_id))
    assert response.status_code == 500
    assert context.provider.calls == 1
    assert snapshot() == damaged_snapshot
    with Session(context.engine) as session:
        document = session.get(Document, document_id)
        knowledge_base = session.get(KnowledgeBase, context.knowledge_base_id)
        assert document is not None and document.status == "ready"
        assert knowledge_base is not None
        assert (
            knowledge_base.embedding_provider,
            knowledge_base.embedding_model,
            knowledge_base.embedding_dimensions,
        ) == (DEFAULT_PROFILE.provider, DEFAULT_PROFILE.model, DIMENSIONS)
