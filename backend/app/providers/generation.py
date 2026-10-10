"""文本生成契约：业务层使用证据与结构化草稿，不依赖具体模型接口。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GenerationError(RuntimeError):
    """生成服务调用失败或输出不符合约定。"""


class GenerationTimeoutError(GenerationError):
    """生成服务超时。"""


class GenerationConfigurationError(GenerationError):
    """生成服务尚未配置或配置无效。"""


@dataclass(frozen=True, slots=True)
class GenerationSource:
    source_id: int
    content: str


class GeneratedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: int = Field(ge=1, le=20)
    quote: str = Field(min_length=1, max_length=1000)

    @field_validator("quote", mode="before")
    @classmethod
    def strip_quote(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    answer: str = Field(max_length=4000)
    abstained: bool
    citations: list[GeneratedCitation] = Field(max_length=20)

    @field_validator("answer", mode="before")
    @classmethod
    def strip_answer(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_decision(self) -> Self:
        if self.abstained:
            if self.answer or self.citations:
                raise ValueError("弃答草稿必须没有答案和引用")
        elif not self.answer or not self.citations:
            raise ValueError("回答草稿必须包含答案和引用")
        return self


class GenerationProvider(Protocol):
    def generate_answer(
        self, question: str, sources: Sequence[GenerationSource]
    ) -> GeneratedAnswer:
        """返回结构化草稿；调用方负责核对来源编号与逐字引文。"""
        ...
