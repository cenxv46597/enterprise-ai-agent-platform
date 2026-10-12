"""引用式操作数；模型不能同时提交引用与自行抄写的值。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.calculation_contracts import (
    BoundNumber, CalculationOperation, DIVISION_DECIMAL_PLACES, MAX_CALCULATIONS,
)
from app.conversations.contracts import CountUnit


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EvidenceOperand(Reference):
    source: Literal["evidence"]
    evidence_id: int = Field(ge=1, le=20)
    quote: str = Field(min_length=1, max_length=200)

    @field_validator("quote", mode="before")
    @classmethod
    def strip_quote(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UserOperand(Reference):
    source: Literal["user_input"]
    field_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    request_id: str = Field(pattern=r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")


class ResultOperand(Reference):
    source: Literal["calculation"]
    calculation_id: int = Field(ge=1, le=MAX_CALCULATIONS)


Operand = Annotated[EvidenceOperand | UserOperand | ResultOperand, Field(discriminator="source")]


class SourcedArguments(Reference):
    operation: CalculationOperation
    left: Operand
    right: Operand


class BoundEvidence(BoundNumber):
    source: Literal["evidence"] = "evidence"


class BoundUser(UserOperand):
    value: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,3})$")
    unit: CountUnit
    raw_text: str = Field(min_length=1, max_length=2000)
    provenance: Literal["user_message"] = "user_message"


class BoundResult(ResultOperand):
    value: str = Field(max_length=50, pattern=r"^-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?$")
    rounded: bool
    decimal_places: int | None = Field(default=None, ge=0, le=DIVISION_DECIMAL_PLACES)
    provenance: Literal["program_calculation"] = "program_calculation"


BoundOperand = Annotated[BoundEvidence | BoundUser | BoundResult, Field(discriminator="source")]


class SourcedCalculation(Reference):
    calculator_kind: Literal["sourced"] = "sourced"
    operation: CalculationOperation
    left: BoundOperand
    right: BoundOperand
    value: str = Field(max_length=50, pattern=r"^-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?$")
    rounded: bool
    decimal_places: int | None = Field(default=None, ge=0, le=DIVISION_DECIMAL_PLACES)
    provenance: Literal["program_calculation"] = "program_calculation"


class SourcedAgentCalculation(SourcedCalculation):
    calculation_id: int = Field(ge=1, le=MAX_CALCULATIONS)
