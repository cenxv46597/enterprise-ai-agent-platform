"""会话状态查询使用短事务；已由用户选定的文档按程序动作执行，不再交给模型挑选。"""

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.agents.contracts import FinishAction, ToolAction, ToolContext, ToolOutput, ToolExecutionError
from app.agents.document_candidates import DocumentCandidatesTool
from app.agents.document_status import DocumentStatusTool
from app.agents.tools import Tool, ToolRegistry


class _TransactionalQueryTool:
    def __init__(self, session: Session, tool: Tool) -> None:
        self._session, self._tool = session, tool
        self.name, self.description, self.argument_schema = tool.name, tool.description, tool.argument_schema

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolOutput:
        # 查询函数由调用方管理事务；模型决策前关闭事务，不持锁等待模型。
        try:
            with self._session.begin():
                return self._tool.execute(arguments, context)
        except SQLAlchemyError:
            raise ToolExecutionError("tool_service_error") from None


def document_status_registry(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (DocumentCandidatesTool(session, workspace_id, knowledge_base_id),
                 DocumentStatusTool(session, workspace_id, knowledge_base_id)):
        registry.register(_TransactionalQueryTool(session, tool))
    return registry


class SelectedDocumentPlanner:
    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id

    def decide(self, state: dict) -> ToolAction | FinishAction:
        if not state["observations"]:
            return ToolAction(action="tool", tool_name="get_document_status",
                              arguments={"document_id": str(self.document_id)})
        return FinishAction(action="finish")
