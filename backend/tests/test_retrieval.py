"""真实 PostgreSQL 余弦检索测试；受控向量只验证检索规则，不代表模型质量。"""

import math
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import documents as document_api
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Chunk, Document, KnowledgeBase, Workspace
from app.db.session import get_session
from app.knowledge.retrieval import (
    RetrievalConflictError,
    RetrievalInputError,
    RetrievalNotFoundError,
    RetrievalStateError,
    search_knowledge_base,
)
from app.main import create_app
from app.providers.embeddings import (
    EmbeddingError,
    EmbeddingProfile,
    EmbeddingTimeoutError,
)


DIMENSIONS = 1024
DEFAULT_PROFILE = EmbeddingProfile("https://example.test/v1", "检索测试模型", DIMENSIONS)


def vector(x: float, y: float = 0.0) -> list[float]:
    return [x, y] + [0.0] * (DIMENSIONS - 2)


class QueryProvider:
    """记录问题与调用次数，返回预设向量或在调用时注入故障。"""

    def __init__(
        self,
        *,
        profile: EmbeddingProfile = DEFAULT_PROFILE,
        vectors: list[list[float]] | None = None,
        error: EmbeddingError | None = None,
        on_call: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self.profile = profile
        self.vectors = [vector(1.0)] if vectors is None else vectors
        self.error = error
        self.on_call = on_call
        self.calls = 0
        self.inputs: list[tuple[str, ...]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.inputs.append(tuple(texts))
        if self.on_call:
            self.on_call(texts)
        if self.error:
            raise self.error
        return [list(item) for item in self.vectors]


@dataclass
class RetrievalContext:
    client: TestClient
    engine: Engine
    workspace_id: UUID
    knowledge_base_id: UUID
    provider: QueryProvider

    def url(self, *, workspace_id: UUID | None = None, knowledge_base_id: UUID | None = None) -> str:
        return (
            f"/api/v1/workspaces/{workspace_id or self.workspace_id}"
            f"/knowledge-bases/{knowledge_base_id or self.knowledge_base_id}/search"
        )

    def knowledge_base(
        self, *, workspace_id: UUID | None = None, profile: EmbeddingProfile | None = DEFAULT_PROFILE
    ) -> UUID:
        identifier = uuid4()
        with Session(self.engine) as session, session.begin():
            session.add(
                KnowledgeBase(
                    id=identifier,
                    workspace_id=workspace_id or self.workspace_id,
                    name=f"知识库-{identifier}",
                    embedding_provider=profile.provider if profile else None,
                    embedding_model=profile.model if profile else None,
                    embedding_dimensions=profile.dimensions if profile else None,
                )
            )
        return identifier

    def workspace(self) -> UUID:
        identifier = uuid4()
        with Session(self.engine) as session, session.begin():
            session.add(Workspace(id=identifier, name=f"团队-{identifier}"))
        return identifier

    def set_profile(self, profile: EmbeddingProfile | None) -> None:
        with Session(self.engine) as session, session.begin():
            knowledge_base = session.get(KnowledgeBase, self.knowledge_base_id)
            assert knowledge_base is not None
            knowledge_base.embedding_provider = profile.provider if profile else None
            knowledge_base.embedding_model = profile.model if profile else None
            knowledge_base.embedding_dimensions = profile.dimensions if profile else None

    def document(
        self,
        vectors: Sequence[list[float] | None],
        *,
        knowledge_base_id: UUID | None = None,
        status: str = "ready",
        name: str = "产品说明.txt",
        ids: Sequence[UUID] | None = None,
    ) -> tuple[UUID, list[UUID]]:
        document_id = uuid4()
        chunk_ids = list(ids) if ids is not None else [uuid4() for _ in vectors]
        assert len(chunk_ids) == len(vectors)
        with Session(self.engine) as session, session.begin():
            session.add(
                Document(
                    id=document_id,
                    knowledge_base_id=knowledge_base_id or self.knowledge_base_id,
                    original_filename=name,
                    media_type="text/plain",
                    storage_key=f"{document_id}.txt",
                    byte_size=100,
                    sha256="a" * 64,
                    status=status,
                    chunk_size=100,
                    chunk_overlap=0,
                    chunking_version="测试切块-v1",
                )
            )
            session.flush()
            offset = 0
            for index, (chunk_id, embedding) in enumerate(zip(chunk_ids, vectors, strict=True), 1):
                content = f"第{index}段：重启后恢复服务。"
                session.add(
                    Chunk(
                        id=chunk_id,
                        document_id=document_id,
                        chunk_index=index,
                        content=content,
                        start_char=offset,
                        end_char=offset + len(content),
                        embedding=embedding,
                    )
                )
                offset += len(content)
        return document_id, chunk_ids

    def search(self, query: str = "服务如何恢复？", *, provider: QueryProvider | None = None, **options):
        with Session(self.engine, expire_on_commit=False) as session:
            return search_knowledge_base(
                session, provider or self.provider,
                self.workspace_id, self.knowledge_base_id, query, **options,
            )


@pytest.fixture
def retrieval_context() -> Iterator[RetrievalContext]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置 EAAP_TEST_DATABASE_URL 后运行向量检索集成测试")
    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("测试数据库不能与 EAAP_DATABASE_URL 指向同一数据库")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("向量检索集成测试需要独立的 PostgreSQL 数据库")

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
        workspace_id, knowledge_base_id = uuid4(), uuid4()
        with Session(scoped_engine) as session, session.begin():
            session.add(Workspace(id=workspace_id, name="检索测试团队"))
            session.flush()
            session.add(
                KnowledgeBase(
                    id=knowledge_base_id,
                    workspace_id=workspace_id,
                    name="技术文档 A",
                    embedding_provider=DEFAULT_PROFILE.provider,
                    embedding_model=DEFAULT_PROFILE.model,
                    embedding_dimensions=DIMENSIONS,
                )
            )

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        provider = QueryProvider()
        app = create_app()
        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[document_api.get_embedding_provider] = lambda: provider
        with TestClient(app) as client:
            yield RetrievalContext(client, scoped_engine, workspace_id, knowledge_base_id, provider)
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_knowledge_base_filter_precedes_limit_and_returns_traceable_sorted_hits(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    candidates = [vector(0.9, 0.1), vector(0.8, 0.2), vector(0.7, 0.3),
                  vector(0.6, 0.4), vector(0.5, 0.5), vector(0.4, 0.6)]
    document_id, chunk_ids = context.document(candidates, name="A-恢复流程.txt")
    other_base_id = context.knowledge_base()
    context.document([vector(1.0)] * 5, knowledge_base_id=other_base_id, name="B-干扰内容.txt")
    other_workspace_id = context.workspace()
    other_workspace_base = context.knowledge_base(workspace_id=other_workspace_id)
    context.document([vector(1.0)], knowledge_base_id=other_workspace_base)

    result = context.search(top_k=5)
    assert result.metric == "cosine_distance"
    assert result.model == DEFAULT_PROFILE.model and result.dimensions == DIMENSIONS
    assert [hit.chunk_id for hit in result.results] == chunk_ids[:5]
    assert len(result.results) == 5
    assert [hit.distance for hit in result.results] == sorted(hit.distance for hit in result.results)
    for index, hit in enumerate(result.results):
        assert hit.document_id == document_id
        assert hit.document_name == "A-恢复流程.txt"
        assert hit.chunk_index == index + 1
        assert hit.content == f"第{index + 1}段：重启后恢复服务。"
        assert hit.end_char - hit.start_char == len(hit.content)
        assert 0.0 <= hit.distance <= 2.0
        x, y = candidates[index][:2]
        assert hit.distance == pytest.approx(1.0 - x / math.hypot(x, y), abs=1e-6)
    assert context.provider.calls == 1
    assert context.provider.inputs == [("服务如何恢复？",)]


def test_equal_distance_uses_chunk_uuid_as_deterministic_tiebreaker(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    first_id, second_id = UUID(int=1), UUID(int=2)
    context.document([vector(1.0), vector(1.0)], ids=[second_id, first_id])
    result = context.search(top_k=1)
    assert [hit.chunk_id for hit in result.results] == [first_id]


def test_nonready_documents_and_missing_vectors_do_not_enter_candidates(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    _, allowed_ids = context.document([vector(0.5, 0.5), None])
    for status in ("uploaded", "processing", "parsed", "failed"):
        context.document([vector(1.0)], status=status)
    result = context.search(top_k=20)
    assert [hit.chunk_id for hit in result.results] == [allowed_ids[0]]


def test_opposite_direction_has_cosine_distance_two(retrieval_context: RetrievalContext) -> None:
    context = retrieval_context
    context.document([vector(-1.0)])
    assert context.search().results[0].distance == pytest.approx(2.0)


@pytest.mark.parametrize("profile_present", [False, True])
def test_empty_knowledge_base_returns_profile_if_known_without_model_call(
    retrieval_context: RetrievalContext, profile_present: bool,
) -> None:
    context = retrieval_context
    if not profile_present:
        context.set_profile(None)
    response = context.client.post(context.url(), json={"query": "如何恢复服务？"})
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == "cosine_distance" and body["results"] == []
    assert body["model"] == (DEFAULT_PROFILE.model if profile_present else None)
    assert body["dimensions"] == (DIMENSIONS if profile_present else None)
    assert context.provider.calls == 0


def test_known_profile_without_eligible_vectors_returns_empty_without_model_call(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    context.document([None])
    context.document([vector(1.0)], status="parsed")
    result = context.search()
    assert result.results == [] and result.model == DEFAULT_PROFILE.model
    assert context.provider.calls == 0


@pytest.mark.parametrize("scope", ["workspace", "knowledge_base"])
def test_wrong_parent_scope_returns_404_before_model_call(
    retrieval_context: RetrievalContext, scope: str,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    wrong_workspace = context.workspace()
    wrong_base = context.knowledge_base(workspace_id=wrong_workspace)
    url = context.url(workspace_id=wrong_workspace) if scope == "workspace" else context.url(knowledge_base_id=wrong_base)
    response = context.client.post(url, json={"query": "服务恢复"})
    assert response.status_code == 404
    assert context.provider.calls == 0
    with Session(context.engine) as session, pytest.raises(RetrievalNotFoundError):
        search_knowledge_base(session, context.provider, context.workspace_id, uuid4(), "服务恢复")


@pytest.mark.parametrize("has_candidates", [False, True])
@pytest.mark.parametrize(
    "profile",
    [
        EmbeddingProfile("https://different.example.test/v1", DEFAULT_PROFILE.model, DIMENSIONS),
        EmbeddingProfile(DEFAULT_PROFILE.provider, "另一测试模型", DIMENSIONS),
        EmbeddingProfile(DEFAULT_PROFILE.provider, DEFAULT_PROFILE.model, 768),
    ],
)
def test_profile_conflict_is_rejected_before_model_call_even_without_candidates(
    retrieval_context: RetrievalContext, has_candidates: bool, profile: EmbeddingProfile,
) -> None:
    context = retrieval_context
    if has_candidates:
        context.document([vector(1.0)])
    provider = QueryProvider(profile=profile)
    with pytest.raises(RetrievalConflictError):
        context.search(provider=provider)
    assert provider.calls == 0
    context.provider.profile = profile
    response = context.client.post(context.url(), json={"query": "恢复服务"})
    assert response.status_code == 409
    assert context.provider.calls == 0


def test_eligible_vectors_without_saved_model_profile_are_invalid_database_state(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    context.set_profile(None)
    with pytest.raises(RetrievalStateError):
        context.search()
    assert context.provider.calls == 0
    assert context.client.post(context.url(), json={"query": "恢复服务"}).status_code == 500


def test_model_call_has_no_open_database_transaction_and_receives_trimmed_query(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    with Session(context.engine, expire_on_commit=False) as session:
        def inspect(texts: Sequence[str]) -> None:
            assert not session.in_transaction()
            assert tuple(texts) == ("恢复服务",)

        provider = QueryProvider(on_call=inspect)
        result = search_knowledge_base(
            session, provider, context.workspace_id, context.knowledge_base_id, "  恢复服务\n"
        )
    assert len(result.results) == 1 and provider.calls == 1


@pytest.mark.parametrize("change", ["provider", "model", "profile_removed"])
def test_model_profile_changed_during_query_rejects_stale_query_vector(
    retrieval_context: RetrievalContext, change: str,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    changed = {
        "provider": EmbeddingProfile("https://changed.example.test/v1", DEFAULT_PROFILE.model, DIMENSIONS),
        "model": EmbeddingProfile(DEFAULT_PROFILE.provider, "已更新模型", DIMENSIONS),
        "profile_removed": None,
    }[change]
    provider = QueryProvider(on_call=lambda texts: context.set_profile(changed))
    with pytest.raises(RetrievalConflictError):
        context.search(provider=provider)
    assert provider.calls == 1


def test_knowledge_base_moved_during_query_is_rechecked_after_model_call(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    other_workspace_id = context.workspace()

    def move(texts: Sequence[str]) -> None:
        with Session(context.engine) as session, session.begin():
            knowledge_base = session.get(KnowledgeBase, context.knowledge_base_id)
            assert knowledge_base is not None
            knowledge_base.workspace_id = other_workspace_id

    provider = QueryProvider(on_call=move)
    with pytest.raises(RetrievalNotFoundError):
        context.search(provider=provider)
    assert provider.calls == 1


@pytest.mark.parametrize(
    "vectors",
    [[], [vector(1.0), vector(1.0)], [[1.0] * (DIMENSIONS - 1)],
     [[0.0] * DIMENSIONS], [[float("nan")] + [0.0] * (DIMENSIONS - 1)]],
    ids=["missing", "too_many", "wrong_dimensions", "zero", "nonfinite"],
)
def test_invalid_query_embedding_returns_safe_502(
    retrieval_context: RetrievalContext, vectors: list[list[float]],
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    context.provider.vectors = vectors
    response = context.client.post(context.url(), json={"query": "恢复服务"})
    assert response.status_code == 502
    assert context.provider.calls == 1
    assert DEFAULT_PROFILE.provider not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(EmbeddingError("内部密钥-禁止泄漏"), 502),
     (EmbeddingTimeoutError("内部密钥-禁止泄漏"), 504)],
)
def test_model_failures_return_safe_status_and_leave_database_unchanged(
    retrieval_context: RetrievalContext, error: EmbeddingError, expected_status: int,
) -> None:
    context = retrieval_context
    document_id, chunk_ids = context.document([vector(1.0)])
    context.provider.error = error
    response = context.client.post(context.url(), json={"query": "恢复服务"})
    assert response.status_code == expected_status
    assert "内部密钥" not in response.text and DEFAULT_PROFILE.provider not in response.text
    with Session(context.engine) as session:
        document = session.get(Document, document_id)
        chunk = session.get(Chunk, chunk_ids[0])
        assert document is not None and document.status == "ready"
        assert chunk is not None and list(chunk.embedding) == pytest.approx(vector(1.0))


def test_zero_stored_vector_reports_invalid_distance_as_database_state_error(
    retrieval_context: RetrievalContext,
) -> None:
    context = retrieval_context
    context.document([[0.0] * DIMENSIONS])
    with pytest.raises(RetrievalStateError):
        context.search()
    response = context.client.post(context.url(), json={"query": "恢复服务"})
    assert response.status_code == 500


def test_api_returns_default_five_hits_and_trimmed_query(retrieval_context: RetrievalContext) -> None:
    context = retrieval_context
    document_id, chunk_ids = context.document([vector(0.9, float(index)) for index in range(1, 7)])
    response = context.client.post(context.url(), json={"query": "  如何恢复服务？\n"})
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == "cosine_distance"
    assert body["model"] == DEFAULT_PROFILE.model and body["dimensions"] == DIMENSIONS
    assert len(body["results"]) == 5
    assert [item["chunk_id"] for item in body["results"]] == [str(value) for value in chunk_ids[:5]]
    first = body["results"][0]
    assert first["document_id"] == str(document_id)
    assert first["document_name"] == "产品说明.txt"
    assert first["chunk_index"] == 1 and first["start_char"] == 0
    assert first["end_char"] == len(first["content"])
    assert isinstance(first["distance"], float)
    assert "embedding" not in first
    assert context.provider.inputs == [("如何恢复服务？",)]


@pytest.mark.parametrize(
    "payload",
    [{}, {"query": ""}, {"query": " \n\t"}, {"query": "问" * 2001},
     {"query": 123}, {"query": None}, {"query": "问题", "top_k": 0},
     {"query": "问题", "top_k": 21}, {"query": "问题", "top_k": True},
     {"query": "问题", "top_k": 1.0}, {"query": "问题", "top_k": "5"},
     {"query": "问题", "unknown": "不允许额外字段"}],
)
def test_api_strict_input_validation_happens_before_model_call(
    retrieval_context: RetrievalContext, payload: dict,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    response = context.client.post(context.url(), json=payload)
    assert response.status_code == 422
    assert context.provider.calls == 0


@pytest.mark.parametrize("top_k", [1, 20])
def test_api_accepts_top_k_boundaries_and_trimmed_maximum_query(
    retrieval_context: RetrievalContext, top_k: int,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)] * 21)
    response = context.client.post(context.url(), json={"query": "  " + "问" * 2000 + "  ", "top_k": top_k})
    assert response.status_code == 200
    assert len(response.json()["results"]) == top_k
    assert context.provider.inputs == [("问" * 2000,)]


@pytest.mark.parametrize("query,top_k", [(" ", 5), ("问" * 2001, 5), ("问题", False), ("问题", 21)])
def test_service_rejects_invalid_input_before_model_call(
    retrieval_context: RetrievalContext, query: str, top_k: int,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    with pytest.raises(RetrievalInputError):
        context.search(query, top_k=top_k)
    assert context.provider.calls == 0


def test_api_missing_model_configuration_returns_503(
    retrieval_context: RetrievalContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = retrieval_context
    context.document([vector(1.0)])
    settings = Settings(_env_file=None, embedding_base_url=None, embedding_api_key=None)
    monkeypatch.setattr(document_api, "Settings", lambda: settings)
    del context.client.app.dependency_overrides[document_api.get_embedding_provider]
    response = context.client.post(context.url(), json={"query": "恢复服务"})
    assert response.status_code == 503
    assert context.provider.calls == 0
