"""文档状态工具；调用方先授权并绑定范围，不读取正文。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.knowledge.document_status import DocumentStatusNotFoundError, get_document_status


class DocumentStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    document_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="要查询的真实文档 UUID；从候选结果或明确指定的文档取得，不能猜测",
    )

    @field_validator("document_id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class DocumentStatusTool:
    name = "get_document_status"
    description = (
        "查询当前知识库内一份文档的处理状态，使用已取得的真实文档 ID。"
        "返回文件名、状态和错误码，不返回正文；文档状态 failed 仍是正常查询结果。"
    )
    argument_schema = DocumentStatusArguments

    def __init__(self, session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self._knowledge_base_id = knowledge_base_id

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        if not isinstance(arguments, DocumentStatusArguments):
            raise ToolExecutionError("invalid_arguments")
        try:
            validated = DocumentStatusArguments.model_validate(arguments.model_dump(warnings=False), strict=True)
        except ValidationError:
            raise ToolExecutionError("invalid_arguments") from None
        try:
            result = get_document_status(
                self._session, self._workspace_id, self._knowledge_base_id, UUID(validated.document_id),
            )
        except DocumentStatusNotFoundError:
            raise ToolExecutionError("document_not_found") from None
        except SQLAlchemyError:
            raise ToolExecutionError("tool_service_error") from None
        return ToolOutput(hits=(), document_status=result)
