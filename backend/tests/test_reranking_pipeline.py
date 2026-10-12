"""真实数据库验证扩大候选、可见降级、原文引用和事务边界；模型均为替身。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.api import answers as answer_api
from app.api import reranking as rerank_api
from app.knowledge.answers import answer_knowledge_base
from app.knowledge.search_pipeline import search_with_reranking
from app.providers.generation import GeneratedAnswer
from app.providers.reranking import (
    RerankConfigurationError, RerankError, RerankInputError, RerankOrder,
    RerankResult, RerankTimeoutError,
)
from test_hybrid_retrieval import set_contents
from test_retrieval import RetrievalContext, retrieval_context, vector


class ControlledRerank:
    def __init__(self, order=None, error=None, inspect=None):
        self.order, self.error, self.inspect = order, error, inspect
        self.calls = []

    def rerank(self, question, candidates):
        self.calls.append((question, candidates))
        if self.inspect:
            self.inspect(candidates)
        if self.error:
            raise self.error
        return RerankResult(RerankOrder(ordered_ids=self.order or list(range(len(candidates), 0, -1))), "受控模型")


def install(context, provider):
    context.client.app.dependency_overrides[rerank_api.get_rerank_provider] = lambda: provider


def run(context, provider, **options):
    with Session(context.engine) as session:
        return search_with_reranking(session, context.provider, context.workspace_id, context.knowledge_base_id,
                                    "如何提交退款？", rerank_provider=provider, **options)


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_candidate_expansion_brings_sixth_hit_into_final_five(retrieval_context, strategy):
    context = retrieval_context
    _, ids = context.document([vector(1, i / 10) for i in range(6)], ids=[UUID(int=i) for i in range(1, 7)])
    # 各块的匹配词数量与长度相同，保持混合基线第六名；模型替身只验证排列。
    set_contents(context, ids, ["退款甲乙丙丁戊己庚辛壬癸。"] * 5 + ["退款需在订单详情页中申请。"])
    baseline = context.search("如何提交退款？", strategy=strategy)
    assert ids[-1] not in [hit.chunk_id for hit in baseline.results]
    provider = ControlledRerank(order=[6, 1, 2, 3, 4, 5])
    result = run(context, provider, strategy=strategy, rerank=True, candidate_k=6)
    assert len(provider.calls) == 1 and len(provider.calls[0][1]) == 6
    assert result.results[0].chunk_id == ids[-1]
    assert result.results[0].retrieval_rank == 6 and result.results[0].rerank_rank == 1
    assert result.reranking.status == "applied" and result.reranking.candidate_count == 6
    assert result.strategy == strategy and result.reranking.response_model == "受控模型"


def test_default_off_never_reads_rerank_configuration(retrieval_context):
    context = retrieval_context
    context.document([vector(1)] * 6)
    provider = ControlledRerank(error=RerankConfigurationError("未配置"))
    result = run(context, provider)
    assert len(result.results) == 5 and not provider.calls and result.reranking is None


@pytest.mark.parametrize("error,reason", [
    (RerankTimeoutError("模拟超时"), "timeout"), (RerankError("模拟故障"), "service_error"),
])
def test_runtime_failure_keeps_baseline_order_and_marks_degradation(retrieval_context, error, reason, caplog):
    context = retrieval_context
    _, ids = context.document([vector(1, i / 10) for i in range(6)], ids=[UUID(int=i) for i in range(1, 7)])
    provider = ControlledRerank(error=error)
    install(context, provider)
    response = context.client.post(context.url(), json={"query": "退款？", "rerank": True, "candidate_k": 6})
    assert response.status_code == 200
    body = response.json()
    assert [hit["chunk_id"] for hit in body["results"]] == [str(item) for item in ids[:5]]
    assert body["reranking"]["status"] == "degraded" and body["reranking"]["reason"] == reason
    assert all(hit["rerank_rank"] is None for hit in body["results"])
    assert len(provider.calls) == 1 and "候选重排降级" in caplog.text


@pytest.mark.parametrize("order", [[1, 1], [2], [2, 3]])
def test_business_boundary_rejects_invalid_order_from_any_provider(retrieval_context, order):
    context = retrieval_context
    context.document([vector(1), vector(0, 1)])
    result = run(context, ControlledRerank(order=order), rerank=True)
    assert result.reranking.status == "degraded" and result.reranking.reason == "invalid_output"
    assert [hit.retrieval_rank for hit in result.results] == [1, 2]


def test_empty_library_skips_both_models_and_configuration(retrieval_context):
    context = retrieval_context
    provider = ControlledRerank(error=RerankConfigurationError("未配置"))
    install(context, provider)
    response = context.client.post(context.url(), json={"query": "退款？", "rerank": True})
    assert response.status_code == 200 and not response.json()["results"]
    assert response.json()["reranking"]["status"] == "skipped_empty"
    assert not provider.calls and context.provider.calls == 0


def test_reranker_receives_only_scoped_snapshot_outside_database_transaction(retrieval_context):
    context = retrieval_context
    _, ids = context.document([vector(1)], name="当前资料.txt")
    set_contents(context, ids, ["本库原文。"])
    context.document([vector(1)], knowledge_base_id=context.knowledge_base())
    context.document([vector(1)], status="parsed")
    with Session(context.engine) as session:
        def inspect(candidates):
            assert not session.in_transaction()
            assert [item.content for item in candidates] == ["本库原文。"]
            # 模型等待时库中发生后续新增，本次仍只对已召回的快照排序。
            context.document([vector(1)])
        result = search_with_reranking(session, context.provider, context.workspace_id, context.knowledge_base_id,
                                      "问题", rerank=True, rerank_provider=ControlledRerank(inspect=inspect))
    assert len(result.results) == 1 and result.results[0].chunk_id == ids[0]


def test_missing_library_is_rejected_before_reranking(retrieval_context):
    context = retrieval_context
    provider = ControlledRerank()
    install(context, provider)
    response = context.client.post(context.url(knowledge_base_id=uuid4()), json={"query": "问题", "rerank": True})
    assert response.status_code == 404 and not provider.calls


@pytest.mark.parametrize("options", [
    {"rerank": "true"}, {"rerank": 1}, {"rerank": None},
    {"rerank": True, "candidate_k": 4}, {"rerank": True, "candidate_k": 21},
    {"rerank": True, "candidate_k": True}, {"rerank": True, "candidate_k": "20"},
])
def test_invalid_options_rejected_by_both_apis_before_models(retrieval_context, options):
    context = retrieval_context
    provider = ControlledRerank()
    install(context, provider)
    for endpoint, field in [("/search", "query"), ("/answers", "question")]:
        response = context.client.post(context.url().removesuffix("/search") + endpoint, json={field: "问题", **options})
        assert response.status_code == 422
    assert not provider.calls and context.provider.calls == 0


def test_input_budget_rejects_whole_pool_without_rerank_or_generation(retrieval_context):
    context = retrieval_context
    _, ids = context.document([vector(1), vector(0, 1)])
    set_contents(context, ids, ["字" * 10000, "字" * 10001])
    provider = ControlledRerank()
    install(context, provider)
    response = context.client.post(context.url(), json={"query": "问题", "rerank": True})
    assert response.status_code == 422 and not provider.calls


def test_configuration_error_is_503_instead_of_degraded_success(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    provider = ControlledRerank(error=RerankConfigurationError("未配置"))
    install(context, provider)
    response = context.client.post(context.url(), json={"query": "问题", "rerank": True})
    assert response.status_code == 503 and len(provider.calls) == 1


@pytest.mark.parametrize("degraded", [False, True])
def test_answer_citations_follow_actual_order_and_surface_degradation(retrieval_context, degraded):
    context = retrieval_context
    document_id, ids = context.document([vector(1), vector(0, 1)])
    original = ["退款审核通过后到账。", "在订单详情页提交退款申请。"]
    set_contents(context, ids, original)
    provider = ControlledRerank(order=[2, 1], error=RerankTimeoutError("超时") if degraded else None)
    install(context, provider)
    chosen = 0 if degraded else 1
    class ControlledGeneration:
        def generate_answer(self, question, sources):
            assert len(sources) == 1 and sources[0].content == original[chosen]
            return GeneratedAnswer(answer=original[chosen], abstained=False, citations=[{"source_id": 1, "quote": original[chosen]}])
    context.client.app.dependency_overrides[answer_api.get_generation_provider] = lambda: ControlledGeneration()
    response = context.client.post(context.url().removesuffix("/search") + "/answers",
                                   json={"question": "如何提交退款？", "rerank": True, "top_k": 1, "candidate_k": 2})
    assert response.status_code == 200
    body = response.json()
    citation = body["citations"][0]
    assert citation["chunk_id"] == str(ids[chosen]) and citation["document_id"] == str(document_id)
    assert citation["start_char"] == (len(original[0]) if chosen else 0)
    assert citation["end_char"] - citation["start_char"] == len(original[chosen])
    assert body["reranking"]["status"] == ("degraded" if degraded else "applied")


def test_successful_sort_does_not_override_generation_abstention(retrieval_context):
    context = retrieval_context
    context.document([vector(1)])
    class AbstainingGeneration:
        def generate_answer(self, question, sources):
            return GeneratedAnswer(answer="", abstained=True, citations=[])
    with Session(context.engine) as session:
        result = answer_knowledge_base(session, context.provider, AbstainingGeneration(), context.workspace_id,
                                      context.knowledge_base_id, "缺少的知识？", rerank=True, rerank_provider=ControlledRerank())
    assert result.abstained and not result.citations and result.reranking.status == "applied"


def test_direct_business_input_validation_precedes_embedding(retrieval_context):
    with pytest.raises(RerankInputError):
        run(retrieval_context, ControlledRerank(), rerank=True, top_k=5, candidate_k=4)
    assert retrieval_context.provider.calls == 0
