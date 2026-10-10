"""用模拟 HTTP 服务验证请求契约、批次顺序和失败边界，不访问真实模型。"""

import json
import traceback

import httpx
from pydantic import SecretStr
import pytest

import app.providers.compatible_embeddings as adapter_module
from app.providers.compatible_embeddings import OpenAICompatibleEmbeddingProvider
from app.providers.embeddings import EmbeddingError, EmbeddingTimeoutError


BASE_URL = "https://model.example/compatible-mode/v1"
SECRET_KEY = "test-key-never-send-to-a-real-service"


def make_provider(handler, **options):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleEmbeddingProvider(
        BASE_URL, SecretStr(SECRET_KEY), client=client, **options
    )
    return provider, client


def response_payload(vectors, indices=None, model="text-embedding-v4"):
    if indices is None:
        indices = range(len(vectors))
    return {
        "model": model,
        "data": [
            {"index": index, "embedding": vector}
            for index, vector in zip(indices, vectors)
        ],
    }


def test_batches_send_selected_model_dimensions_and_restore_response_order() -> None:
    requests: list[httpx.Request] = []
    texts = [f"技术说明第 {index} 段" for index in range(11)]

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url) == BASE_URL + "/embeddings"
        assert request.headers["Authorization"] == "Bearer " + SECRET_KEY
        assert payload["model"] == "text-embedding-v4"
        assert payload["dimensions"] == 1024
        assert payload["encoding_format"] == "float"
        count = len(payload["input"])
        indices = list(reversed(range(count)))
        vectors = [[index + 1] + [0] * 1023 for index in indices]
        return httpx.Response(200, json=response_payload(vectors, indices))

    provider, client = make_provider(handler)
    with client:
        vectors = provider.embed_texts(texts)
        assert not client.is_closed
    assert [json.loads(request.content)["input"] for request in requests] == [
        texts[:10], texts[10:]
    ]
    assert [vector[0] for vector in vectors] == list(range(1, 11)) + [1]
    assert all(len(vector) == 1024 for vector in vectors)


def test_empty_list_does_not_call_service_and_base_url_is_normalized() -> None:
    def unexpected_request(request):
        pytest.fail("空列表不应访问模型服务")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            "  " + BASE_URL + "///  ", SecretStr(SECRET_KEY), client=client
        )
        assert provider.profile.provider == BASE_URL
        assert provider.embed_texts([]) == []


@pytest.mark.parametrize("texts", ["文本", [""], ["  \t"], [123], ["有效文本", None]])
def test_invalid_texts_are_rejected_before_request(texts) -> None:
    provider, client = make_provider(lambda request: pytest.fail("非法输入不应发送请求"))
    with client, pytest.raises(EmbeddingError):
        provider.embed_texts(texts)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://model.example/v1",
        "https://user:password@model.example/v1",
        BASE_URL + "?api_key=private",
        BASE_URL + "#fragment",
        BASE_URL + "/embeddings",
        "https://{WorkspaceId}.model.example/v1",
        "https://model.example/{WorkspaceId}/v1",
        "https://model.example:bad/v1",
        "https://model.example\\other/v1",
    ],
)
def test_invalid_service_address_is_rejected_without_echoing_it(base_url) -> None:
    with pytest.raises(EmbeddingError) as caught:
        OpenAICompatibleEmbeddingProvider(base_url, SecretStr(SECRET_KEY))
    assert base_url not in str(caught.value)
    assert SECRET_KEY not in str(caught.value)


@pytest.mark.parametrize("batch_size", [0, 11, True])
def test_service_batch_limit_is_enforced(batch_size) -> None:
    with pytest.raises(EmbeddingError):
        OpenAICompatibleEmbeddingProvider(BASE_URL, SecretStr(SECRET_KEY), batch_size=batch_size)


@pytest.mark.parametrize(
    "payload",
    [
        response_payload([[1, 2], [3, 4]], indices=[0, 0]),
        response_payload([[1, 2], [3, 4]], indices=[0, 2]),
        response_payload([[1, 2], [3, 4]], indices=[True, 1]),
        response_payload([[1, 2], [3, 4]], indices=["0", 1]),
        response_payload([[1, 2]]),
        response_payload([[1, 2], [3, 4]], model="another-model"),
        response_payload([[1], [3, 4]]),
        response_payload([[True, 2], [3, 4]]),
        response_payload([[float("nan"), 2], [3, 4]]),
        response_payload([[float("inf"), 2], [3, 4]]),
        response_payload([[1e39, 2], [3, 4]]),
        response_payload([[0, 0], [3, 4]]),
    ],
)
def test_malformed_service_results_never_become_valid_vectors(payload) -> None:
    # 手动编码允许模拟服务发回 NaN/Infinity；它们必须在存储前被拒绝。
    provider, client = make_provider(
        lambda request: httpx.Response(200, content=json.dumps(payload)), dimensions=2
    )
    with client, pytest.raises(EmbeddingError):
        provider.embed_texts(["第一段", "第二段"])


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_errors_and_redirects_are_not_retried_or_disclosed(status) -> None:
    calls = 0
    private_text = "这段输入原文不应出现在异常日志中"

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            headers={"Location": "https://unexpected.example/embeddings"},
            text=SECRET_KEY + private_text,
        )

    provider, client = make_provider(handler)
    with client, pytest.raises(EmbeddingError) as caught:
        provider.embed_texts([private_text])
    assert calls == 1
    formatted = "".join(traceback.format_exception(caught.value))
    assert SECRET_KEY not in formatted
    assert private_text not in formatted
    assert "unexpected.example" not in formatted


def test_network_timeout_is_safe_and_not_retried() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(SECRET_KEY, request=request)

    provider, client = make_provider(handler)
    with client, pytest.raises(EmbeddingTimeoutError) as caught:
        provider.embed_texts(["技术说明"])
    assert calls == 1
    assert SECRET_KEY not in "".join(traceback.format_exception(caught.value))


def test_non_json_response_becomes_safe_model_error() -> None:
    provider, client = make_provider(
        lambda request: httpx.Response(200, text="不是 JSON " + SECRET_KEY)
    )
    with client, pytest.raises(EmbeddingError) as caught:
        provider.embed_texts(["技术说明"])
    assert SECRET_KEY not in "".join(traceback.format_exception(caught.value))


def test_response_limit_stops_streaming_and_closes_response() -> None:
    class OversizedStream(httpx.SyncByteStream):
        consumed = 0
        closed = False

        def __iter__(self):
            for _ in range(40):
                self.consumed += 1
                yield b"x" * (64 * 1024)

        def close(self):
            self.closed = True

    stream = OversizedStream()
    provider, client = make_provider(lambda request: httpx.Response(200, stream=stream))
    with client, pytest.raises(EmbeddingError, match="体积"):
        provider.embed_texts(["技术说明"])
    assert stream.consumed == 33
    assert stream.closed


def test_total_deadline_limits_stream_read_and_request_timeout(monkeypatch) -> None:
    calls = 0
    clock = [0.0]

    def handler(request):
        nonlocal calls
        calls += 1
        assert request.extensions["timeout"]["read"] == 1
        clock[0] = 2
        return httpx.Response(200, json=response_payload([[1, 2]]))

    monkeypatch.setattr(adapter_module, "monotonic", lambda: clock[0])
    provider, client = make_provider(
        handler, dimensions=2, batch_size=1, total_timeout_seconds=1
    )
    with client, pytest.raises(EmbeddingTimeoutError):
        provider.embed_texts(["第一段", "第二段"])
    assert calls == 1


def test_failure_in_later_batch_raises_instead_of_returning_earlier_vectors() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=response_payload([[1, 2]]))
        return httpx.Response(429, text="模型服务限流")

    provider, client = make_provider(handler, dimensions=2, batch_size=1)
    with client, pytest.raises(EmbeddingError):
        provider.embed_texts(["第一段", "第二段"])
    assert calls == 2


def test_deadline_is_checked_between_small_stream_fragments(monkeypatch) -> None:
    clock = [0.0]

    class SlowStream(httpx.SyncByteStream):
        consumed = 0
        closed = False

        def __iter__(self):
            for _ in range(5):
                self.consumed += 1
                clock[0] += 0.1
                yield b"x"

        def close(self):
            self.closed = True

    stream = SlowStream()
    monkeypatch.setattr(adapter_module, "monotonic", lambda: clock[0])
    provider, client = make_provider(
        lambda request: httpx.Response(200, stream=stream), total_timeout_seconds=0.15
    )
    with client, pytest.raises(EmbeddingTimeoutError):
        provider.embed_texts(["技术说明"])
    assert stream.consumed == 2
    assert stream.closed
