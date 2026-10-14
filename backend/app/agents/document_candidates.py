"""固定文件名候选工具；调用方先授权并绑定范围。"""

from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.knowledge.document_candidates import DocumentFilenameQuery, find_document_candidates


class DocumentCandidatesTool:
    name = "find_documents"
    description = (
        "在当前知识库按文件名线索查找文档候选，忽略大小写。最多返回五份并标记更多匹配；"
        "同名候选须确认，不自动选中。返回文件元数据，不能作为原文引用；未找到是正常空结果。"
    )
    argument_schema = DocumentFilenameQuery

    def __init__(self, session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self._knowledge_base_id = knowledge_base_id

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        if not isinstance(arguments, DocumentFilenameQuery):
            raise ToolExecutionError("invalid_arguments")
        try:
            validated = DocumentFilenameQuery.model_validate(arguments.model_dump(warnings=False), strict=True)
        except ValidationError:
            raise ToolExecutionError("invalid_arguments") from None
        try:
            result = find_document_candidates(
                self._session, self._workspace_id, self._knowledge_base_id, validated.filename_query,
            )
        except SQLAlchemyError:
            raise ToolExecutionError("tool_service_error") from None
        return ToolOutput(hits=(), document_candidates=result)
