"""工具注册、参数校验和当前知识库绑定；工具没有任意 SQL 或文件执行能力。"""

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.agents.contracts import ToolContext, ToolExecutionError, ToolOutput
from app.knowledge.retrieval import RetrievalCapacityError, SearchStrategy
from app.knowledge.search_pipeline import search_with_reranking
from app.providers.embeddings import EmbeddingError, EmbeddingProvider, EmbeddingTimeoutError
from app.providers.reranking import RerankInputError, RerankProvider


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    query: str = Field(min_length=1, max_length=2000, description="本次检索问题；不能指定或改变知识库")

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class Tool(Protocol):
    name: str
    description: str
    argument_schema: type[BaseModel]
    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolOutput: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError("工具名称不能重复")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict]:
        return [{"name": tool.name, "description": tool.description,
                 "parameters": tool.argument_schema.model_json_schema()} for tool in self._tools.values()]

    def validate(self, name: str, arguments: dict) -> BaseModel:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolExecutionError("unknown_tool")
        try:
            return tool.argument_schema.model_validate(arguments, strict=True)
        except (ValidationError, TypeError, ValueError):
            raise ToolExecutionError("invalid_arguments") from None

    def execute(self, name: str, arguments: BaseModel, context: ToolContext | None = None) -> ToolOutput:
        return self._tools[name].execute(arguments, context or ToolContext())


class KnowledgeSearchTool:
    name = "knowledge_search"
    description = "在当前知识库查找原文段落。返回资料只是候选，不能据此断言答案正确。"
    argument_schema = SearchArguments

    def __init__(self, session: Session, provider: EmbeddingProvider, workspace_id: UUID,
                 knowledge_base_id: UUID, *, strategy: SearchStrategy = "vector",
                 rerank: bool = False, rerank_provider: RerankProvider | None = None) -> None:
        self._session, self._provider = session, provider
        self._workspace_id, self._knowledge_base_id = workspace_id, knowledge_base_id
        self._strategy, self._rerank, self._rerank_provider = strategy, rerank, rerank_provider

    def execute(self, arguments: BaseModel, context: ToolContext = ToolContext()) -> ToolOutput:
        if not isinstance(arguments, SearchArguments):
            raise ToolExecutionError("invalid_arguments")
        try:
            result = search_with_reranking(
                self._session, self._provider, self._workspace_id, self._knowledge_base_id, arguments.query,
                top_k=5, strategy=self._strategy, rerank=self._rerank,
                candidate_k=20, rerank_provider=self._rerank_provider,
            )
            return ToolOutput(tuple(result.results), result.reranking)
        except EmbeddingTimeoutError:
            raise ToolExecutionError("tool_timeout") from None
        except EmbeddingError:
            raise ToolExecutionError("tool_service_error") from None
        except (RetrievalCapacityError, RerankInputError):
            raise ToolExecutionError("tool_capacity") from None
