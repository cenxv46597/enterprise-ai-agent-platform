"""Real answer routes and HTTP tracing with controlled services and no SQL/models.

Run with Settings.env_file disabled before importing app.main, like HTTP tests.
"""

import asyncio
import importlib
import json
import logging
import os
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import httpx
import pytest

from app.api import answers as answer_api
from app.api.documents import get_embedding_provider
from app.api.reranking import get_rerank_provider
from app.auth.workspace_access import require_workspace_member
from app.core.config import Settings
from app.db import session as database_session
from app.knowledge import answers as answer_logic
from app.knowledge.retrieval import (
    RetrievalCapacityError, RetrievalConflictError, RetrievalInputError,
    RetrievalNotFoundError, RetrievalStateError, SearchHit,
)
from app.observability import http
from app.observability.answer_errors import AnswerErrorCode
from app.providers.embeddings import EmbeddingError, EmbeddingTimeoutError
from app.providers.generation import (
    GeneratedAnswer, GeneratedCitation, GenerationConfigurationError,
    GenerationError, GenerationTimeoutError,
)
from app.providers.reranking import RerankConfigurationError, RerankInputError


WORKSPACE_ID, KNOWLEDGE_BASE_ID = uuid4(), uuid4()
URL = f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases/{KNOWLEDGE_BASE_ID}/answers"
ROUTE = "/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/answers"
FIELDS = {
    "recorded_at", "service", "environment", "event", "trace_id", "method",
    "route", "status_code", "elapsed_ms", "outcome",
}


class NoDatabaseSession:
    def __getattr__(self, _name):
        raise AssertionError("answer error tests must not perform SQL")


class Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def application(monkeypatch):
    assert Settings.model_config.get("env_file") is None, "Run the no-env-file pytest wrapper"
    for name in tuple(os.environ):
        if name.startswith("EAAP_"):
            monkeypatch.delenv(name)
    main = importlib.import_module("app.main")
    controlled = Settings(_env_file=None, app_env="test",
                          jwt_secret_key="controlled-answer-trace-test-signing-key")
    monkeypatch.setattr(main, "Settings", lambda: controlled)
    monkeypatch.setattr(answer_api, "Settings", lambda: controlled)

    def forbidden(*_args, **_kwargs):
        pytest.fail("uncontrolled database/service execution")

    monkeypatch.setattr(database_session, "get_engine", forbidden)
    monkeypatch.setattr(answer_api, "answer_knowledge_base", forbidden)
    app = main.create_app()
    # Auth/membership are replaced for route-classification tests, not validated here.
    app.dependency_overrides[require_workspace_member] = lambda: None
    app.dependency_overrides[database_session.get_session] = lambda: NoDatabaseSession()
    app.dependency_overrides[get_embedding_provider] = lambda: object()
    app.dependency_overrides[answer_api.get_generation_provider] = lambda: object()
    app.dependency_overrides[get_rerank_provider] = lambda: object()
    collector = Collector()
    logger = logging.getLogger("eaap.http")
    logger.addHandler(collector)
    try:
        yield SimpleNamespace(app=app, records=collector.records)
    finally:
        logger.removeHandler(collector)


def assert_event(application, response, *, status, code=None, route=ROUTE, outcome="completed"):
    assert response.status_code == status
    assert len(response.headers.get_list("X-Trace-Id")) == 1
    trace = response.headers["X-Trace-Id"]
    assert UUID(trace).version == 4
    assert len(application.records) == 1
    record = application.records[0]
    value = json.loads(record.getMessage())
    assert set(value) == FIELDS | ({"error_code"} if code else set())
    assert value["event"] == "http_request" and value["trace_id"] == trace
    assert value["service"] == "eaap-api" and value["environment"] == "test"
    assert value["status_code"] == status and value["outcome"] == outcome
    assert value["route"] == route
    if code:
        assert value["error_code"] == code
    assert record.levelno == logging.INFO and record.exc_info is None
    assert "set-cookie" not in response.headers
    return value


@pytest.mark.parametrize("failure,status,code,detail", [
    (RetrievalNotFoundError, 404, "answer_knowledge_base_not_found", "知识库不存在"),
    (RetrievalConflictError, 409, "answer_embedding_profile_conflict", "查询模型与知识库向量配置不一致"),
    (RetrievalCapacityError, 422, "answer_retrieval_capacity_exceeded", "当前混合检索支持最多 2000 个可检索文本块、100 万正文字符，请使用较小知识库或选择 vector 策略"),
    (RetrievalInputError, 422, "answer_input_or_context_invalid", "问答参数无效或文本块超过上下文预算"),
    (answer_logic.AnswerContextLimitError, 422, "answer_input_or_context_invalid", "问答参数无效或文本块超过上下文预算"),
    (RerankInputError, 422, "answer_rerank_input_invalid", "重排参数无效或候选正文超过 20000 字符；请减少候选数或关闭重排"),
    (RerankConfigurationError, 503, "answer_rerank_configuration_invalid", "当前重排基线复用生成服务，请检查生成服务及重排等待时间配置"),
    (GenerationConfigurationError, 503, "answer_generation_configuration_invalid", "请检查生成服务的接口地址、API 密钥和模型配置"),
    (EmbeddingTimeoutError, 504, "answer_embedding_timeout", "模型服务请求超时"),
    (GenerationTimeoutError, 504, "answer_generation_timeout", "模型服务请求超时"),
    (EmbeddingError, 502, "answer_embedding_failed", "模型服务调用或返回结果异常"),
    (GenerationError, 502, "answer_generation_failed", "模型服务调用或返回结果异常"),
    (RetrievalStateError, 500, "answer_retrieval_state_invalid", "知识库向量数据异常"),
])
def test_known_answer_failures_share_response_trace_and_preserve_contract(
    application, monkeypatch, failure, status, code, detail, caplog,
):
    calls = []

    def fail(*args, **_kwargs):
        calls.append(args)
        raise failure("PRIVATE_EXCEPTION_SENTINEL")

    monkeypatch.setattr(answer_api, "answer_knowledge_base", fail)
    with TestClient(application.app) as client:
        response = client.post(URL + "?secret=QUERY_SENTINEL", json={"question": "QUESTION_SENTINEL"},
                               headers={"X-Trace-Id": str(uuid4()), "X-Error-Code": "CLIENT_CODE_SENTINEL",
                                        "Authorization": "Bearer AUTH_SENTINEL", "Cookie": "session=COOKIE_SENTINEL"})
    assert len(calls) == 1
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
    assert_event(application, response, status=status, code=code)
    summary = application.records[0].getMessage()
    for sentinel in ("PRIVATE_EXCEPTION_SENTINEL", "QUERY_SENTINEL", "QUESTION_SENTINEL",
                     "CLIENT_CODE_SENTINEL", "AUTH_SENTINEL", "COOKIE_SENTINEL",
                     str(WORKSPACE_ID), str(KNOWLEDGE_BASE_ID)):
        assert sentinel not in summary
    assert not [record for record in caplog.records if record.name == "app.api.answers"]


def test_success_after_failure_has_new_trace_and_no_previous_error_code(application, monkeypatch):
    calls = 0

    def answer(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GenerationTimeoutError("private")
        return answer_logic.AnswerResult(answer_logic.ABSTENTION_MESSAGE, True, [])

    monkeypatch.setattr(answer_api, "answer_knowledge_base", answer)
    with TestClient(application.app) as client:
        first = client.post(URL, json={"question": "first"})
        second = client.post(URL, json={"question": "second"}, headers={"X-Error-Code": "answer_generation_timeout"})
    assert first.status_code == 504 and second.status_code == 200 and calls == 2
    assert first.headers["X-Trace-Id"] != second.headers["X-Trace-Id"]
    values = [json.loads(record.getMessage()) for record in application.records]
    assert len(values) == 2 and values[0]["error_code"] == "answer_generation_timeout"
    assert set(values[1]) == FIELDS and second.json()["abstained"] is True


def test_concurrent_sync_answer_routes_keep_their_own_failure_classification(application, monkeypatch):
    barrier = Barrier(2)

    def fail(_session, _embedding, _generation, _workspace, _kb, question, **_kwargs):
        barrier.wait(timeout=5)
        if question == "embedding":
            raise EmbeddingTimeoutError("private")
        raise GenerationTimeoutError("private")

    monkeypatch.setattr(answer_api, "answer_knowledge_base", fail)

    async def request_both():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app),
                                     base_url="http://controlled.test") as client:
            return await asyncio.wait_for(asyncio.gather(*[
                client.post(URL, json={"question": question}) for question in ("embedding", "generation")
            ]), timeout=10)

    responses = asyncio.run(request_both())
    values = {value["trace_id"]: value for value in map(lambda r: json.loads(r.getMessage()), application.records)}
    assert len(values) == 2
    for response, code in zip(responses, ("answer_embedding_timeout", "answer_generation_timeout"), strict=True):
        assert response.status_code == 504
        assert values[response.headers["X-Trace-Id"]]["error_code"] == code


@pytest.mark.parametrize("saved,status", [
    ("answer_generation_timeout", 504),
    ("UNTRUSTED_ERROR_SENTINEL", 504),
    ({"error": "PRIVATE_ERROR_SENTINEL"}, 504),
    (AnswerErrorCode.GENERATION_TIMEOUT, 502),
    (AnswerErrorCode.GENERATION_TIMEOUT, 200),
])
def test_summary_does_not_copy_arbitrary_or_status_mismatched_state(application, saved, status):
    from starlette.responses import Response

    @application.app.get("/controlled-classification")
    def controlled(request: Request):
        request.state.http_answer_error = saved
        return Response(status_code=status)

    with TestClient(application.app) as client:
        response = client.get("/controlled-classification")
    assert_event(application, response, status=status, route="/controlled-classification")


@pytest.mark.parametrize("before_route", ["authentication", "validation", "embedding_dependency"])
def test_failures_before_answer_body_have_no_invented_business_classification(application, before_route):
    if before_route == "authentication":
        del application.app.dependency_overrides[require_workspace_member]
        expected_status, payload = 401, {"question": "controlled"}
    elif before_route == "validation":
        expected_status, payload = 422, {"question": ""}
    else:
        def reject_provider():
            raise HTTPException(status_code=503, detail="controlled provider configuration rejection")

        application.app.dependency_overrides[get_embedding_provider] = reject_provider
        expected_status, payload = 503, {"question": "controlled"}
    with TestClient(application.app) as client:
        response = client.post(URL, json=payload)
    assert_event(application, response, status=expected_status)


def test_unclassified_exception_keeps_generic_500_without_guessing_error_code(application, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("UNCLASSIFIED_PRIVATE_SENTINEL")

    monkeypatch.setattr(answer_api, "answer_knowledge_base", fail)
    with TestClient(application.app, raise_server_exceptions=False) as client:
        response = client.post(URL, json={"question": "controlled"})
    assert response.text == "Internal Server Error"
    assert_event(application, response, status=500, outcome="error")
    assert "UNCLASSIFIED_PRIVATE_SENTINEL" not in application.records[0].getMessage()


def test_log_sink_failure_preserves_known_answer_rejection(application, monkeypatch):
    def fail(*_args, **_kwargs):
        raise GenerationError("PRIVATE_EXCEPTION_SENTINEL")

    def broken_logger(_message):
        raise OSError("controlled sink failure")

    monkeypatch.setattr(answer_api, "answer_knowledge_base", fail)
    monkeypatch.setattr(http.logger, "info", broken_logger)
    with TestClient(application.app) as client:
        response = client.post(URL, json={"question": "controlled"})
    assert response.status_code == 502
    assert response.json() == {"detail": "模型服务调用或返回结果异常"}
    assert UUID(response.headers["X-Trace-Id"]).version == 4
    assert application.records == []


def test_real_citation_rejection_is_classified_without_returning_or_logging_draft(application, monkeypatch):
    content = "SOURCE_BODY_SENTINEL"
    hit = SearchHit(uuid4(), uuid4(), "PRIVATE_DOCUMENT_SENTINEL", 1, content, 0, len(content), 0.0)
    monkeypatch.setattr(answer_api, "answer_knowledge_base", answer_logic.answer_knowledge_base)
    monkeypatch.setattr(answer_logic, "search_with_reranking",
                        lambda *_args, **_kwargs: SimpleNamespace(results=[hit], reranking=None))
    calls = []

    class InvalidCitationProvider:
        def generate_answer(self, _question, _sources):
            calls.append(True)
            return GeneratedAnswer(answer="DRAFT_BODY_SENTINEL", abstained=False,
                                   citations=[GeneratedCitation(source_id=1, quote="INVENTED_QUOTE_SENTINEL")])

    application.app.dependency_overrides[answer_api.get_generation_provider] = InvalidCitationProvider
    with TestClient(application.app) as client:
        response = client.post(URL, json={"question": "QUESTION_BODY_SENTINEL"})
    assert calls == [True]
    assert_event(application, response, status=502, code="answer_generation_failed")
    visible = response.text + application.records[0].getMessage()
    for sentinel in (content, "PRIVATE_DOCUMENT_SENTINEL", "DRAFT_BODY_SENTINEL",
                     "INVENTED_QUOTE_SENTINEL", "QUESTION_BODY_SENTINEL"):
        assert sentinel not in visible


def test_real_valid_answer_keeps_body_and_has_no_error_code_or_content_in_summary(application, monkeypatch):
    content = "SOURCE_BODY_SENTINEL"
    hit = SearchHit(uuid4(), uuid4(), "PRIVATE_DOCUMENT_SENTINEL", 1, content, 0, len(content), 0.0)
    monkeypatch.setattr(answer_api, "answer_knowledge_base", answer_logic.answer_knowledge_base)
    monkeypatch.setattr(answer_logic, "search_with_reranking",
                        lambda *_args, **_kwargs: SimpleNamespace(results=[hit], reranking=None))
    calls = []

    class ValidCitationProvider:
        def generate_answer(self, _question, _sources):
            calls.append(True)
            return GeneratedAnswer(answer="ANSWER_BODY_SENTINEL", abstained=False,
                                   citations=[GeneratedCitation(source_id=1, quote=content)])

    application.app.dependency_overrides[answer_api.get_generation_provider] = ValidCitationProvider
    with TestClient(application.app) as client:
        response = client.post(URL, json={"question": "QUESTION_BODY_SENTINEL"})
    assert calls == [True]
    assert_event(application, response, status=200)
    assert response.json() == {
        "answer": "ANSWER_BODY_SENTINEL", "abstained": False,
        "citations": [{"chunk_id": str(hit.chunk_id), "document_id": str(hit.document_id),
                       "document_name": hit.document_name, "chunk_index": 1, "quote": content,
                       "start_char": 0, "end_char": len(content)}],
    }
    summary = application.records[0].getMessage()
    for sentinel in (content, hit.document_name, "ANSWER_BODY_SENTINEL", "QUESTION_BODY_SENTINEL",
                     str(hit.chunk_id), str(hit.document_id)):
        assert sentinel not in summary
