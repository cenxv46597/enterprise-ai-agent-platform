"""公开演示专用生成请求快照；记录正文，不保存请求头或凭据。"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class GenerationSnapshotError(ValueError):
    """不携带输入正文的固定快照错误。"""


class SnapshotSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: int = Field(ge=1, le=20)
    content: str = Field(min_length=1, max_length=50000)


class SnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=2000)
    sources: list[SnapshotSource] = Field(min_length=1, max_length=20)


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def capture_generation_request(payload: dict, public_text: str, expected_question: str) -> dict:
    """只接受固定公开题目的生成请求；验证通过后才复制、保存正文。"""
    try:
        if not isinstance(payload, dict) or set(payload) != {"model", "messages", "stream", "response_format", "max_tokens"}:
            raise GenerationSnapshotError("request_fields")
        if (
            not isinstance(payload["model"], str) or not 1 <= len(payload["model"].strip()) <= 200
            or payload["stream"] is not False or payload["response_format"] != {"type": "json_object"}
            or type(payload["max_tokens"]) is not int or not 1 <= payload["max_tokens"] <= 8192
        ):
            raise GenerationSnapshotError("request_parameters")
        messages = payload["messages"]
        if not isinstance(messages, list) or len(messages) != 2:
            raise GenerationSnapshotError("request_messages")
        for message, role in zip(messages, ("system", "user"), strict=True):
            if (
                not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] != role or not isinstance(message["content"], str)
                or not message["content"].strip() or len(message["content"]) > 200000
            ):
                raise GenerationSnapshotError("request_message")
        data = SnapshotInput.model_validate(json.loads(messages[1]["content"]))
        if data.question != expected_question.strip() or not data.question.strip():
            raise GenerationSnapshotError("question_changed")
        if [source.source_id for source in data.sources] != list(range(1, len(data.sources) + 1)):
            raise GenerationSnapshotError("source_order")
        if sum(len(source.content) for source in data.sources) > 50000:
            raise GenerationSnapshotError("context_size")
        if not public_text or any(not source.content.strip() or source.content not in public_text for source in data.sources):
            raise GenerationSnapshotError("non_public_source")
        return {
            "version": "generation-request-snapshot-v1",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "capture_stage": "请求发送前；不证明远端已经收到或完成生成",
            "request_body": deepcopy(payload),
            "request_body_sha256": digest(payload),
            "input_sha256": digest(data.model_dump()),
            "system_prompt_sha256": hashlib.sha256(messages[0]["content"].encode("utf-8")).hexdigest(),
            "public_text_sha256": hashlib.sha256(public_text.encode("utf-8")).hexdigest(),
            "source_count": len(data.sources),
            "context_characters": sum(len(source.content) for source in data.sources),
        }
    except GenerationSnapshotError:
        raise
    except (ValidationError, ValueError, TypeError, KeyError, RecursionError):
        raise GenerationSnapshotError("snapshot_contract") from None
