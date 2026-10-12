"""模型评审的受控接口测试；不访问真实服务。"""

from copy import deepcopy
import json
import traceback

import httpx
from pydantic import SecretStr
import pytest

from app.evaluation.answers import RubricCheck
from app.providers.compatible_review import CompatibleReviewProvider
from app.providers.review import ReviewConfigurationError, ReviewError, ReviewTask, ReviewTimeoutError


SECRET = "controlled-review-key"
PUBLIC = "健康检查通过后，恢复文件导入。"
ANSWER = "健康检查完成后，恢复文件导入。"
DRAFT = {"checks": {"healthcheck_gate": {
    "verdict": "不通过", "reason": "完成检查不保证通过，答案改变了恢复导入的前提。",
    "answer_excerpt": "健康检查完成后", "evidence_excerpt": "健康检查通过后",
}}}


def task():
    return ReviewTask(
        case_id="recovery", answer_sha256="a" * 64, question="什么时候恢复导入？",
        response={"answer": ANSWER, "abstained": False, "citations": [{
            "document_id": "00000000-0000-0000-0000-000000000001",
            "chunk_id": "00000000-0000-0000-0000-000000000002", "document_name": "public.md",
            "chunk_index": 1, "quote": PUBLIC, "start_char": 0, "end_char": len(PUBLIC),
        }]}, public_text=PUBLIC,
        checks=[RubricCheck(id="healthcheck_gate", category="必要条件", description="通过才恢复", evidence=PUBLIC)],
    )


def envelope(draft=None, **changes):
    value = {
        "model": "controlled-review-snapshot",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(DRAFT if draft is None else draft, ensure_ascii=False),
        }}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    value.update(changes)
    return value


def provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CompatibleReviewProvider("https://review.example/v1", SecretStr(SECRET), "controlled-review", client=client), client


def test_request_keeps_untrusted_data_separate_and_only_records_ai_identity():
    calls = []
    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == "https://review.example/v1/chat/completions"
        assert payload["stream"] is False
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["temperature"] == 0
        assert "不可信数据" in payload["messages"][0]["content"]
        data = json.loads(payload["messages"][1]["content"])
        assert set(data) == {"question", "response", "public_text", "checks"}
        assert SECRET not in payload["messages"][1]["content"]
        assert data["response"]["answer"] == ANSWER
        assert "expected_abstained" not in data
        return httpx.Response(200, json=envelope())
    adapter, client = provider(handler)
    with client:
        result = adapter.review_answer(task())
        assert not client.is_closed
    assert len(calls) == 1
    assert result.review.reviewer_kind == "AI辅助"
    assert result.review.answer_sha256 == task().answer_sha256
    assert result.usage.total_tokens == 120
    assert result.response_model == "controlled-review-snapshot"


@pytest.mark.parametrize("change", [
    {"checks": {}}, {"checks": {"unknown": DRAFT["checks"]["healthcheck_gate"]}},
    {"reviewer_kind": "人工"},
    {"checks": {"healthcheck_gate": {"verdict": "通过", "reason": " ", "answer_excerpt": None, "evidence_excerpt": None}}},
    {"checks": {"healthcheck_gate": {"verdict": "不通过", "reason": "有问题", "answer_excerpt": SECRET, "evidence_excerpt": PUBLIC}}},
    {"checks": {"healthcheck_gate": {"verdict": "不通过", "reason": "有问题", "answer_excerpt": ANSWER, "evidence_excerpt": "捏造原文"}}},
    {"checks": {"healthcheck_gate": {"verdict": "待审核", "reason": "已经审核", "answer_excerpt": None, "evidence_excerpt": None}}},
])
def test_invalid_decisions_and_invented_excerpts_are_rejected_without_echo(change):
    draft = deepcopy(DRAFT)
    draft.update(change)
    adapter, client = provider(lambda request: httpx.Response(200, json=envelope(draft)))
    with client, pytest.raises(ReviewError) as caught:
        adapter.review_answer(task())
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert ANSWER not in str(caught.value)


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": True, "completion_tokens": 20, "total_tokens": 21},
    {"prompt_tokens": -1, "completion_tokens": 20, "total_tokens": 19},
    {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 999},
    "not-a-usage-object",
])
def test_invalid_service_usage_is_not_reported_as_known_cost(usage):
    adapter, client = provider(lambda request: httpx.Response(200, json=envelope(usage=usage)))
    with client, pytest.raises(ReviewError):
        adapter.review_answer(task())


def test_missing_usage_remains_unknown_and_pending_is_not_a_pass():
    draft = {"checks": {"healthcheck_gate": {"verdict": "待审核", "reason": None, "answer_excerpt": None, "evidence_excerpt": None}}}
    adapter, client = provider(lambda request: httpx.Response(200, json=envelope(draft, usage=None)))
    with client:
        result = adapter.review_answer(task())
    assert result.usage is None
    assert result.review.reviewer_kind is None
    assert result.review.checks["healthcheck_gate"].verdict == "待审核"


@pytest.mark.parametrize("failure", ["timeout", "http", "truncated"])
def test_technical_failures_are_safe_and_never_retried(failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if failure == "http":
            return httpx.Response(429, text=SECRET)
        payload = envelope()
        payload["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=payload)
    adapter, client = provider(handler)
    with client, pytest.raises(ReviewTimeoutError if failure == "timeout" else ReviewError) as caught:
        adapter.review_answer(task())
    assert len(calls) == 1
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_review_configuration_is_explicit_and_invalid_input_precedes_call():
    with pytest.raises(ReviewConfigurationError):
        CompatibleReviewProvider("http://review.example", SecretStr(SECRET), "controlled-review")
    adapter, client = provider(lambda request: pytest.fail("非法检查项不应发请求"))
    invalid = task().model_copy(update={"checks": []})
    with client, pytest.raises(ReviewError):
        adapter.review_answer(invalid)
