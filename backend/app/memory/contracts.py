"""偏好作用域与版本契约；清除以带版本的空值表示。"""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


AnswerDetail = Literal["concise", "detailed"]
MAX_PREFERENCE_VERSION = 2147483647


class PreferenceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    knowledge_base_id: UUID
    version: int = Field(ge=0, le=MAX_PREFERENCE_VERSION)
    answer_detail: AnswerDetail | None

    @model_validator(mode="after")
    def zero_version_has_no_preference(self) -> Self:
        if self.version == 0 and self.answer_detail is not None:
            raise ValueError("未设置偏好不能携带回答详细程度")
        return self


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    expected_version: int = Field(ge=0, le=MAX_PREFERENCE_VERSION - 1)
    answer_detail: AnswerDetail | None


class PreferenceNotFoundError(ValueError):
    pass


class PreferenceConflictError(ValueError):
    pass


class PreferenceStateError(RuntimeError):
    pass
