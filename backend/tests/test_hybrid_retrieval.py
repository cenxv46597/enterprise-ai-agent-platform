"""真实数据库验证两路独立召回、范围、预算及问答引用；模型使用替身。"""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.api import answers as answer_api
from app.db.models import Chunk
from app.knowledge import retrieval
from app.knowledge.retrieval import RetrievalCapacityError, RetrievalConflictError, RetrievalStateError
from app.providers.embeddings import EmbeddingTimeoutError
from app.providers.generation import GeneratedAnswer
from test_retrieval import QueryProvider, RetrievalContext, DEFAULT_PROFILE, retrieval_context, vector


def set_contents(context, identifiers, contents):
    with Session(context.engine) as session, session.begin():
        offset = 0
        for identifier, content in zip(identifiers, contents, strict=True):
            chunk = session.get(Chunk, identifier)
            chunk.content = content
            chunk.start_char, chunk.end_char = offset, offset + len(content)
            offset += len(content)


def test_exact_identifier_outside_vector_channel_is_recalled_and_explained(retrieval_context: RetrievalContext):
    context = retrieval_context
    _, identifiers = context.document(
        [vector(1, index / 100) for index in range(20)] + [vector(0, 1)],
        ids=[UUID(int=index) for index in range(1, 22)],
    )
    set_contents(context, identifiers, ["无关日志信息。"] * 20 + ["TOKEN_EXPIRED：更换访问令牌后进行健康检查。"])
    baseline = context.search("TOKEN_EXPIRED", top_k=5)
    hybrid = context.search("TOKEN_EXPIRED", top_k=5, strategy="hybrid")
    assert identifiers[-1] not in [hit.chunk_id for hit in baseline.results]
    assert hybrid.results[1].chunk_id == identifiers[-1]
    assert hybrid.results[1].vector_rank is None and hybrid.results[1].lexical_rank == 1
    assert hybrid.results[1].distance == pytest.approx(1)
    assert hybrid.metric == "reciprocal_rank_fusion" and hybrid.strategy == "hybrid"
    assert len(hybrid.results) == len({hit.chunk_id for hit in hybrid.results}) == 5
    assert context.provider.calls == 2  # 每次检索只请求一条问题向量。


def test_hybrid_filters_scope_and_state_before_counting_or_ranking(retrieval_context, monkeypatch):
    context = retrieval_context
    _, allowed = context.document([vector(0, 1)])
    set_contents(context, allowed, ["TOKEN_EXPIRED：本库有效资料。"])
    bases = [context.knowledge_base(), context.knowledge_base(workspace_id=context.workspace())]
    for base in bases:
        _, identifiers = context.document([vector(1)] * 3, knowledge_base_id=base)
        set_contents(context, identifiers, ["TOKEN_EXPIRED 干扰资料。"] * 3)
    _, parsed = context.document([vector(1)], status="parsed")
    set_contents(context, parsed, ["TOKEN_EXPIRED 未就绪资料。"])
    context.document([None])
    monkeypatch.setattr(retrieval, "MAX_HYBRID_CHUNKS", 1)
    response = context.client.post(context.url(), json={"query": "TOKEN_EXPIRED", "strategy": "hybrid"})
    assert response.status_code == 200
    assert [row["chunk_id"] for row in response.json()["results"]] == [str(allowed[0])]
    assert response.json()["results"][0]["vector_rank"] == response.json()["results"][0]["lexical_rank"] == 1


def test_hybrid_empty_library_skips_models_with_correct_metric(retrieval_context):
    context = retrieval_context
    response = context.client.post(context.url(), json={"query": "TOKEN_EXPIRED", "strategy": "hybrid"})
    assert response.status_code == 200 and response.json()["results"] == []
    assert response.json()["metric"] == "reciprocal_rank_fusion"
    assert context.provider.calls == 0


def test_capacity_rejects_before_paid_call_without_silent_sampling(retrieval_context, monkeypatch):
    context = retrieval_context
    context.document([vector(1)] * 3)
    monkeypatch.setattr(retrieval, "MAX_HYBRID_CHUNKS", 2)
    response = context.client.post(context.url(), json={"query": "恢复", "strategy": "hybrid"})
    assert response.status_code == 422 and context.provider.calls == 0
    assert context.search("恢复", strategy="vector").results


def test_character_capacity_counts_full_text_before_vector_call(retrieval_context, monkeypatch):
    context = retrieval_context
    _, identifiers = context.document([vector(1)])
    set_contents(context, identifiers, ["令牌" * 60])
    monkeypatch.setattr(retrieval, "MAX_HYBRID_CHARACTERS", 100)
    with pytest.raises(RetrievalCapacityError):
        context.search("令牌", strategy="hybrid")
    assert context.provider.calls == 0


def test_capacity_growth_during_vector_request_is_rechecked(retrieval_context, monkeypatch):
    context = retrieval_context
    context.document([vector(1)])
    monkeypatch.setattr(retrieval, "MAX_HYBRID_CHUNKS", 1)
    provider = QueryProvider(on_call=lambda texts: context.document([vector(1)]))
    with pytest.raises(RetrievalCapacityError):
        context.search("令牌", strategy="hybrid", provider=provider)
    assert provider.calls == 1


def test_hybrid_model_request_stays_outside_transaction_and_profile_is_rechecked(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    with Session(context.engine) as session:
        def inspect(texts):
            assert not session.in_transaction()
            context.set_profile(None)
        provider = QueryProvider(on_call=inspect)
        with pytest.raises(RetrievalConflictError):
            retrieval.search_knowledge_base(session, provider, context.workspace_id, context.knowledge_base_id, "令牌", strategy="hybrid")


def test_hybrid_detects_invalid_vector_and_propagates_timeout(retrieval_context):
    context = retrieval_context
    context.document([[0.0] * 1024])
    with pytest.raises(RetrievalStateError):
        context.search("令牌", strategy="hybrid")
    with pytest.raises(EmbeddingTimeoutError):
        context.search("令牌", strategy="hybrid", provider=QueryProvider(error=EmbeddingTimeoutError("模拟超时")))


@pytest.mark.parametrize("strategy", ["unknown", 1, True, None, []])
def test_invalid_strategy_is_rejected_by_both_apis_before_models(retrieval_context, strategy):
    context = retrieval_context
    context.document([vector(1)])
    search_response = context.client.post(context.url(), json={"query": "令牌", "strategy": strategy})
    answer_response = context.client.post(context.url().removesuffix("/search") + "/answers", json={"question": "令牌", "strategy": strategy})
    assert search_response.status_code == answer_response.status_code == 422
    assert context.provider.calls == 0


def test_answer_uses_fused_order_and_preserves_exact_source_positions(retrieval_context):
    context = retrieval_context
    document_id, identifiers = context.document([vector(1), vector(0, 1)])
    original = "返回 TOKEN_EXPIRED 时更换令牌，健康检查通过后撤销旧令牌。"
    set_contents(context, identifiers, ["无关日志资料。", original])
    seen = []
    class ControlledGeneration:
        def generate_answer(self, question, sources):
            seen.extend(sources)
            assert sources[0].content == original
            return GeneratedAnswer(answer=original, abstained=False, citations=[{"source_id": 1, "quote": original}])
    context.client.app.dependency_overrides[answer_api.get_generation_provider] = lambda: ControlledGeneration()
    response = context.client.post(context.url().removesuffix("/search") + "/answers", json={"question": "TOKEN_EXPIRED", "strategy": "hybrid"})
    assert response.status_code == 200
    citation = response.json()["citations"][0]
    assert citation["document_id"] == str(document_id) and citation["chunk_id"] == str(identifiers[1])
    assert citation["quote"] == original and citation["start_char"] == len("无关日志资料。")
    assert citation["end_char"] - citation["start_char"] == len(original)
    assert len(seen) == 2 and context.provider.calls == 1
