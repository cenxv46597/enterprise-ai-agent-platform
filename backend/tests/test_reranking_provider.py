"""模拟 HTTP 验证生成式重排契约及一次请求边界，不访问模型服务。"""

import json

import httpx
from pydantic import SecretStr
import pytest

from app.providers.compatible_reranking import CompatibleRerankProvider
from app.providers.reranking import (
    RerankCandidate, RerankConfigurationError, RerankError, RerankInputError,
    RerankOrder, RerankOutputError, RerankTimeoutError, validate_candidates, validate_order,
)


CANDIDATES = (RerankCandidate(1, "退款审核通过后到账。"), RerankCandidate(2, "在订单详情页提交退款申请。"))


def payload(content=None):
    return {"model": "controlled-model", "choices": [{
        "index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(content or {"ordered_ids": [2, 1]}, ensure_ascii=False)},
    }]}


def make_provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CompatibleRerankProvider("https://example.test/v1", SecretStr("fictional-test-key"), "test-model", client=client), client


def test_single_request_sends_original_text_and_local_ids_only():
    seen = []
    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["temperature"] == 0 and body["max_tokens"] == 512
        system, user = body["messages"]
        assert "不可信" in system["content"] and "排序不代表允许回答" in system["content"]
        assert json.loads(user["content"]) == {"question": "怎样提交退款？", "candidates": [
            {"candidate_id": item.candidate_id, "content": item.content} for item in CANDIDATES
        ]}
        return httpx.Response(200, json=payload())
    provider, client = make_provider(handler)
    with client:
        result = provider.rerank("  怎样提交退款？  ", CANDIDATES)
    assert result.order.ordered_ids == [2, 1] and result.response_model == "controlled-model"
    assert len(seen) == 1


@pytest.mark.parametrize("content", [
    {"ordered_ids": [1]}, {"ordered_ids": [1, 1]}, {"ordered_ids": [1, 3]},
    {"ordered_ids": [True, 2]}, {"ordered_ids": ["2", "1"]}, {"ordered_ids": [1.0, 2.0]},
    {"ordered_ids": []}, {"ordered_ids": [2, 1], "answer": "伪造答案"}, {"order": [2, 1]},
])
def test_invalid_permutations_rejected_without_retry(content):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload(content))
    provider, client = make_provider(handler)
    with client, pytest.raises(RerankOutputError):
        provider.rerank("怎样提交退款？", CANDIDATES)
    assert len(seen) == 1


@pytest.mark.parametrize("question,candidates", [
    (" ", CANDIDATES), ("问" * 2001, CANDIDATES), (42, CANDIDATES),
    ("问题", ()), ("问题", "文字"), ("问题", (RerankCandidate(True, "文字"),)),
    ("问题", (RerankCandidate(2, "文字"),)), ("问题", (RerankCandidate(1, " "),)),
    ("问题", (RerankCandidate(1, "字" * 20001),)),
    ("问题", tuple(RerankCandidate(i, "原文") for i in range(1, 22))),
])
def test_invalid_input_rejected_before_network(question, candidates):
    def forbidden(request):
        pytest.fail("无效输入不应发送请求")
    provider, client = make_provider(forbidden)
    with client, pytest.raises(RerankInputError):
        provider.rerank(question, candidates)


def test_character_limit_accepts_exact_boundary_and_checks_whole_pool():
    validate_candidates("问题", (RerankCandidate(1, "字" * 20000),))
    with pytest.raises(RerankInputError):
        validate_candidates("问题", (RerankCandidate(1, "字" * 10000), RerankCandidate(2, "字" * 10001)))


def test_constructed_object_cannot_bypass_integer_validation():
    with pytest.raises(RerankOutputError):
        validate_order(RerankOrder.model_construct(ordered_ids=[True, 2]), 2)


@pytest.mark.parametrize("error,expected", [
    (httpx.ReadTimeout("fictional-sensitive-body"), RerankTimeoutError),
    (httpx.ConnectError("fictional-sensitive-body"), RerankError),
])
def test_transport_failures_are_sanitized_and_not_retried(error, expected):
    seen = []
    def handler(request):
        seen.append(request)
        raise error
    provider, client = make_provider(handler)
    with client, pytest.raises(expected) as caught:
        provider.rerank("问题", CANDIDATES)
    assert len(seen) == 1 and "fictional-sensitive-body" not in str(caught.value)


def test_invalid_configuration_is_separate_from_runtime_failure():
    with pytest.raises(RerankConfigurationError):
        CompatibleRerankProvider("http://example.test", SecretStr("fictional-test-key"), "test-model")


def test_lazy_provider_explicitly_reuses_generation_config(monkeypatch):
    from app.api import reranking as api
    from app.core.config import Settings
    from app.providers.reranking import RerankResult
    settings = Settings(_env_file=None, generation_base_url="https://example.test/v1",
                        generation_api_key=SecretStr("fictional-test-key"), generation_model="test-model")
    monkeypatch.setattr(api, "Settings", lambda: settings)
    observed = []
    class Adapter:
        def __init__(self, **options):
            observed.append(options)
        def rerank(self, question, candidates):
            return RerankResult(RerankOrder(ordered_ids=[2, 1]), "test-model")
    monkeypatch.setattr(api, "CompatibleRerankProvider", Adapter)
    provider = api.get_rerank_provider()
    assert not observed
    assert provider.rerank("问题", CANDIDATES).order.ordered_ids == [2, 1]
    assert observed[0]["base_url"] == settings.generation_base_url
    assert observed[0]["api_key"] == settings.generation_api_key
    assert observed[0]["model"] == settings.generation_model
    assert observed[0]["max_tokens"] == 512 and observed[0]["total_timeout_seconds"] == 20


def test_lazy_provider_missing_config_does_not_start_transport(monkeypatch):
    from app.api import reranking as api
    from app.core.config import Settings
    monkeypatch.setattr(api, "Settings", lambda: Settings(_env_file=None, generation_base_url=None,
                                                        generation_api_key=None, generation_model=None))
    def forbidden(**options):
        pytest.fail("缺失配置不能构造请求客户端")
    monkeypatch.setattr(api, "CompatibleRerankProvider", forbidden)
    with pytest.raises(RerankConfigurationError):
        api.get_rerank_provider().rerank("问题", CANDIDATES)
