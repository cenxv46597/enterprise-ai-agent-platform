"""知识库内的会话创建、读取与跨请求条件补充。"""

import json
from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.agents.calculator import CalculatorTool
from app.agents.sourced_calculator import SourcedCalculatorTool
from app.agents.clarification import ClarificationTool
from app.billing.preparation import PrepareBillingTool
from app.agents.engine import run_agent
from app.agents.tools import KnowledgeSearchTool, ToolRegistry
from app.api.agent_runs import PlannerDep
from app.api.answers import GenerationDep
from app.api.documents import EmbeddingDep
from app.api.workspaces import DbSession
from app.conversations.contracts import (
    ConversationConflictError, ConversationNotFoundError, ConversationRead, ConversationStateError,
    MessageRead, MessageRequest, PendingTask,
)
from app.conversations.service import create_conversation, read_conversation, send_message
from app.core.config import Settings
from app.knowledge.retrieval import RetrievalConflictError, RetrievalNotFoundError, RetrievalStateError
from app.agents.contracts import MAX_AGENT_CHARACTERS, PlannerConfigurationError
from app.knowledge.retrieval import SearchHit
from app.agents.contracts import AgentInputError
from app.providers.generation import GenerationConfigurationError
from app.memory.contracts import PreferenceRead, PreferenceStateError
from app.memory.generation import PreferenceGenerationProvider


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/conversations",
                   tags=["conversations"])


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, (ConversationNotFoundError, RetrievalNotFoundError)):
        return HTTPException(404, "会话或知识库不存在")
    if isinstance(error, ConversationConflictError):
        return HTTPException(409, str(error))
    if isinstance(error, RetrievalConflictError):
        return HTTPException(409, "查询模型与知识库向量配置不一致")
    if isinstance(error, AgentInputError):
        return HTTPException(409, "恢复运行的输入或原文超出预算")
    if isinstance(error, (PlannerConfigurationError, GenerationConfigurationError)):
        return HTTPException(503, "请检查智能体及生成服务配置")
    return HTTPException(500, "会话或知识数据状态异常")


ERRORS = (ConversationNotFoundError, ConversationConflictError, ConversationStateError, RetrievalNotFoundError,
          RetrievalConflictError, RetrievalStateError, PlannerConfigurationError, GenerationConfigurationError,
          AgentInputError, PreferenceStateError)


@router.post("", response_model=ConversationRead, status_code=201)
def create(workspace_id: UUID, knowledge_base_id: UUID, session: DbSession) -> ConversationRead:
    try:
        return create_conversation(session, workspace_id, knowledge_base_id)
    except ERRORS as error:
        raise _http_error(error) from None


@router.get("/{conversation_id}", response_model=ConversationRead)
def read(workspace_id: UUID, knowledge_base_id: UUID, conversation_id: UUID,
         session: DbSession) -> ConversationRead:
    try:
        return read_conversation(session, workspace_id, knowledge_base_id, conversation_id)
    except ERRORS as error:
        raise _http_error(error) from None


@router.post("/{conversation_id}/messages", response_model=MessageRead)
def message(workspace_id: UUID, knowledge_base_id: UUID, conversation_id: UUID, payload: MessageRequest,
            session: DbSession, embedding: EmbeddingDep, generation: GenerationDep, planner: PlannerDep) -> MessageRead:
    def runner(question: str, context: dict):
        registry = ToolRegistry()
        registry.register(KnowledgeSearchTool(session, embedding, workspace_id, knowledge_base_id))
        registry.register(CalculatorTool())
        registry.register(SourcedCalculatorTool())
        resume = context.get("resume_task")
        task = PendingTask.model_validate_json(json.dumps(resume)) if resume is not None else None
        if task is None:
            registry.register(ClarificationTool())
            registry.register(PrepareBillingTool())
        initial = tuple(SearchHit(item.chunk_id, item.document_id, item.document_name, item.chunk_index,
                                  item.content, item.start_char, item.end_char, 0.0) for item in task.evidence) if task else ()
        raw_preference = context.get("answer_preference")
        preference = PreferenceRead.model_validate_json(json.dumps(raw_preference)) if raw_preference is not None else None
        styled = (PreferenceGenerationProvider(generation, preference.answer_detail)
                  if preference is not None and preference.answer_detail is not None else None)
        # 偏好只提供给最终生成，不供模型选择工具或解释可信算术输入。
        planner_context = {key: value for key, value in context.items()
                           if key not in ("resume_task", "answer_preference")}
        result = run_agent(planner, styled or generation, registry, question, conversation_context=planner_context,
                         initial_evidence=initial, user_inputs=tuple(task.confirmed_inputs) if task else (),
                         require_calculation=task is not None, max_steps=8 if task else 4,
                         max_evidence_characters=min(Settings().generation_max_context_characters, MAX_AGENT_CHARACTERS))
        return replace(result, answer_preference=preference) if styled is not None and styled.used else result
    try:
        return send_message(session, workspace_id, knowledge_base_id, conversation_id, payload, runner)
    except ERRORS as error:
        raise _http_error(error) from None
