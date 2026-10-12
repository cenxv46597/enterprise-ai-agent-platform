"""本地单用户原型的知识库偏好；短事务读取及版本条件更新。"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import AnswerPreference, KnowledgeBase
from app.memory.contracts import (
    PreferenceConflictError, PreferenceNotFoundError, PreferenceRead,
    PreferenceStateError, PreferenceUpdate,
)


def _scope(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> None:
    found = session.scalar(select(KnowledgeBase.id).where(
        KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == workspace_id,
    ).with_for_update(read=True))
    if found is None:
        raise PreferenceNotFoundError("知识库不存在")


def load_preference(session: Session, knowledge_base_id: UUID) -> PreferenceRead:
    """复用调用方事务；调用方先校验知识库作用域，不在此嵌套 begin。"""
    row = session.execute(select(AnswerPreference.knowledge_base_id, AnswerPreference.version,
                                 AnswerPreference.answer_detail).where(
        AnswerPreference.knowledge_base_id == knowledge_base_id,
    )).one_or_none()
    if row is None:
        return PreferenceRead(knowledge_base_id=knowledge_base_id, version=0, answer_detail=None)
    try:
        # 数据库中的版本从 1 开始，0 只表示确实不存在的一行。
        if row.version == 0:
            raise ValueError
        return PreferenceRead(knowledge_base_id=row.knowledge_base_id, version=row.version,
                              answer_detail=row.answer_detail)
    except (ValidationError, ValueError, TypeError):
        raise PreferenceStateError("回答偏好状态无效") from None


def read_preference(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> PreferenceRead:
    with session.begin():
        _scope(session, workspace_id, knowledge_base_id)
        return load_preference(session, knowledge_base_id)


def set_preference(session: Session, workspace_id: UUID, knowledge_base_id: UUID,
                   payload: PreferenceUpdate) -> PreferenceRead:
    with session.begin():
        _scope(session, workspace_id, knowledge_base_id)
        # 防止内部调用用 model_construct 绕过 API 的严格类型和版本上限。
        try:
            payload = PreferenceUpdate.model_validate(payload.model_dump(), strict=True)
        except (ValidationError, ValueError, TypeError, AttributeError):
            raise PreferenceStateError("回答偏好更新无效") from None
        if payload.expected_version == 0:
            statement = insert(AnswerPreference).values(
                knowledge_base_id=knowledge_base_id, version=1, answer_detail=payload.answer_detail,
            ).on_conflict_do_nothing(index_elements=[AnswerPreference.knowledge_base_id]).returning(
                AnswerPreference.knowledge_base_id,
            )
        else:
            statement = update(AnswerPreference).where(
                AnswerPreference.knowledge_base_id == knowledge_base_id,
                AnswerPreference.version == payload.expected_version,
            ).values(version=payload.expected_version + 1, answer_detail=payload.answer_detail).returning(
                AnswerPreference.knowledge_base_id,
            )
        if session.scalar(statement) is None:
            raise PreferenceConflictError("回答偏好版本已变化，请读取最新设置")
        # null 不删除行：清除后仍保存新版本，旧请求不能把已清除的偏好复活。
        return load_preference(session, knowledge_base_id)
