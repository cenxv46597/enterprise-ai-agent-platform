"""证据回答的本地故障测试；受控模型只验证流程与引用，不代表语义质量。"""

import os
import json
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import answers as answer_api
from app.api import documents as document_api
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Chunk, Document, KnowledgeBase, Workspace
from app.db.session import get_session
from app.knowledge.answers import (
    ABSTENTION_MESSAGE,
    AnswerContextLimitError,
    answer_knowledge_base,
)
from app.main import create_app
from auth_helpers import create_authenticated_user
from app.providers.embeddings import EmbeddingError, EmbeddingProfile, EmbeddingTimeoutError
from app.providers.generation import (
    GeneratedAnswer,
    GeneratedCitation,
    GenerationError,
    GenerationSource,
    GenerationTimeoutError,
)


DIMENSIONS = 1024
PROFILE = EmbeddingProfile("https://answer-vectors.example.test/v1", "回答测试向量", DIMENSIONS)


def vector(x: float, y: float = 0.0) -> list[float]:
    return [x, y] + [0.0] * (DIMENSIONS - 2)


class AnswerEmbeddingProvider:
    def __init__(self) -> None:
        self.profile = PROFILE
        self.calls: list[tuple[str, ...]] = []
        self.error: EmbeddingError | None = None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        if self.error:
            raise self.error
        return [vector(1.0)]


class AnswerGenerationProvider:
    """记录传入证据，可以返回受控草稿或注入服务故障。"""

    def __init__(
        self,
        *,
        draft: object | None = None,
        error: GenerationError | None = None,
        on_call: Callable[[str, Sequence[GenerationSource]], None] | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.on_call = on_call
        self.calls: list[tuple[str, tuple[GenerationSource, ...]]] = []

    def generate_answer(self, question: str, sources: Sequence[GenerationSource]) -> GeneratedAnswer:
        self.calls.append((question, tuple(sources)))
        if self.on_call:
            self.on_call(question, sources)
        if self.error:
            raise self.error
        if self.draft is not None:
            # 故意允许测试返回不合约的对象，验证业务层会再次检查。
            return self.draft  # type: ignore[return-value]
        return GeneratedAnswer(
            answer="根据检索到的资料：" + sources[0].content,
            abstained=False,
            citations=[GeneratedCitation(source_id=1, quote=sources[0].content)],
        )


@dataclass
class AnswerContext:
    client: TestClient
    engine: Engine
    workspace_id: UUID
    knowledge_base_id: UUID
    embedding: AnswerEmbeddingProvider
    generation: AnswerGenerationProvider

    def url(self, *, workspace_id: UUID | None = None, knowledge_base_id: UUID | None = None) -> str:
        return (
            f"/api/v1/workspaces/{workspace_id or self.workspace_id}"
            f"/knowledge-bases/{knowledge_base_id or self.knowledge_base_id}/answers"
        )

    def knowledge_base(self, *, workspace_id: UUID | None = None) -> UUID:
        identifier = uuid4()
        with Session(self.engine) as session, session.begin():
            session.add(
                KnowledgeBase(
                    id=identifier,
                    workspace_id=workspace_id or self.workspace_id,
                    name=f"回答知识库-{identifier}",
                    embedding_provider=PROFILE.provider,
                    embedding_model=PROFILE.model,
                    embedding_dimensions=DIMENSIONS,
                )
            )
        return identifier

    def workspace(self) -> UUID:
        identifier = uuid4()
        with Session(self.engine) as session, session.begin():
            session.add(Workspace(id=identifier, name=f"回答团队-{identifier}"))
        return identifier

    def document(
        self,
        contents: Sequence[str],
        *,
        vectors: Sequence[list[float] | None] | None = None,
        knowledge_base_id: UUID | None = None,
        name: str = "退款说明.txt",
        status: str = "ready",
        start_char: int = 11,
    ) -> tuple[UUID, list[UUID]]:
        document_id = uuid4()
        chunk_ids = [uuid4() for _ in contents]
        embeddings = list(vectors) if vectors is not None else [vector(1.0)] * len(contents)
        assert len(contents) == len(embeddings)
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
                    chunk_size=20000,
                    chunk_overlap=0,
                    chunking_version="回答测试-v1",
                )
            )
            session.flush()
            offset = start_char
            for index, (content, embedding, chunk_id) in enumerate(
                zip(contents, embeddings, chunk_ids, strict=True), 1
            ):
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
                offset += len(content) + 1
        return document_id, chunk_ids

    def answer(self, question: str = "如何提交退款申请？", **options):
        with Session(self.engine, expire_on_commit=False) as session:
            return answer_knowledge_base(
                session, self.embedding, self.generation,
                self.workspace_id, self.knowledge_base_id, question, **options,
            )


@pytest.fixture
def answer_context() -> Iterator[AnswerContext]:
    database_url = os.environ.get("EAAP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("设置 EAAP_TEST_DATABASE_URL 后运行证据回答集成测试")
    app_url = Settings().database_url
    if app_url and make_url(database_url) == make_url(app_url):
        pytest.fail("测试数据库不能与 EAAP_DATABASE_URL 指向同一数据库")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("证据回答集成测试需要独立的 PostgreSQL 数据库")

    engine = create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=15000 -c lock_timeout=10000",
        },
    )
    schema = f"eaap_answer_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped_engine)
        workspace_id, knowledge_base_id = uuid4(), uuid4()
        with Session(scoped_engine) as session, session.begin():
            session.add(Workspace(id=workspace_id, name="证据回答测试团队"))
            session.flush()
            session.add(
                KnowledgeBase(
                    id=knowledge_base_id,
                    workspace_id=workspace_id,
                    name="目标技术文档",
                    embedding_provider=PROFILE.provider,
                    embedding_model=PROFILE.model,
                    embedding_dimensions=DIMENSIONS,
                )
            )

        def test_session() -> Iterator[Session]:
            with Session(scoped_engine, expire_on_commit=False) as session:
                yield session

        embedding, generation = AnswerEmbeddingProvider(), AnswerGenerationProvider()
        app = create_app()
        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[document_api.get_embedding_provider] = lambda: embedding
        app.dependency_overrides[answer_api.get_generation_provider] = lambda: generation
        with TestClient(app) as client:
            create_authenticated_user(client, scoped_engine, workspace_ids=[workspace_id])
            yield AnswerContext(client, scoped_engine, workspace_id, knowledge_base_id, embedding, generation)
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_answer_uses_only_target_knowledge_base_and_builds_exact_quote_span(
    answer_context: AnswerContext,
) -> None:
    context = answer_context
    content = "退款步骤：登录控制台，在订单详情中提交退款申请。"
    quote = "在订单详情中提交退款申请"
    document_id, chunk_ids = context.document([content], vectors=[vector(0.8, 0.2)], name="A-退款流程.txt")
    other_base = context.knowledge_base()
    context.document(["B 的服务器部署资料"] * 5, knowledge_base_id=other_base)
    other_workspace = context.workspace()
    context.document(
        ["另一团队的退款记录"], knowledge_base_id=context.knowledge_base(workspace_id=other_workspace)
    )
    context.generation.draft = GeneratedAnswer(
        answer="登录控制台，打开订单详情并提交退款申请。",
        abstained=False,
        citations=[GeneratedCitation(source_id=1, quote=quote)],
    )

    response = context.client.post(context.url(), json={"question": "  如何提交退款申请？\n"})
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    assert body["answer"] == "登录控制台，打开订单详情并提交退款申请。"
    assert body["citations"] == [{
        "chunk_id": str(chunk_ids[0]),
        "document_id": str(document_id),
        "document_name": "A-退款流程.txt",
        "chunk_index": 1,
        "quote": quote,
        "start_char": 11 + content.index(quote),
        "end_char": 11 + content.index(quote) + len(quote),
    }]
    assert context.embedding.calls == [("如何提交退款申请？",)]
    question, sources = context.generation.calls[0]
    assert question == "如何提交退款申请？"
    assert sources == (GenerationSource(source_id=1, content=content),)
    assert not any(value in response.text for value in ("storage_key", "embedding", PROFILE.provider))


def test_empty_knowledge_base_abstains_without_calling_either_model(answer_context: AnswerContext) -> None:
    context = answer_context
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == 200
    assert response.json() == {"answer": ABSTENTION_MESSAGE, "abstained": True, "citations": []}
    assert context.embedding.calls == [] and context.generation.calls == []


def test_related_but_insufficient_sources_follow_controlled_abstention_decision(
    answer_context: AnswerContext,
) -> None:
    context = answer_context
    content = "退款审核通过后，款项将在三个工作日内到账。"
    context.document([content])
    # 这里由受控模型给出弃答，只验证处理流程，不验证真实模型能否判断证据充分性。
    context.generation.draft = GeneratedAnswer(answer="", abstained=True, citations=[])
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == 200
    assert response.json() == {"answer": ABSTENTION_MESSAGE, "abstained": True, "citations": []}
    assert len(context.embedding.calls) == len(context.generation.calls) == 1
    assert context.generation.calls[0][1][0].content == content


def test_generation_call_is_outside_database_transaction_and_is_read_only(answer_context: AnswerContext) -> None:
    context = answer_context
    document_id, chunk_ids = context.document(["登录控制台提交退款申请。"])
    with Session(context.engine, expire_on_commit=False) as session:
        def inspect(question: str, sources: Sequence[GenerationSource]) -> None:
            assert not session.in_transaction()
            assert question == "如何提交退款申请？" and len(sources) == 1

        provider = AnswerGenerationProvider(on_call=inspect)
        result = answer_knowledge_base(
            session, context.embedding, provider, context.workspace_id,
            context.knowledge_base_id, "如何提交退款申请？",
        )
        assert not session.in_transaction()
    assert not result.abstained and len(provider.calls) == 1
    with Session(context.engine) as session:
        document, chunk = session.get(Document, document_id), session.get(Chunk, chunk_ids[0])
        assert document is not None and document.status == "ready"
        assert chunk is not None and chunk.content == "登录控制台提交退款申请。"
        assert list(chunk.embedding) == pytest.approx(vector(1.0))


def test_context_budget_skips_whole_long_chunk_and_renumbers_later_sources(answer_context: AnswerContext) -> None:
    context = answer_context
    contents = ["长" * 20, "退款步骤", "联系客服"]
    document_id, chunk_ids = context.document(
        contents, vectors=[vector(1.0), vector(0.9, 0.1), vector(0.8, 0.2)], start_char=0
    )
    context.generation.draft = GeneratedAnswer(
        answer="退款步骤中提到联系客服。", abstained=False,
        citations=[GeneratedCitation(source_id=2, quote="联系客服")],
    )
    result = context.answer(top_k=3, max_context_characters=8)
    assert context.generation.calls[0][1] == (
        GenerationSource(source_id=1, content="退款步骤"),
        GenerationSource(source_id=2, content="联系客服"),
    )
    assert result.citations[0].chunk_id == chunk_ids[2]
    assert result.citations[0].document_id == document_id
    assert result.citations[0].start_char == 26
    assert result.citations[0].end_char == 30


def test_generation_snapshot_records_budgeted_sources_not_all_retrieval_candidates(answer_context: AnswerContext):
    from app.providers.compatible_generation import OpenAICompatibleGenerationProvider
    from app.providers.generation_snapshot import capture_generation_request

    context = answer_context
    contents = ["长" * 20, "退款步骤", "联系客服"]
    _, chunk_ids = context.document(contents, vectors=[vector(1.0), vector(0.9, 0.1), vector(0.8, 0.2)], start_char=0)
    snapshots = []
    question = "如何提交退款申请？"

    def observe(body):
        snapshots.append(capture_generation_request(body, "\n".join(contents), question))

    def handler(request):
        assert len(snapshots) == 1
        assert json.loads(request.content) == snapshots[0]["request_body"]
        return httpx.Response(200, json={"model": "controlled-model", "choices": [{
            "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({
                "answer": "退款步骤中提到联系客服。", "abstained": False,
                "citations": [{"source_id": 2, "quote": "联系客服"}],
            })},
        }]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleGenerationProvider(
            "https://generation.example/v1", SecretStr("controlled-secret"), "controlled-model",
            client=client, request_observer=observe,
        )
        with Session(context.engine) as session:
            result = answer_knowledge_base(
                session, context.embedding, provider, context.workspace_id, context.knowledge_base_id,
                question, top_k=3, max_context_characters=8,
            )
    data = json.loads(snapshots[0]["request_body"]["messages"][1]["content"])
    assert data["sources"] == [{"source_id": 1, "content": "退款步骤"}, {"source_id": 2, "content": "联系客服"}]
    assert result.citations[0].chunk_id == chunk_ids[2]
    assert snapshots[0]["source_count"] == 2 and snapshots[0]["context_characters"] == 8


def test_all_chunks_over_context_budget_raise_without_generation(answer_context: AnswerContext) -> None:
    context = answer_context
    context.document(["这是一段不能完整放入预算的资料"])
    with pytest.raises(AnswerContextLimitError):
        context.answer(max_context_characters=2)
    assert len(context.embedding.calls) == 1 and context.generation.calls == []


def test_api_context_budget_failure_returns_422_before_generation(
    answer_context: AnswerContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = answer_context
    context.document(["这段资料无法完整放入两字符预算。"])
    settings = Settings(_env_file=None, generation_max_context_characters=2)
    monkeypatch.setattr(answer_api, "Settings", lambda: settings)
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == 422
    assert len(context.embedding.calls) == 1 and context.generation.calls == []


def test_duplicate_sentence_quote_points_to_an_existing_first_occurrence(answer_context: AnswerContext) -> None:
    context = answer_context
    content = "提交申请。处理完成后，可再次提交申请。"
    quote = "提交申请"
    context.document([content], start_char=17)
    context.generation.draft = GeneratedAnswer(
        answer="资料中提到提交申请。", abstained=False,
        citations=[GeneratedCitation(source_id=1, quote=quote)],
    )
    citation = context.answer().citations[0]
    assert citation.start_char == 17 and citation.end_char == 17 + len(quote)
    assert content[citation.start_char - 17:citation.end_char - 17] == quote


@pytest.mark.parametrize("problem", ["invented_source", "invented_quote", "duplicate_citation"])
def test_invalid_source_references_are_rejected_without_returning_draft(
    answer_context: AnswerContext, problem: str,
) -> None:
    context = answer_context
    quote = "提交申请"
    context.document(["在控制台提交申请。"])
    citation = GeneratedCitation(source_id=2 if problem == "invented_source" else 1,
                                 quote="捏造的原文" if problem == "invented_quote" else quote)
    citations = [citation, citation] if problem == "duplicate_citation" else [citation]
    context.generation.draft = GeneratedAnswer(answer="禁止返回的模型草稿", abstained=False, citations=citations)
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == 502
    assert "禁止返回" not in response.text and "捏造的原文" not in response.text
    assert len(context.generation.calls) == 1


@pytest.mark.parametrize(
    "draft",
    [
        GeneratedAnswer.model_construct(answer="未经验证的内容", abstained="false", citations=[]),
        GeneratedAnswer.model_construct(answer="未经验证的内容", abstained=True, citations=[]),
        GeneratedAnswer.model_construct(answer="未经验证的内容", abstained=False, citations=[]),
        GeneratedAnswer.model_construct(answer="未经验证的内容", abstained=False, citations=[
            GeneratedCitation.model_construct(source_id=True, quote="提交申请")
        ]),
        {"answer": "未经验证的内容", "abstained": False, "citations": [{"source_id": "1", "quote": "提交申请"}]},
        {"answer": "未经验证的内容", "abstained": False, "citations": [{"source_id": 1, "quote": "提交申请"}], "extra": "禁止字段"},
    ],
    ids=["nonboolean", "abstention_with_answer", "answer_without_citations", "boolean_source", "string_source", "extra"],
)
def test_service_revalidates_model_drafts_strictly(answer_context: AnswerContext, draft: object) -> None:
    context = answer_context
    context.document(["在控制台提交申请。"])
    context.generation.draft = draft
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == 502
    assert "未经验证" not in response.text and "Traceback" not in response.text


def test_invalid_draft_type_does_not_leak_text_through_warnings_or_errors(
    answer_context: AnswerContext, caplog: pytest.LogCaptureFixture,
) -> None:
    context = answer_context
    context.document(["在控制台提交申请。"])
    context.generation.draft = GeneratedAnswer.model_construct(
        answer={"text": "PRIVATE-DRAFT-TEXT"}, abstained=False, citations=[],
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(GenerationError) as captured:
            context.answer()
    assert recorded == []
    assert "PRIVATE-DRAFT-TEXT" not in str(captured.value)
    assert "PRIVATE-DRAFT-TEXT" not in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(GenerationError("private-generation-key"), 502), (GenerationTimeoutError("private-generation-key"), 504)],
)
def test_generation_faults_return_safe_status_and_preserve_records(
    answer_context: AnswerContext, error: GenerationError, expected_status: int,
) -> None:
    context = answer_context
    document_id, chunk_ids = context.document(["在控制台提交申请。"])
    context.generation.error = error
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == expected_status
    assert "private-generation-key" not in response.text and PROFILE.provider not in response.text
    with Session(context.engine) as session:
        document, chunk = session.get(Document, document_id), session.get(Chunk, chunk_ids[0])
        assert document is not None and document.status == "ready"
        assert chunk is not None and chunk.content == "在控制台提交申请。"


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(EmbeddingError("private-vector-key"), 502), (EmbeddingTimeoutError("private-vector-key"), 504)],
)
def test_vector_faults_stop_before_generation_and_preserve_safe_status(
    answer_context: AnswerContext, error: EmbeddingError, expected_status: int,
) -> None:
    context = answer_context
    context.document(["在控制台提交申请。"])
    context.embedding.error = error
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == expected_status
    assert "private-vector-key" not in response.text
    assert context.generation.calls == []


@pytest.mark.parametrize("scope", ["workspace", "knowledge_base"])
def test_wrong_scope_returns_404_without_either_model_call(answer_context: AnswerContext, scope: str) -> None:
    context = answer_context
    other_workspace = context.workspace()
    other_base = context.knowledge_base(workspace_id=other_workspace)
    url = context.url(workspace_id=other_workspace) if scope == "workspace" else context.url(knowledge_base_id=other_base)
    response = context.client.post(url, json={"question": "退款流程"})
    assert response.status_code == 404
    assert context.embedding.calls == [] and context.generation.calls == []


@pytest.mark.parametrize(
    "payload",
    [{}, {"question": ""}, {"question": " \n\t"}, {"question": "问" * 2001},
     {"question": 123}, {"question": None}, {"question": "问题", "top_k": 0},
     {"question": "问题", "top_k": 21}, {"question": "问题", "top_k": True},
     {"question": "问题", "top_k": 1.0}, {"question": "问题", "top_k": "5"},
     {"question": "问题", "unknown": "禁止额外字段"}],
)
def test_api_strict_input_validation_precedes_both_models(answer_context: AnswerContext, payload: dict) -> None:
    context = answer_context
    response = context.client.post(context.url(), json=payload)
    assert response.status_code == 422
    assert context.embedding.calls == [] and context.generation.calls == []


@pytest.mark.parametrize("top_k", [1, 20])
def test_api_accepts_boundaries_and_sends_no_more_than_requested_sources(
    answer_context: AnswerContext, top_k: int,
) -> None:
    context = answer_context
    context.document([f"第 {index} 条资料。" for index in range(21)])
    response = context.client.post(context.url(), json={"question": "  " + "问" * 2000 + "  ", "top_k": top_k})
    assert response.status_code == 200
    question, sources = context.generation.calls[0]
    assert question == "问" * 2000 and len(sources) == top_k
    assert [source.source_id for source in sources] == list(range(1, top_k + 1))


def test_api_uses_default_top_five_sources(answer_context: AnswerContext) -> None:
    context = answer_context
    context.document([f"第 {index} 条资料。" for index in range(6)])
    response = context.client.post(context.url(), json={"question": "资料说明"})
    assert response.status_code == 200
    assert len(context.generation.calls[0][1]) == 5


@pytest.mark.parametrize("has_sources", [False, True])
def test_missing_generation_configuration_only_fails_when_generation_is_needed(
    answer_context: AnswerContext, has_sources: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = answer_context
    if has_sources:
        context.document(["在控制台提交申请。"])
    settings = Settings(_env_file=None, generation_base_url=None, generation_api_key=None, generation_model=None)
    monkeypatch.setattr(answer_api, "Settings", lambda: settings)
    del context.client.app.dependency_overrides[answer_api.get_generation_provider]
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == (503 if has_sources else 200)
    if not has_sources:
        assert response.json() == {"answer": ABSTENTION_MESSAGE, "abstained": True, "citations": []}
    assert context.generation.calls == []


@pytest.mark.parametrize(
    "overrides",
    [{"generation_base_url": "http://generation.example.test/v1"},
     {"generation_api_key": SecretStr("  ")},
     {"generation_model": "  "}],
)
def test_invalid_generation_configuration_returns_safe_503(
    answer_context: AnswerContext, overrides: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = answer_context
    context.document(["在控制台提交申请。"])
    settings = Settings(_env_file=None, **{
        "generation_base_url": "https://generation.example.test/v1",
        "generation_api_key": SecretStr("private-generation-key"),
        "generation_model": "受控生成模型",
        **overrides,
    })
    monkeypatch.setattr(answer_api, "Settings", lambda: settings)
    del context.client.app.dependency_overrides[answer_api.get_generation_provider]
    response = context.client.post(context.url(), json={"question": "如何提交退款申请？"})
    assert response.status_code == 503
    assert "private-generation-key" not in response.text and "generation.example.test" not in response.text


def test_generation_settings_load_numeric_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EAAP_GENERATION_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("EAAP_GENERATION_TOTAL_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("EAAP_GENERATION_MAX_TOKENS", "2000")
    monkeypatch.setenv("EAAP_GENERATION_MAX_CONTEXT_CHARACTERS", "12000")
    settings = Settings(_env_file=None)
    assert settings.generation_timeout_seconds == 30
    assert settings.generation_total_timeout_seconds == 120
    assert settings.generation_max_tokens == 2000
    assert settings.generation_max_context_characters == 12000
