"""显式设置或清除知识库回答偏好；当前并非多用户授权接口。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.workspaces import DbSession
from app.memory.contracts import (
    PreferenceConflictError, PreferenceNotFoundError, PreferenceRead,
    PreferenceStateError, PreferenceUpdate,
)
from app.memory.service import read_preference, set_preference


router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/answer-preferences",
    tags=["answer-preferences"],
)
ERRORS = (PreferenceNotFoundError, PreferenceConflictError, PreferenceStateError)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, PreferenceNotFoundError):
        return HTTPException(404, "知识库不存在")
    if isinstance(error, PreferenceConflictError):
        return HTTPException(409, "回答偏好版本已变化，请读取最新设置")
    return HTTPException(500, "回答偏好状态异常")


@router.get("", response_model=PreferenceRead)
def read(workspace_id: UUID, knowledge_base_id: UUID, session: DbSession) -> PreferenceRead:
    try:
        return read_preference(session, workspace_id, knowledge_base_id)
    except ERRORS as error:
        raise _http_error(error) from None


@router.put("", response_model=PreferenceRead)
def write(workspace_id: UUID, knowledge_base_id: UUID, payload: PreferenceUpdate,
          session: DbSession) -> PreferenceRead:
    try:
        return set_preference(session, workspace_id, knowledge_base_id, payload)
    except ERRORS as error:
        raise _http_error(error) from None
