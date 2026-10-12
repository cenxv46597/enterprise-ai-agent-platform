"""计算请求与独立来源记录；派生结果不充当文档原文。"""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


CalculationOperation = Literal["add", "subtract", "multiply", "divide"]
MAX_CALCULATIONS = 7
DIVISION_DECIMAL_PLACES = 8


def canonical_decimal(value: Decimal) -> str:
    """固定格式输出，无 float 中转；不依赖全局精度做 normalize。"""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class CalculationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    evidence_id: int = Field(ge=1, le=20, description="本次知识搜索返回的证据编号")
    quote: str = Field(min_length=1, max_length=200, description="包含一个完整数字的逐字原文；不支持百分数、千分位或指数")
    value: str = Field(max_length=20, pattern=r"^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$",
                       description="原文数值，十进制字符串，整数最多12位、小数最多6位；不转换单位")

    @field_validator("quote", mode="before")
    @classmethod
    def strip_quote(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("value", mode="after")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        return canonical_decimal(Decimal(value))


class CalculatorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation: CalculationOperation
    left: CalculationInput
    right: CalculationInput


class BoundNumber(CalculationInput):
    """已绑定真实块与引文位置的原文数值。"""
    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    start_char: int
    end_char: int


class CalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation: CalculationOperation
    left: BoundNumber
    right: BoundNumber
    value: str = Field(max_length=50, pattern=r"^-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?$")
    rounded: bool
    decimal_places: int | None = Field(default=None, ge=0, le=DIVISION_DECIMAL_PLACES)
    provenance: Literal["program_calculation"] = "program_calculation"


class AgentCalculation(CalculationResult):
    calculation_id: int = Field(ge=1, le=MAX_CALCULATIONS)
