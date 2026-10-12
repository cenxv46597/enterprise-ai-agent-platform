"""知识库范围内的一次有界智能体运行；暂不保存会话或执行记录。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.contracts import (
    MAX_AGENT_CHARACTERS, MAX_AGENT_STEPS, AgentEvidence, AgentInputError, AgentRun, AgentStep,
    FinishAction, PlannerConfigurationError, PlannerProvider, ToolAction,
)
from app.agents.engine import run_agent, validate_scope
from app.agents.calculator import CalculatorTool
from app.agents.calculation_contracts import AgentCalculation
from app.agents.tools import KnowledgeSearchTool, ToolRegistry
from app.api.answers import AnswerRead, GenerationDep
from app.api.documents import EmbeddingDep
from app.api.reranking import RerankDep
from app.api.workspaces import DbSession
from app.core.config import Settings
from app.knowledge.retrieval import RetrievalConflictError, RetrievalNotFoundError, RetrievalStateError, SearchStrategy
from app.providers.compatible_planner import CompatiblePlannerProvider
from app.providers.native_planner import NativePlannerProvider
from app.providers.generation import GenerationConfigurationError
from app.providers.reranking import RerankConfigurationError
from app.mcp_tools.client import McpSearchClient, StdioSearchClient
from app.mcp_tools.tool import McpKnowledgeSearchTool


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}", tags=["agent-runs"])


class ConfiguredPlannerProvider:
    def decide(self, state: dict) -> ToolAction | FinishAction:
        try:
            settings = Settings()
            if not settings.generation_base_url or not settings.generation_api_key or not settings.generation_model:
                raise PlannerConfigurationError("请配置生成服务，用于动作决策")
            provider_class = NativePlannerProvider if settings.agent_protocol == "native" else CompatiblePlannerProvider
            provider = provider_class(
                settings.generation_base_url, settings.generation_api_key, settings.generation_model,
                settings.agent_timeout_seconds, settings.agent_total_timeout_seconds, settings.agent_max_tokens,
            )
        except (PlannerConfigurationError, TypeError, ValueError):
            raise PlannerConfigurationError("智能体动作模型配置无效或尚未完成") from None
        return provider.decide(state)


def get_planner_provider() -> PlannerProvider:
    return ConfiguredPlannerProvider()


PlannerDep = Annotated[PlannerProvider, Depends(get_planner_provider)]


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000, strict=True)
    max_steps: int = Field(default=4, ge=1, le=MAX_AGENT_STEPS, strict=True, description="动作决策次数上限；最后一步必须结束，否则停止")
    strategy: SearchStrategy = "vector"
    rerank: bool = Field(default=False, strict=True)
    search_backend: Literal["local", "mcp"] = "local"

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def get_mcp_search_client(workspace_id: UUID, knowledge_base_id: UUID, payload: AgentRequest) -> McpSearchClient:
    return StdioSearchClient(workspace_id, knowledge_base_id, payload.strategy)


McpSearchDep = Annotated[McpSearchClient, Depends(get_mcp_search_client)]


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: UUID
    status: Literal["completed", "stopped", "failed"]
    stop_reason: str | None
    steps: list[AgentStep]
    result: AnswerRead | None
    evidence: list[AgentEvidence]
    calculations: list[AgentCalculation]


@router.post("/agent-runs", response_model=AgentRunRead)
def start_agent(workspace_id: UUID, knowledge_base_id: UUID, payload: AgentRequest,
                session: DbSession, embedding: EmbeddingDep, generation: GenerationDep,
                planner: PlannerDep, rerank_provider: RerankDep, mcp_client: McpSearchDep) -> AgentRun:
    try:
        validate_scope(session, workspace_id, knowledge_base_id)
        if payload.search_backend == "mcp" and payload.rerank:
            raise HTTPException(422, "首版 MCP 搜索暂不支持候选重排；请选择 local 或关闭 rerank")
        registry = ToolRegistry()
        if payload.search_backend == "mcp":
            registry.register(McpKnowledgeSearchTool(session, mcp_client, workspace_id, knowledge_base_id,
                                                    strategy=payload.strategy))
        else:
            registry.register(KnowledgeSearchTool(session, embedding, workspace_id, knowledge_base_id,
                                                 strategy=payload.strategy, rerank=payload.rerank, rerank_provider=rerank_provider))
        registry.register(CalculatorTool())
        return run_agent(planner, generation, registry, payload.question, max_steps=payload.max_steps,
                         max_evidence_characters=min(Settings().generation_max_context_characters, MAX_AGENT_CHARACTERS))
    except RetrievalNotFoundError:
        raise HTTPException(404, "知识库不存在") from None
    except RetrievalConflictError:
        raise HTTPException(409, "查询模型与知识库向量配置不一致") from None
    except RetrievalStateError:
        raise HTTPException(500, "知识库向量数据异常") from None
    except AgentInputError:
        raise HTTPException(422, "智能体运行参数无效") from None
    except (PlannerConfigurationError, GenerationConfigurationError, RerankConfigurationError):
        raise HTTPException(503, "请检查生成模型及智能体或重排配置") from None
