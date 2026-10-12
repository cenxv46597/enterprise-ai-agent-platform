"""有限 HTTP 传输与 JSON 正文协议；工具协议使用独立解码器。"""

from abc import ABC, abstractmethod
from contextlib import nullcontext
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from time import monotonic
from urllib.parse import unquote, urlsplit
from typing import Generic, TypeVar

import httpx
from pydantic import SecretStr


MAX_RESPONSE_BYTES = 256 * 1024


class JsonChatError(RuntimeError):
    """只携带固定错误信息，不携带接口响应和凭据。"""


class JsonChatTimeoutError(JsonChatError):
    """模型请求超过等待期限。"""


@dataclass(frozen=True, slots=True)
class JsonChatResult:
    content: dict
    model: str
    usage: dict | None


ChatResult = TypeVar("ChatResult")


class CompatibleChatTransport(ABC, Generic[ChatResult]):
    """复用配置验证、有限响应和总期限；失败不自动重试。"""

    def __init__(
        self, base_url: str, api_key: SecretStr, model: str,
        timeout_seconds: float = 30, total_timeout_seconds: float = 60,
        max_tokens: int = 2000, client: httpx.Client | None = None,
        *, request_observer: Callable[[dict], None] | None = None,
    ) -> None:
        if not isinstance(base_url, str):
            raise JsonChatError("模型服务地址配置无效。")
        normalized = base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(normalized)
            parsed.port
            decoded = unquote(normalized)
            parts = unquote(parsed.path).lower().strip("/").split("/")
            full_endpoint = any(parts[i:i + 2] == ["chat", "completions"] for i in range(len(parts) - 1))
            address = httpx.URL(normalized)
        except (httpx.InvalidURL, TypeError, ValueError):
            raise JsonChatError("模型服务地址配置无效。") from None
        if (
            not 1 <= len(normalized) <= 512 or parsed.scheme != "https"
            or not parsed.hostname or not address.host
            or parsed.username is not None or parsed.password is not None
            or "?" in normalized or "#" in normalized or "\\" in decoded
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in decoded)
            or any(char in decoded for char in "{}<>") or full_endpoint
        ):
            raise JsonChatError("模型服务地址必须是有效的 HTTPS 基础地址。")
        if not isinstance(api_key, SecretStr) or not api_key.get_secret_value().strip():
            raise JsonChatError("模型服务密钥未配置。")
        if not isinstance(model, str) or not 1 <= len(model.strip()) <= 200:
            raise JsonChatError("模型名称配置无效。")
        for timeout in (timeout_seconds, total_timeout_seconds):
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
                raise JsonChatError("模型服务等待时间配置无效。")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise JsonChatError("模型输出长度配置无效。")
        self.model = model.strip()
        self._base_url = normalized
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._max_tokens = max_tokens
        self._client = client
        self._request_observer = request_observer

    def _send_request(self, request: dict) -> ChatResult:
        """只发送已核对且已复制的正文；失败不重试。"""
        deadline = monotonic() + self._total_timeout_seconds
        context = nullcontext(self._client) if self._client is not None else httpx.Client(follow_redirects=False, trust_env=False)
        try:
            with context as client:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise JsonChatTimeoutError("模型请求超过本次处理的等待期限。")
                if self._request_observer is not None:
                    try:
                        # 仅传请求正文副本；回调不能修改实际待发送消息，也看不到认证头。
                        self._request_observer(json.loads(json.dumps(request, ensure_ascii=False)))
                    except Exception:
                        raise JsonChatError("模型请求快照保存失败。") from None
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise JsonChatTimeoutError("模型请求超过本次处理的等待期限。")
                with client.stream(
                    "POST", self._base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self._api_key.get_secret_value()},
                    json=request,
                    timeout=min(self._timeout_seconds, remaining), follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    if response.headers.get("content-type", "").lower().startswith("text/event-stream"):
                        raise JsonChatError("模型服务返回了不支持的流式消息。")
                    payload = bytearray()
                    for part in response.iter_bytes():
                        if monotonic() >= deadline:
                            raise JsonChatTimeoutError("模型请求超过本次处理的等待期限。")
                        if len(payload) + len(part) > MAX_RESPONSE_BYTES:
                            raise JsonChatError("模型服务响应超过允许的体积。")
                        payload.extend(part)
                    if monotonic() >= deadline:
                        raise JsonChatTimeoutError("模型请求超过本次处理的等待期限。")
                    decoded = json.loads(payload)
                result = self._decode(decoded)
                if monotonic() >= deadline:
                    raise JsonChatTimeoutError("模型请求超过本次处理的等待期限。")
                return result
        except httpx.TimeoutException:
            raise JsonChatTimeoutError("模型服务响应超时，请稍后重试。") from None
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, RecursionError):
            raise JsonChatError("模型服务调用失败，请检查配置或稍后重试。") from None

    @staticmethod
    @abstractmethod
    def _decode(payload: object) -> ChatResult:
        """子类只解码自己的协议；传输层负责期限、响应上限和错误脱敏。"""
        raise NotImplementedError


class CompatibleJsonChatClient(CompatibleChatTransport[JsonChatResult]):
    """JSON 正文协议；拒绝原生工具调用，不放宽快照重放契约。"""

    def complete(self, system_prompt: str, data: dict, *, temperature: float | None = None) -> JsonChatResult:
        """把资料编码为用户消息数据，只接受一次完整 JSON 响应。"""
        user_content = json.dumps(data, ensure_ascii=False)
        if temperature is not None and (
            isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0 <= temperature <= 2
        ):
            raise JsonChatError("模型采样参数配置无效。")
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            "stream": False, "response_format": {"type": "json_object"}, "max_tokens": self._max_tokens,
        }
        if temperature is not None:
            request["temperature"] = temperature
        return self.complete_request(request)

    def complete_request(self, body: dict) -> JsonChatResult:
        """重放已经构造的正文；保留用户消息字符串，不重新编码证据。"""
        try:
            required = {"model", "messages", "stream", "response_format", "max_tokens"}
            if not isinstance(body, dict) or set(body) not in (required, required | {"temperature"}):
                raise ValueError
            if (
                not isinstance(body["model"], str) or body["model"] != self.model
                or type(body["max_tokens"]) is not int or body["max_tokens"] != self._max_tokens
                or body["stream"] is not False
                or not isinstance(body["response_format"], dict)
                or body["response_format"] != {"type": "json_object"}
            ):
                raise ValueError
            messages = body["messages"]
            if not isinstance(messages, list) or len(messages) != 2:
                raise ValueError
            for message, role in zip(messages, ("system", "user"), strict=True):
                if (
                    not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] != role or not isinstance(message["content"], str)
                    or not message["content"].strip()
                ):
                    raise ValueError
            if "temperature" in body:
                temperature = body["temperature"]
                if (
                    isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                    or not math.isfinite(temperature) or not 0 <= temperature <= 2
                ):
                    raise ValueError
            request = deepcopy(body)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise JsonChatError("模型重放请求不符合约定格式。") from None
        return self._send_request(request)

    @staticmethod
    def _decode(payload: object) -> JsonChatResult:
        if not isinstance(payload, dict) or not isinstance(payload.get("model"), str) or not 1 <= len(payload["model"].strip()) <= 200:
            raise JsonChatError("模型服务返回的模型信息无效。")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise JsonChatError("模型服务返回的消息数量无效。")
        choice = choices[0]
        if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0 or choice.get("finish_reason") != "stop":
            raise JsonChatError("模型服务未正常完成生成。")
        message = choice.get("message")
        if (
            not isinstance(message, dict) or message.get("role") != "assistant"
            or message.get("tool_calls") not in (None, []) or message.get("function_call") is not None
            or not isinstance(message.get("content"), str) or not message["content"].strip()
        ):
            raise JsonChatError("模型服务返回的消息格式无效。")
        content = json.loads(message["content"])
        if not isinstance(content, dict):
            raise JsonChatError("模型输出必须是 JSON 对象。")
        return JsonChatResult(content, payload["model"].strip(), payload.get("usage"))
