"""原生工具协议；共用有限 HTTP 传输，独立校验消息配对和调用响应。"""

from dataclasses import dataclass
from copy import deepcopy
import json
import math
import re
from urllib.parse import urlsplit

from app.agents.contracts import NativeToolCall
from app.providers.compatible_chat import CompatibleChatTransport, JsonChatError


MAX_TOOL_REQUEST_BYTES = 512 * 1024
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def strict_object(raw: str) -> dict:
    """拒绝重复键和非 JSON 数值，避免参数在不同解析器中产生歧义。"""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)
    finite(result)
    return result


@dataclass(frozen=True, slots=True)
class ToolChatResult:
    name: str | None
    arguments: dict | None
    call: NativeToolCall | None
    model: str
    usage: dict | None


def _validate_call(call: object) -> tuple[str, dict, NativeToolCall]:
    if not isinstance(call, dict) or set(call) != {"id", "type", "function"} or call["type"] != "function":
        raise ValueError
    function = call["function"]
    if not isinstance(function, dict) or set(function) != {"name", "arguments"}:
        raise ValueError
    name, raw = function["name"], function["arguments"]
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or not isinstance(raw, str) or not 2 <= len(raw) <= 8192:
        raise ValueError
    envelope = NativeToolCall(id=call["id"], arguments_json=raw)
    arguments = strict_object(raw)
    if len(arguments) > 10:
        raise ValueError
    return name, arguments, envelope


def _validate_messages(messages: object) -> None:
    if not isinstance(messages, list) or not 2 <= len(messages) <= 17:
        raise ValueError
    for message, role in zip(messages[:2], ("system", "user"), strict=True):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] != role or not isinstance(message["content"], str) or not message["content"].strip()):
            raise ValueError
    tail = messages[2:]
    if tail and isinstance(tail[-1], dict) and tail[-1].get("role") == "user":
        final = tail.pop()
        if set(final) != {"role", "content"} or not isinstance(final["content"], str) or not final["content"].strip():
            raise ValueError
    if len(tail) % 2:
        raise ValueError
    used = set()
    for index in range(0, len(tail), 2):
        assistant, tool = tail[index:index + 2]
        if (not isinstance(assistant, dict) or set(assistant) != {"role", "content", "tool_calls"}
                or assistant["role"] != "assistant"
                or (assistant["content"] is not None and not isinstance(assistant["content"], str))
                or not isinstance(assistant["tool_calls"], list) or len(assistant["tool_calls"]) != 1):
            raise ValueError
        _, _, call = _validate_call(assistant["tool_calls"][0])
        if call.id in used:
            raise ValueError
        used.add(call.id)
        if (not isinstance(tool, dict) or set(tool) != {"role", "tool_call_id", "content"}
                or tool["role"] != "tool" or tool["tool_call_id"] != call.id
                or not isinstance(tool["content"], str) or not tool["content"].strip()):
            raise ValueError


class CompatibleToolChatClient(CompatibleChatTransport[ToolChatResult]):
    """当前一次只接受一个调用；不自动降级到 JSON 或重试模型。"""

    def complete(self, messages: list[dict], definitions: list[dict]) -> ToolChatResult:
        try:
            _validate_messages(messages)
            if not isinstance(definitions, list) or not 1 <= len(definitions) <= 8:
                raise ValueError
            names = set()
            for definition in definitions:
                if not isinstance(definition, dict) or set(definition) != {"name", "description", "parameters"}:
                    raise ValueError
                name = definition["name"]
                if (not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or name in names
                        or not isinstance(definition["description"], str)
                        or not isinstance(definition["parameters"], dict) or definition["parameters"].get("type") != "object"):
                    raise ValueError
                names.add(name)
            request = {"model": self.model, "messages": messages, "stream": False,
                       "max_tokens": self._max_tokens, "temperature": 0, "tool_choice": "auto",
                       "tools": [{"type": "function", "function": item} for item in definitions]}
            # 官网当前默认思考模式，需要额外思考字段的连续回传；本轮显式使用普通模式。
            # 专属扩展不发给其他兼容服务；不基于模型名称猜测服务商能力。
            if urlsplit(self._base_url).hostname == "api.deepseek.com":
                request["thinking"] = {"type": "disabled"}
            encoded = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_TOOL_REQUEST_BYTES:
                raise ValueError
            request = deepcopy(request)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise JsonChatError("模型工具请求不符合约定格式或超过预算。") from None
        return self._send_request(request)

    @staticmethod
    def _decode(payload: object) -> ToolChatResult:
        try:
            if (not isinstance(payload, dict) or not isinstance(payload.get("model"), str)
                    or not 1 <= len(payload["model"].strip()) <= 200):
                raise ValueError
            choices = payload.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
                raise ValueError
            message = choice.get("message")
            if (not isinstance(message, dict) or message.get("role") != "assistant"
                    or message.get("function_call") is not None or message.get("reasoning_content") not in (None, "")):
                raise ValueError
            calls, content = message.get("tool_calls"), message.get("content")
            if choice.get("finish_reason") == "stop" and calls in (None, []):
                if not isinstance(content, str) or not content.strip():
                    raise ValueError
                # 普通文本只表示结束；绝不直接成为最终答案。
                return ToolChatResult(None, None, None, payload["model"].strip(), payload.get("usage"))
            if choice.get("finish_reason") != "tool_calls" or not isinstance(calls, list) or len(calls) != 1:
                raise ValueError
            name, arguments, envelope = _validate_call(calls[0])
            envelope = NativeToolCall(id=envelope.id, arguments_json=envelope.arguments_json, content=content)
            return ToolChatResult(name, arguments, envelope, payload["model"].strip(), payload.get("usage"))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise JsonChatError("模型工具响应不符合单动作协议。") from None
