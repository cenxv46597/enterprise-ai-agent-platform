"""调用兼容接口生成向量，限制批次、等待时间及响应体积。"""

from collections.abc import Sequence
from contextlib import nullcontext
import json
import math
from time import monotonic
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import SecretStr

from app.providers.embeddings import (
    EmbeddingError,
    EmbeddingProfile,
    EmbeddingTimeoutError,
    validate_embeddings,
)


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class OpenAICompatibleEmbeddingProvider:
    """同步调用模型服务；一次调用失败时不返回部分向量。"""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
        batch_size: int = 10,
        timeout_seconds: float = 30,
        total_timeout_seconds: float = 120,
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(base_url, str):
            raise EmbeddingError("向量服务地址配置无效。")
        normalized_url = base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(normalized_url)
            # 读取 port 也会检查错误的端口格式。
            parsed.port
        except ValueError:
            raise EmbeddingError("向量服务地址配置无效。") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in normalized_url
            or "#" in normalized_url
            or any(character.isspace() or ord(character) < 32 for character in normalized_url)
            or "\\" in normalized_url
            or "{" in unquote(normalized_url)
            or "}" in unquote(normalized_url)
            or "embeddings" in unquote(parsed.path).lower().split("/")
        ):
            raise EmbeddingError("向量服务地址必须是有效的 HTTPS 基础地址。")
        if not isinstance(api_key, SecretStr) or not api_key.get_secret_value().strip():
            raise EmbeddingError("向量服务密钥未配置。")
        if not isinstance(model, str) or not model.strip():
            raise EmbeddingError("向量模型名称配置无效。")
        if type(dimensions) is not int or dimensions <= 0:
            raise EmbeddingError("向量维数配置无效。")
        if type(batch_size) is not int or not 1 <= batch_size <= 10:
            raise EmbeddingError("每批向量请求必须包含 1 至 10 段文本。")
        for timeout in (timeout_seconds, total_timeout_seconds):
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise EmbeddingError("向量服务等待时间配置无效。")

        try:
            httpx.URL(normalized_url)
            self.profile = EmbeddingProfile(
                provider=normalized_url, model=model.strip(), dimensions=dimensions
            )
        except (httpx.InvalidURL, TypeError, ValueError):
            raise EmbeddingError("向量服务配置超出支持范围或格式无效。") from None
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._client = client

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
            raise EmbeddingError("待生成向量的文本必须是文本列表。")
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingError("待生成向量的每段文本都必须非空。")

        deadline = monotonic() + self._total_timeout_seconds
        # 禁止跟随重定向；默认不读取系统代理，避免把密钥发往意外地址。
        context = (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(follow_redirects=False, trust_env=False)
        )
        vectors: list[list[float]] = []
        try:
            with context as client:
                for offset in range(0, len(texts), self._batch_size):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise EmbeddingTimeoutError("向量生成超过本次处理的等待期限。")
                    batch = list(texts[offset : offset + self._batch_size])
                    with client.stream(
                        "POST",
                        self.profile.provider + "/embeddings",
                        headers={
                            "Authorization": "Bearer " + self._api_key.get_secret_value(),
                        },
                        json={
                            "model": self.profile.model,
                            "input": batch,
                            "dimensions": self.profile.dimensions,
                            "encoding_format": "float",
                        },
                        timeout=min(self._timeout_seconds, remaining),
                        follow_redirects=False,
                    ) as response:
                        # 错误响应不读取原文，也不把 HTTP 异常链接到对外异常。
                        response.raise_for_status()
                        payload = bytearray()
                        for part in response.iter_bytes():
                            if monotonic() >= deadline:
                                raise EmbeddingTimeoutError("向量生成超过本次处理的等待期限。")
                            if len(payload) + len(part) > MAX_RESPONSE_BYTES:
                                raise EmbeddingError("向量服务响应超过允许的体积。")
                            payload.extend(part)
                        if monotonic() >= deadline:
                            raise EmbeddingTimeoutError("向量生成超过本次处理的等待期限。")
                        decoded = json.loads(payload)
                    vectors.extend(self._decode_batch(decoded, len(batch)))
                    if monotonic() >= deadline:
                        raise EmbeddingTimeoutError("向量生成超过本次处理的等待期限。")
        except httpx.TimeoutException:
            raise EmbeddingTimeoutError("向量服务响应超时，请稍后重新处理。") from None
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, RecursionError):
            raise EmbeddingError("向量服务调用失败，请检查配置或稍后重新处理。") from None
        return vectors

    def _decode_batch(self, payload: object, count: int) -> list[list[float]]:
        if not isinstance(payload, dict) or payload.get("model") != self.profile.model:
            raise EmbeddingError("向量服务返回的模型名称与请求不一致。")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != count:
            raise EmbeddingError("向量服务返回的记录数量不正确。")
        ordered: list[object] = [None] * count
        seen: set[int] = set()
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingError("向量服务返回的记录格式不正确。")
            index = item.get("index")
            if type(index) is not int or not 0 <= index < count or index in seen:
                raise EmbeddingError("向量服务返回的记录编号不正确。")
            seen.add(index)
            ordered[index] = item.get("embedding")
        return validate_embeddings(
            ordered, expected_count=count, dimensions=self.profile.dimensions
        )
