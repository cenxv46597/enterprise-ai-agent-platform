"""短事务读取、事务外决策、版本条件写回；等待与计算任务完成分别表达。"""

from collections.abc import Callable, Sequence
import hashlib
import json
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.contracts import AgentRun
from app.billing.workflow import billing_reply, execute_billing
from app.conversations.contracts import (
    MAX_TURNS, MEMORY_CHARACTERS, MEMORY_TURNS, ConversationConflictError, ConversationNotFoundError,
    ConversationRead, ConversationStateError, MessageRead, MessageRequest, PendingTask, RecentMessage,
    TaskRead, UserCount,
)
from app.conversations.counts import parse_count
from app.db.models import Chunk, Conversation, ConversationTurn, Document, KnowledgeBase


Runner = Callable[[str, dict], AgentRun]
RUN_ADAPTER = TypeAdapter(AgentRun)


def recent_messages(turns: Sequence[tuple[str, str]]) -> list[RecentMessage]:
    """按最新完整问答对选择，再恢复时间顺序；正文预算与轮数分别有界。"""
    chosen, remaining = [], MEMORY_CHARACTERS
    for content, reply in list(turns)[-MEMORY_TURNS:][::-1]:
        size = len(content) + len(reply)
        if size > remaining:
            break  # 保留连续的最近历史，不拿更旧的片段替代。
        chosen.append((content, reply))
        remaining -= size
    return [RecentMessage(role=role, content=text)
            for content, reply in reversed(chosen)
            for role, text in (("user", content), ("assistant", reply))]


def public_task(task: PendingTask | None) -> TaskRead | None:
    if task is None:
        return None
    return TaskRead(question=task.question, clarification=task.clarification,
                    confirmed_inputs=task.confirmed_inputs,
                    evidence=[item.model_dump(exclude={"content"}) for item in task.evidence],
                    clarifications=task.clarifications, billing_plan=task.billing_plan)


def _scope(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> None:
    found = session.scalar(select(KnowledgeBase.id).where(
        KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == workspace_id,
    ).with_for_update(read=True))
    if found is None:
        raise ConversationNotFoundError("知识库不存在")


def _conversation(session: Session, workspace_id: UUID, knowledge_base_id: UUID,
                  conversation_id: UUID, *, lock: bool = False) -> Conversation:
    _scope(session, workspace_id, knowledge_base_id)
    statement = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.knowledge_base_id == knowledge_base_id,
    ).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update(read=True)
    conversation = session.scalar(statement)
    if conversation is None:
        raise ConversationNotFoundError("会话不存在")
    return conversation


def _task(conversation: Conversation) -> PendingTask | None:
    try:
        task = (PendingTask.model_validate_json(json.dumps(conversation.task, ensure_ascii=False))
                if conversation.task is not None else None)
        if (conversation.status == "idle") != (task is None):
            raise ValueError
        if task is not None:
            if conversation.status == "waiting_for_user" and (task.inputs_complete() or task.billing_result is not None):
                raise ValueError
            if conversation.status == "ready" and (not task.inputs_complete() or task.billing_result is not None):
                raise ValueError
            if conversation.status == "completed" and (task.billing_plan is None or task.billing_result is None):
                raise ValueError
            if conversation.status not in ("waiting_for_user", "ready", "completed"):
                raise ValueError
        return task
    except (ValidationError, ValueError, TypeError):
        raise ConversationStateError("会话状态无效") from None


def _recent(session: Session, conversation_id: UUID) -> list[RecentMessage]:
    rows = session.execute(select(ConversationTurn.content, ConversationTurn.reply, ConversationTurn.mode).where(
        ConversationTurn.conversation_id == conversation_id,
    ).order_by(ConversationTurn.version.desc()).limit(MEMORY_TURNS)).all()
    ordered = list(reversed(rows))
    last_new_task = next((index for index in range(len(ordered) - 1, -1, -1)
                          if ordered[index].mode == "new_task"), 0)
    return recent_messages([(row.content, row.reply) for row in ordered[last_new_task:]])


def create_conversation(session: Session, workspace_id: UUID, knowledge_base_id: UUID) -> ConversationRead:
    with session.begin():
        _scope(session, workspace_id, knowledge_base_id)
        conversation = Conversation(knowledge_base_id=knowledge_base_id)
        session.add(conversation)
        session.flush()
        return ConversationRead(conversation_id=conversation.id, version=0, status="idle", task=None,
                                recent_messages=[])


def read_conversation(session: Session, workspace_id: UUID, knowledge_base_id: UUID,
                      conversation_id: UUID) -> ConversationRead:
    with session.begin():
        conversation = _conversation(session, workspace_id, knowledge_base_id, conversation_id, lock=True)
        task = _task(conversation)
        return ConversationRead(conversation_id=conversation.id, version=conversation.version,
                                status=conversation.status, task=public_task(task),
                                recent_messages=_recent(session, conversation_id),
                                billing_result=task.billing_result if task else None)


def _digest(payload: MessageRequest) -> str:
    data = payload.model_dump(mode="json")
    # 未确认的旧请求保持阶段4.1/4.2摘要算法；显式确认则必须参与幂等校验。
    if not data["confirm_billing_rule"]:
        data.pop("confirm_billing_rule")
    return hashlib.sha256(json.dumps(data, sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _replay(session: Session, conversation_id: UUID, payload: MessageRequest, digest: str) -> MessageRead | None:
    turn = session.scalar(select(ConversationTurn).where(
        ConversationTurn.conversation_id == conversation_id, ConversationTurn.request_id == payload.request_id,
    ))
    if turn is None:
        return None
    if turn.request_digest != digest:
        raise ConversationConflictError("request_id 已用于不同请求")
    try:
        return MessageRead.model_validate_json(json.dumps(turn.response, ensure_ascii=False))
    except (ValidationError, ValueError, TypeError):
        raise ConversationStateError("已保存的会话回复无效") from None


def _validate_evidence(session: Session, knowledge_base_id: UUID, task: PendingTask) -> None:
    """写入待补快照前核对来源仍属于该知识库，且正文未被替换。"""
    rows = session.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(
        Chunk.id.in_([item.chunk_id for item in task.evidence]), Document.knowledge_base_id == knowledge_base_id,
    ).with_for_update(read=True)).all()
    current = {chunk.id: (chunk, document) for chunk, document in rows}
    for snapshot in task.evidence:
        if snapshot.chunk_id not in current:
            raise ConversationConflictError("待补条件原文已经变化，请重新开始任务")
        chunk, document = current[snapshot.chunk_id]
        if (chunk.document_id, document.original_filename, chunk.chunk_index, chunk.content, chunk.start_char,
            chunk.end_char) != (snapshot.document_id, snapshot.document_name, snapshot.chunk_index,
                                snapshot.content, snapshot.start_char, snapshot.end_char):
            raise ConversationConflictError("待补条件原文已经变化，请重新开始任务")


def send_message(session: Session, workspace_id: UUID, knowledge_base_id: UUID, conversation_id: UUID,
                 payload: MessageRequest, runner: Runner) -> MessageRead:
    digest = _digest(payload)
    with session.begin():
        conversation = _conversation(session, workspace_id, knowledge_base_id, conversation_id, lock=True)
        replay = _replay(session, conversation_id, payload, digest)
        if replay is not None:
            return replay
        version, status, task = conversation.version, conversation.status, _task(conversation)
        if payload.expected_version != version:
            raise ConversationConflictError("会话版本已变化，请读取最新状态")
        if version >= MAX_TURNS:
            raise ConversationConflictError("会话达到轮次上限，请创建新会话")
        memory = _recent(session, conversation_id)
        if status == "completed" and payload.mode != "new_task":
            raise ConversationConflictError("标准估算已经完成；修改条件请用 new_task 开始新任务")
        if payload.mode == "execute":
            if status != "ready" or task is None:
                raise ConversationConflictError("仅 ready 状态可显式执行已确认输入")
            if task.billing_plan is not None and not payload.confirm_billing_rule:
                raise ConversationConflictError("请确认所选标准收费规则适用于已补充的同一计费期间，再显式执行")
            if task.billing_plan is None and payload.confirm_billing_rule:
                raise ConversationConflictError("旧算术任务不能借确认标志升级为标准计费任务")
            _validate_evidence(session, knowledge_base_id, task)

    run = None
    deferred = None
    if payload.mode == "execute":
        assert task is not None
        if task.billing_plan is not None:
            try:
                billing_result = execute_billing(task, payload.request_id)
                task = PendingTask(**{**task.model_dump(), "billing_result": billing_result})
            except (ValidationError, ValueError, TypeError):
                raise ConversationStateError("标准计费状态无效，未写入计算结果") from None
            status, reply = "completed", billing_reply(billing_result)
        else:
            # 旧来源绑定算术仍不代表业务任务完成，也不能静默升级公式。
            context = {"recent_messages": [item.model_dump() for item in memory],
                       "resume_task": task.model_dump(mode="json")}
            result = runner(task.question, context)
            if result.pending_task is not None:
                raise ConversationStateError("算术执行不能替换已有待补任务")
            run = RUN_ADAPTER.dump_python(result, mode="json", exclude={"pending_task"}, warnings=False)
            reply = (result.result.answer if result.result is not None else
                     f"本轮算术运行{result.status}，原因：{result.stop_reason}。已确认输入保留，可显式再次执行。")
    elif payload.mode == "continue" and status == "ready":
        raise ConversationConflictError("输入已确认；请用 execute 显式执行，或用 new_task 开始其他任务")
    elif payload.mode == "continue" and status == "waiting_for_user":
        assert task is not None
        count = parse_count(payload.content, task.clarification.unit)
        requirement = task.clarification
        if count is None or not requirement.min_value <= count <= requirement.max_value:
            reply = (f"仍需补充{requirement.label}，请只回复 {requirement.min_value} 到 {requirement.max_value} 的整数，"
                     f"可带单位“{requirement.unit}”；切换问题请使用 new_task。")
        else:
            user_input = UserCount(field_name=task.clarification.field_name, value=count,
                                   unit=task.clarification.unit, request_id=payload.request_id, raw_text=payload.content)
            confirmed = [*task.confirmed_inputs, user_input]
            remaining = [item for item in task.requirements() if item.field_name not in {value.field_name for value in confirmed}]
            task = PendingTask(**{**task.model_dump(), "confirmed_inputs": confirmed,
                                  "clarification": remaining[0] if remaining else task.clarification})
            reply = f"已将“{payload.content}”保存为{requirement.label}：{count}{requirement.unit}。"
            if remaining:
                next_item = remaining[0]
                reply += (f"请补充{next_item.label}（{next_item.min_value} 到 {next_item.max_value}，单位：{next_item.unit}）；"
                          "不会根据‘全年’自动假设计费12个月。")
            else:
                status, deferred = "ready", "explicit_execution_required"
                if task.billing_plan is not None:
                    values = {item.field_name: item.value for item in task.confirmed_inputs}
                    reply += (f"已确认同一期间 {values['billing_months']} 个月、{values['visit_count']} 次。"
                              f"规则原文：“{task.billing_plan.rule_quote}”。请核对该标准规则是否适用；"
                              "确认后用 mode=execute、confirm_billing_rule=true 显式计算标准规则估算，暂未执行总额计算。"
                              "本流程不处理优惠、套餐、税费等其他条款。")
                else:
                    reply += ("原问题已恢复，输入已确认；可用 execute 显式执行来源绑定算术，暂未执行总额计算。"
                              "计费期间、单位和业务适用条件仍需另行验证。")
    else:
        # 显式新任务不携带旧任务的对话上下文，防止隐式串用条件。
        context = {"recent_messages": [item.model_dump() for item in memory] if payload.mode == "continue" else []}
        result = runner(payload.content, context)  # 此处没有活动数据库事务。
        run = RUN_ADAPTER.dump_python(result, mode="json", exclude={"pending_task"}, warnings=False)
        task = result.pending_task
        status = "waiting_for_user" if result.status == "waiting_for_user" else "idle"
        if (status == "waiting_for_user") != (task is not None):
            raise ConversationStateError("运行待补条件无效")
        if task is not None:
            reply = (f"规则原文：“{task.clarification.rule_quote}”。为了继续处理“{task.question}”，"
                     f"请补充{task.clarification.label}（单位：{task.clarification.unit}，"
                     f"{task.clarification.min_value} 到 {task.clarification.max_value} 的整数）。")
        else:
            reply = result.result.answer if result.result is not None else f"本轮运行{result.status}，原因：{result.stop_reason}。"

    response = MessageRead(conversation_id=conversation_id, request_id=payload.request_id, version=version + 1,
                           status=status, reply=reply, task=public_task(task), execution_deferred=deferred, run=run,
                           billing_result=task.billing_result if task else None)
    with session.begin():
        _conversation(session, workspace_id, knowledge_base_id, conversation_id)
        replay = _replay(session, conversation_id, payload, digest)
        if replay is not None:
            return replay
        updated = session.execute(update(Conversation).where(
            Conversation.id == conversation_id, Conversation.version == version,
        ).values(version=version + 1, status=status, task=task.model_dump(mode="json") if task else None))
        if updated.rowcount != 1:
            # 同编号并发请求在另一个事务提交后可能刚刚可见。
            replay = _replay(session, conversation_id, payload, digest)
            if replay is not None:
                return replay
            raise ConversationConflictError("会话版本已变化，本次结果没有写入")
        if task is not None:
            _validate_evidence(session, knowledge_base_id, task)
        session.add(ConversationTurn(conversation_id=conversation_id, request_id=payload.request_id,
                                     request_digest=digest, version=version + 1, content=payload.content,
                                     mode=payload.mode, reply=reply, response=response.model_dump(mode="json")))
        session.flush()
    return response
