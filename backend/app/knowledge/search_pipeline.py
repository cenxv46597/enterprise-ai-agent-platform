"""组合已有召回与可选重排；在候选快照上排序，模型调用不持有事务。"""

from dataclasses import replace
import logging
from time import monotonic
from uuid import UUID

from sqlalchemy.orm import Session

from app.knowledge.retrieval import MAX_TOP_K, SearchResult, SearchStrategy, search_knowledge_base
from app.providers.embeddings import EmbeddingProvider
from app.providers.reranking import (
    MAX_RERANK_CANDIDATES, RerankCandidate, RerankConfigurationError, RerankError,
    RerankInputError, RerankOutputError, RerankProvider, RerankResult,
    RerankTimeoutError, RerankTrace, validate_candidates, validate_order,
)


logger = logging.getLogger(__name__)


def search_with_reranking(
    session: Session, provider: EmbeddingProvider,
    workspace_id: UUID, knowledge_base_id: UUID, query: str, *,
    top_k: int = 5, strategy: SearchStrategy = "vector",
    rerank: bool = False, candidate_k: int = 20,
    rerank_provider: RerankProvider | None = None,
) -> SearchResult:
    if type(rerank) is not bool:
        raise RerankInputError("重排开关必须是布尔值")
    if not rerank:
        return search_knowledge_base(session, provider, workspace_id, knowledge_base_id, query, top_k=top_k, strategy=strategy)
    if (
        type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K
        or type(candidate_k) is not int or not top_k <= candidate_k <= MAX_RERANK_CANDIDATES
    ):
        raise RerankInputError("重排候选量必须在最终返回条数与 20 之间")
    if rerank_provider is None:
        raise RerankConfigurationError("开启重排时必须提供重排服务")
    retrieved = search_knowledge_base(
        session, provider, workspace_id, knowledge_base_id, query, top_k=candidate_k, strategy=strategy,
    )
    if not retrieved.results:
        return replace(retrieved, reranking=RerankTrace(status="skipped_empty", candidate_count=0, elapsed_ms=0.0))
    # 只发正文与本次局部编号；原文、数据库 ID、字符偏移保留在本地快照。
    candidates = tuple(RerankCandidate(index, hit.content) for index, hit in enumerate(retrieved.results, 1))
    validate_candidates(query, candidates)
    started = monotonic()
    response_model = None
    reason = None
    order = list(range(1, len(candidates) + 1))
    try:
        response = rerank_provider.rerank(query.strip(), candidates)
        if (
            not isinstance(response, RerankResult) or not isinstance(response.response_model, str)
            or not 1 <= len(response.response_model.strip()) <= 200
        ):
            raise RerankOutputError("重排服务返回对象或模型标识无效")
        checked = validate_order(response.order, len(candidates))
        order = checked.ordered_ids
        response_model = response.response_model.strip()
    except RerankConfigurationError:
        raise
    except RerankError as error:
        if isinstance(error, RerankTimeoutError):
            reason = "timeout"
        elif isinstance(error, RerankOutputError):
            reason = "invalid_output"
        else:
            reason = "service_error"
        # 日志只含知识库 ID 与故障类型，不记录原文、问题或接口异常正文。
        logger.warning("候选重排降级，知识库 ID：%s，原因：%s", knowledge_base_id, reason)
    applied = reason is None
    hits = [replace(
        retrieved.results[identifier - 1], retrieval_rank=identifier,
        rerank_rank=rank if applied else None,
    ) for rank, identifier in enumerate(order[:top_k], 1)]
    return replace(retrieved, results=hits, reranking=RerankTrace(
        status="applied" if applied else "degraded", reason=reason,
        candidate_count=len(candidates), response_model=response_model,
        elapsed_ms=(monotonic() - started) * 1000,
    ))
