"""短事务领取与提交检查点；模型调用在事务外，旧执行者不能覆盖新结果。"""

from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import AnswerWorkflow, AnswerWorkflowAttempt, Chunk, Document, KnowledgeBase
from app.knowledge.answers import answer_from_hits
from app.knowledge.context import AnswerContextLimitError, select_context
from app.knowledge.retrieval import (
    RetrievalConflictError, RetrievalInputError, RetrievalNotFoundError, RetrievalStateError,
    search_knowledge_base,
)
from app.providers.embeddings import EmbeddingError, EmbeddingProvider, EmbeddingTimeoutError
from app.providers.generation import (
    GenerationConfigurationError, GenerationError, GenerationProvider, GenerationTimeoutError,
)
from app.workflows.contracts import (
    MAX_CHECKPOINT_BYTES, MAX_CHECKPOINT_CHARACTERS, MAX_WORKFLOW_ATTEMPTS, MAX_WORKFLOW_VERSION,
    RETRYABLE_ERRORS, WORKFLOW_LEASE_SECONDS, WorkflowAnswer, WorkflowCheckpoint, WorkflowConflictError,
    WorkflowCreate, WorkflowErrorCode, WorkflowEvidence, WorkflowExecute, WorkflowExecutionRead,
    WorkflowListItem, WorkflowNotFoundError, WorkflowRead, WorkflowStateError,
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _digest(payload: BaseModel) -> str:
    return sha256(_json(payload.model_dump(mode="json", exclude={"request_id"})).encode("utf-8")).hexdigest()


def _checkpoint(data) -> WorkflowCheckpoint:
    try:
        encoded = _json(data)
        if len(encoded.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ValueError("检查点序列化超限")
        return WorkflowCheckpoint.model_validate_json(encoded)
    except (TypeError, ValueError):
        raise WorkflowStateError("checkpoint_invalid") from None


def _scope(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> None:
    found = session.scalar(select(KnowledgeBase.id).where(
        KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == workspace_id,
    ).with_for_update(read=True))
    if found is None:
        raise WorkflowNotFoundError("知识库不存在")


def _workflow(session: Session, workspace_id: UUID, knowledge_base_id: UUID, workflow_id: UUID,
              *, lock: bool = False) -> AnswerWorkflow:
    _scope(session, workspace_id, knowledge_base_id)
    statement = select(AnswerWorkflow).where(
        AnswerWorkflow.id == workflow_id, AnswerWorkflow.knowledge_base_id == knowledge_base_id,
    ).execution_options(populate_existing=True)
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise WorkflowNotFoundError("工作流不存在")
    return row


def _view(row: AnswerWorkflow, workspace_id: UUID) -> WorkflowRead:
    try:
        checkpoint = _checkpoint(row.checkpoint) if row.checkpoint is not None else None
        result = WorkflowAnswer.model_validate_json(_json(row.result)) if row.result is not None else None
        return WorkflowRead(
            workflow_id=row.id, workspace_id=workspace_id, knowledge_base_id=row.knowledge_base_id,
            question=row.question, strategy=row.strategy, max_context_characters=row.max_context_characters,
            status=row.status, next_step=row.next_step, version=row.version, attempt_count=row.attempt_count,
            checkpoint=checkpoint, error_code=row.error_code,
            retryable=(row.status == "failed" and row.error_code in RETRYABLE_ERRORS
                       and row.attempt_count < MAX_WORKFLOW_ATTEMPTS),
            active_request_id=row.active_request_id, lease_expires_at=row.lease_expires_at, result=result,
        )
    except (WorkflowStateError, TypeError, ValueError):
        raise WorkflowConflictError("工作流保存状态无效，请重新创建") from None


def read_workflow(session: Session, workspace_id: UUID, knowledge_base_id: UUID,
                  workflow_id: UUID) -> WorkflowRead:
    with session.begin():
        return _view(_workflow(session, workspace_id, knowledge_base_id, workflow_id), workspace_id)


def list_workflows(session: Session, workspace_id: UUID, knowledge_base_id: UUID,
                   limit: int = 20, offset: int = 0) -> list[WorkflowListItem]:
    # 不加载 ORM 实体，避免为历史列表读取大段检查点、答案或内部请求信息。
    statement = select(
        AnswerWorkflow.id.label("workflow_id"), AnswerWorkflow.knowledge_base_id,
        AnswerWorkflow.question, AnswerWorkflow.strategy, AnswerWorkflow.max_context_characters,
        AnswerWorkflow.status, AnswerWorkflow.next_step, AnswerWorkflow.version,
        AnswerWorkflow.attempt_count, AnswerWorkflow.error_code,
        AnswerWorkflow.created_at, AnswerWorkflow.updated_at,
    ).where(AnswerWorkflow.knowledge_base_id == knowledge_base_id).order_by(
        AnswerWorkflow.created_at.desc(), AnswerWorkflow.id.desc(),
    ).limit(limit).offset(offset)
    with session.begin():
        _scope(session, workspace_id, knowledge_base_id)
        try:
            return [WorkflowListItem(
                **row, workspace_id=workspace_id,
                retryable=(row["status"] == "failed" and row["error_code"] in RETRYABLE_ERRORS
                           and row["attempt_count"] < MAX_WORKFLOW_ATTEMPTS),
            ) for row in session.execute(statement).mappings()]
        except (TypeError, ValueError):
            raise WorkflowConflictError("工作流保存状态无效，请重新读取") from None


def create_workflow(session: Session, workspace_id: UUID, knowledge_base_id: UUID,
                    payload: WorkflowCreate) -> WorkflowRead:
    # 内部调用也不能利用 model_construct 绕过输入校验。
    payload = WorkflowCreate.model_validate_json(payload.model_dump_json())
    digest = _digest(payload)
    try:
        with session.begin():
            _scope(session, workspace_id, knowledge_base_id)
            row = session.scalar(select(AnswerWorkflow).where(
                AnswerWorkflow.knowledge_base_id == knowledge_base_id,
                AnswerWorkflow.creation_request_id == payload.request_id,
            ).execution_options(populate_existing=True))
            if row is not None:
                if row.creation_digest != digest:
                    raise WorkflowConflictError("创建请求编号已用于不同内容")
                return _view(row, workspace_id)
            row = AnswerWorkflow(
                knowledge_base_id=knowledge_base_id, creation_request_id=payload.request_id,
                creation_digest=digest, question=payload.question, strategy=payload.strategy,
                max_context_characters=min(Settings().generation_max_context_characters, MAX_CHECKPOINT_CHARACTERS),
            )
            session.add(row)
            session.flush()
            return _view(row, workspace_id)
    except IntegrityError:
        # 唯一约束兜底并发创建；不能把任意完整性错误一概当成功。
        with session.begin():
            _scope(session, workspace_id, knowledge_base_id)
            row = session.scalar(select(AnswerWorkflow).where(
                AnswerWorkflow.knowledge_base_id == knowledge_base_id,
                AnswerWorkflow.creation_request_id == payload.request_id,
            ).execution_options(populate_existing=True))
            if row is None or row.creation_digest != digest:
                raise WorkflowConflictError("创建请求冲突") from None
            return _view(row, workspace_id)


def _attempt(session: Session, workflow_id: UUID, request_id: UUID) -> AnswerWorkflowAttempt | None:
    return session.scalar(select(AnswerWorkflowAttempt).where(
        AnswerWorkflowAttempt.workflow_id == workflow_id, AnswerWorkflowAttempt.request_id == request_id,
    ).execution_options(populate_existing=True))


def _receipt(row: AnswerWorkflowAttempt, workspace_id: UUID, knowledge_base_id: UUID) -> WorkflowExecutionRead:
    try:
        result = WorkflowExecutionRead.model_validate_json(_json(row.response))
        if (result.request_id != row.request_id or result.attempt_number != row.attempt_number
                or result.status != row.status or result.workflow.workflow_id != row.workflow_id
                or result.workflow.workspace_id != workspace_id
                or result.workflow.knowledge_base_id != knowledge_base_id):
            raise ValueError("回执身份不一致")
        return result
    except (TypeError, ValueError):
        raise WorkflowConflictError("工作流执行回执无效") from None


def _close_attempt(session: Session, row: AnswerWorkflow, attempt: AnswerWorkflowAttempt,
                   workspace_id: UUID, *, interrupted: bool = False) -> WorkflowExecutionRead:
    result = WorkflowExecutionRead(
        request_id=attempt.request_id, attempt_number=attempt.attempt_number,
        status="interrupted" if interrupted else row.status, workflow=_view(row, workspace_id),
    )
    # 先取数据库时间，再一次性填齐终态字段，避免未来加入查询时暴露半个回执。
    finished_at = session.scalar(select(func.clock_timestamp()))
    attempt.status, attempt.response, attempt.finished_at = result.status, result.model_dump(mode="json"), finished_at
    return result


def _fail_row(row: AnswerWorkflow, code: WorkflowErrorCode) -> None:
    row.status, row.error_code = "failed", code
    row.active_request_id, row.lease_expires_at = None, None
    row.version += 1
    # 坏/陈旧检查点不能再公开或用作生成输入；需要重新创建，不静默重新检索。
    if code in {"checkpoint_invalid", "evidence_changed"}:
        row.checkpoint, row.next_step = None, "retrieve"


def _claim(session: Session, workspace_id: UUID, knowledge_base_id: UUID, workflow_id: UUID,
           payload: WorkflowExecute) -> WorkflowRead | WorkflowExecutionRead:
    with session.begin():
        row = _workflow(session, workspace_id, knowledge_base_id, workflow_id, lock=True)
        previous = _attempt(session, workflow_id, payload.request_id)
        digest = _digest(payload)
        if previous is not None:
            if previous.request_digest != digest:
                raise WorkflowConflictError("执行请求编号已用于不同内容")
            if previous.response is not None:
                return _receipt(previous, workspace_id, knowledge_base_id)
            raise WorkflowConflictError("该请求仍在执行或结果未知；恢复需使用新的请求编号")
        if row.version != payload.expected_version:
            raise WorkflowConflictError("工作流版本冲突，请重新读取")
        if row.attempt_count >= MAX_WORKFLOW_ATTEMPTS or row.version > MAX_WORKFLOW_VERSION - 4:
            raise WorkflowConflictError("已达到执行尝试上限，请重新创建工作流")
        if row.status == "completed":
            raise WorkflowConflictError("工作流已经完成，请读取结果或重放原请求")
        now = session.scalar(select(func.clock_timestamp()))
        broken_checkpoint = False
        if row.status == "running":
            if row.lease_expires_at is None or row.lease_expires_at > now or not payload.recover_interrupted:
                raise WorkflowConflictError("工作流仍在执行；租约到期后方可显式恢复")
            old = _attempt(session, workflow_id, row.active_request_id)
            if old is None or old.status != "running" or old.response is not None:
                raise WorkflowConflictError("中断执行记录无效")
            _fail_row(row, "execution_interrupted")
            try:
                _view(row, workspace_id)
            except WorkflowConflictError:
                # 先规范化损坏检查点，才能保存中断回执；新尝试也明确失败而不发模型请求。
                row.checkpoint, row.next_step, row.error_code = None, "retrieve", "checkpoint_invalid"
                broken_checkpoint = True
            _close_attempt(session, row, old, workspace_id, interrupted=True)
        elif payload.recover_interrupted:
            raise WorkflowConflictError("仅租约到期的执行中工作流可使用中断恢复")
        elif row.status == "failed" and row.error_code not in RETRYABLE_ERRORS:
            raise WorkflowConflictError("该故障不能直接重试，请重新创建工作流")
        row.status, row.error_code = "running", None
        row.version += 1
        row.attempt_count += 1
        row.active_request_id = payload.request_id
        row.lease_expires_at = now + timedelta(seconds=WORKFLOW_LEASE_SECONDS)
        session.add(AnswerWorkflowAttempt(
            workflow_id=workflow_id, request_id=payload.request_id, request_digest=digest,
            attempt_number=row.attempt_count,
        ))
        session.flush()
        if broken_checkpoint:
            _fail_row(row, "checkpoint_invalid")
            attempt = _attempt(session, workflow_id, payload.request_id)
            return _close_attempt(session, row, attempt, workspace_id)
        # 领取时不解析可能损坏的检查点，执行阶段将其安全标记为不可重试。
        try:
            return _view(row, workspace_id)
        except WorkflowConflictError:
            _fail_row(row, "checkpoint_invalid")
            attempt = _attempt(session, workflow_id, payload.request_id)
            return _close_attempt(session, row, attempt, workspace_id)


def _owned(session: Session, workspace_id: UUID, knowledge_base_id: UUID, state: WorkflowRead) -> AnswerWorkflow:
    row = _workflow(session, workspace_id, knowledge_base_id, state.workflow_id, lock=True)
    if (row.status != "running" or row.active_request_id != state.active_request_id or row.version != state.version
            or row.lease_expires_at is None or row.lease_expires_at <= session.scalar(select(func.clock_timestamp()))):
        raise WorkflowConflictError("执行权已失效，旧执行者不能写回，请重新读取")
    return row


def _verify(session: Session, checkpoint: WorkflowCheckpoint, workspace_id: UUID,
            knowledge_base_id: UUID, strategy: str) -> None:
    if (checkpoint.workspace_id != workspace_id or checkpoint.knowledge_base_id != knowledge_base_id
            or checkpoint.strategy != strategy):
        raise WorkflowStateError("evidence_changed")
    _scope(session, workspace_id, knowledge_base_id)
    rows = session.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(
        Chunk.id.in_([hit.chunk_id for hit in checkpoint.hits]), Document.knowledge_base_id == knowledge_base_id,
        Document.status == "ready", Chunk.embedding.is_not(None),
    ).execution_options(populate_existing=True).with_for_update(read=True)).all() if checkpoint.hits else []
    current = {chunk.id: (chunk, document) for chunk, document in rows}
    for hit in checkpoint.hits:
        pair = current.get(hit.chunk_id)
        if pair is None:
            raise WorkflowStateError("evidence_changed")
        chunk, document = pair
        if (document.id, document.original_filename, chunk.chunk_index, chunk.content, chunk.start_char, chunk.end_char) != (
            hit.document_id, hit.document_name, hit.chunk_index, hit.content, hit.start_char, hit.end_char
        ):
            raise WorkflowStateError("evidence_changed")


def _save_checkpoint(session: Session, state: WorkflowRead, checkpoint: WorkflowCheckpoint) -> WorkflowRead:
    with session.begin():
        row = _owned(session, state.workspace_id, state.knowledge_base_id, state)
        _verify(session, checkpoint, state.workspace_id, state.knowledge_base_id, state.strategy)
        # 原文锁也可能等待；不能只使用等待前的租约检查结果。
        row = _owned(session, state.workspace_id, state.knowledge_base_id, state)
        row.checkpoint = checkpoint.model_dump(mode="json")
        row.next_step, row.version = "generate", row.version + 1
        return _view(row, state.workspace_id)


def _finish(session: Session, state: WorkflowRead, *, result: WorkflowAnswer | None = None,
            error_code: WorkflowErrorCode | None = None) -> WorkflowExecutionRead:
    with session.begin():
        row = _owned(session, state.workspace_id, state.knowledge_base_id, state)
        if result is not None:
            checkpoint = _checkpoint(row.checkpoint)
            _verify(session, checkpoint, state.workspace_id, state.knowledge_base_id, state.strategy)
            row = _owned(session, state.workspace_id, state.knowledge_base_id, state)
            row.status, row.next_step, row.result = "completed", "done", result.model_dump(mode="json")
            row.active_request_id, row.lease_expires_at = None, None
            row.version += 1
        else:
            _fail_row(row, error_code or "service_error")
        attempt = _attempt(session, row.id, state.active_request_id)
        if attempt is None or attempt.status != "running":
            raise WorkflowConflictError("执行记录已变化")
        return _close_attempt(session, row, attempt, state.workspace_id)


def _error_code(error: Exception) -> WorkflowErrorCode:
    if isinstance(error, WorkflowStateError):
        return error.code
    if isinstance(error, EmbeddingTimeoutError):
        return "embedding_timeout"
    if isinstance(error, EmbeddingError):
        return "embedding_error"
    if isinstance(error, GenerationConfigurationError):
        return "generation_configuration"
    if isinstance(error, GenerationTimeoutError):
        return "generation_timeout"
    if isinstance(error, GenerationError):
        return "generation_error"
    if isinstance(error, AnswerContextLimitError):
        return "context_limit"
    if isinstance(error, RetrievalConflictError):
        return "retrieval_conflict"
    if isinstance(error, (RetrievalInputError, RetrievalStateError)):
        return "retrieval_invalid"
    return "service_error"


def execute_workflow(session: Session, embedding: EmbeddingProvider, generation: GenerationProvider,
                     workspace_id: UUID, knowledge_base_id: UUID, workflow_id: UUID,
                     payload: WorkflowExecute) -> WorkflowExecutionRead:
    payload = WorkflowExecute.model_validate_json(payload.model_dump_json())
    state = _claim(session, workspace_id, knowledge_base_id, workflow_id, payload)
    if isinstance(state, WorkflowExecutionRead):
        return state
    try:
        if state.next_step == "retrieve":
            search = search_knowledge_base(session, embedding, workspace_id, knowledge_base_id,
                                           state.question, top_k=5, strategy=state.strategy)
            selected = select_context(search.results, state.max_context_characters) if search.results else []
            checkpoint = _checkpoint(WorkflowCheckpoint(
                workspace_id=workspace_id, knowledge_base_id=knowledge_base_id, strategy=state.strategy,
                hits=[WorkflowEvidence.from_hit(hit) for hit in selected],
            ).model_dump(mode="json"))
            state = _save_checkpoint(session, state, checkpoint)
        with session.begin():
            row = _owned(session, workspace_id, knowledge_base_id, state)
            checkpoint = _checkpoint(row.checkpoint)
            _verify(session, checkpoint, workspace_id, knowledge_base_id, state.strategy)
            _owned(session, workspace_id, knowledge_base_id, state)
        # 包含生成和引用核验的一步；期间既不持有数据库事务，也不占有行锁。
        answer = answer_from_hits(generation, state.question, [hit.to_hit() for hit in checkpoint.hits],
                                  max_context_characters=state.max_context_characters)
        result = WorkflowAnswer.model_validate(answer)
        return _finish(session, state, result=result)
    except RetrievalNotFoundError:
        raise WorkflowNotFoundError("知识库不存在") from None
    except (WorkflowConflictError, WorkflowNotFoundError):
        raise
    except SQLAlchemyError:
        # 数据库提交可能已经成功但确认丢失；不使用旧版本继续尝试写“失败”。
        # API 返回固定 503，调用方读取当前状态，再重放或显式恢复。
        raise
    except Exception as error:
        # 回执不保存第三方异常正文、请求配置或堆栈。
        return _finish(session, state, error_code=_error_code(error))
