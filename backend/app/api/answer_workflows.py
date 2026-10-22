"""显式创建、读取与执行固定知识问答工作流；不改动既有问答/Agent/会话入口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.answers import GenerationDep
from app.api.documents import EmbeddingDep
from app.api.workspaces import DbSession
from app.workflows.contracts import (
    WorkflowConflictError, WorkflowCreate, WorkflowExecute, WorkflowExecutionRead,
    WorkflowListItem, WorkflowNotFoundError, WorkflowRead, WorkflowStatus,
)
from app.workflows.service import create_workflow, execute_workflow, list_workflows, read_workflow


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/answer-workflows",
                   tags=["answer-workflows"])


def _call(operation, *args):
    try:
        return operation(*args)
    except WorkflowNotFoundError:
        raise HTTPException(404, "知识库或工作流不存在") from None
    except WorkflowConflictError as error:
        raise HTTPException(409, str(error)) from None
    except ValidationError:
        raise HTTPException(422, "工作流输入或保存状态无效") from None
    except SQLAlchemyError:
        raise HTTPException(503, "工作流数据库操作失败；请读取状态后再决定是否恢复") from None


@router.post("", response_model=WorkflowRead, status_code=201)
def create(workspace_id: UUID, knowledge_base_id: UUID, payload: WorkflowCreate,
           session: DbSession) -> WorkflowRead:
    return _call(create_workflow, session, workspace_id, knowledge_base_id, payload)


@router.get("", response_model=list[WorkflowListItem])
def list_recent(workspace_id: UUID, knowledge_base_id: UUID, session: DbSession,
                limit: Annotated[int, Query(ge=1, le=100)] = 20,
                offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
                workflow_status: Annotated[WorkflowStatus | None, Query(alias="status")] = None) -> list[WorkflowListItem]:
    if workflow_status is None:
        return _call(list_workflows, session, workspace_id, knowledge_base_id, limit, offset)
    return _call(list_workflows, session, workspace_id, knowledge_base_id, limit, offset, workflow_status)


@router.get("/{workflow_id}", response_model=WorkflowRead)
def read(workspace_id: UUID, knowledge_base_id: UUID, workflow_id: UUID, session: DbSession) -> WorkflowRead:
    return _call(read_workflow, session, workspace_id, knowledge_base_id, workflow_id)


@router.post("/{workflow_id}/execute", response_model=WorkflowExecutionRead)
def execute(workspace_id: UUID, knowledge_base_id: UUID, workflow_id: UUID, payload: WorkflowExecute,
            session: DbSession, embedding: EmbeddingDep, generation: GenerationDep) -> WorkflowExecutionRead:
    return _call(execute_workflow, session, embedding, generation, workspace_id, knowledge_base_id,
                 workflow_id, payload)
