"""知识搜索的有界 MCP 线协议，不接收任意外部观察或指令。"""

import json
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.tools import SearchArguments
from app.knowledge.retrieval import SearchHit, SearchStrategy


MAX_MCP_RESULT_BYTES = 100_000
MAX_MCP_DISCOVERED_TOOLS = 32


class McpSearchError(RuntimeError):
    """只携带固定故障码，不传播 SDK、进程或数据库异常正文。"""

    def __init__(self, code: str = "mcp_service_error") -> None:
        self.code = code
        super().__init__(code)


class SearchHitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    chunk_id: UUID
    document_id: UUID
    document_name: str = Field(min_length=1, max_length=255)
    chunk_index: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=12000)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    distance: float = Field(ge=0, le=2, allow_inf_nan=False)
    fusion_score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vector_rank: int | None = Field(default=None, ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    lexical_score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    retrieval_rank: int | None = Field(default=None, ge=1)
    rerank_rank: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_range(self) -> Self:
        if self.end_char - self.start_char != len(self.content):
            raise ValueError("原文范围与正文长度不一致")
        return self

    @classmethod
    def from_hit(cls, hit: SearchHit) -> Self:
        return cls(**{name: getattr(hit, name) for name in cls.model_fields})


class SearchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    workspace_id: UUID
    knowledge_base_id: UUID
    strategy: SearchStrategy
    hits: list[SearchHitPayload] = Field(max_length=5)

    @field_validator("schema_version", mode="before")
    @classmethod
    def check_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("结果契约版本必须是整数 1")
        return value

    @model_validator(mode="after")
    def check_budget(self) -> Self:
        if len({hit.chunk_id for hit in self.hits}) != len(self.hits):
            raise ValueError("重复原文块")
        if sum(len(hit.content) for hit in self.hits) > 12000:
            raise ValueError("原文预算超限")
        return self


def wire_json(data: object) -> str:
    """序列化后检查应用层上限；不宣称限制了 SDK 解码前的内存。"""
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_MCP_RESULT_BYTES:
        raise McpSearchError("mcp_result_invalid")
    return raw


def approved_input_schema() -> dict:
    return SearchArguments.model_json_schema()


def approved_output_schema() -> dict:
    return SearchEnvelope.model_json_schema()
