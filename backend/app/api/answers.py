"""基于指定知识库的证据问答接口。"""

from collections.abc import Callable, Sequence
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.documents import EmbeddingDep
from app.api.reranking import RerankDep
from app.api.workspaces import DbSession
from app.core.config import Settings
from app.knowledge.answers import AnswerContextLimitError, AnswerResult, answer_knowledge_base
from app.knowledge.retrieval import (
    MAX_QUERY_CHARACTERS,
    MAX_TOP_K,
    RetrievalCapacityError,
    RetrievalConflictError,
    RetrievalInputError,
    RetrievalNotFoundError,
    RetrievalStateError,
    SearchStrategy,
)
from app.providers.compatible_generation import OpenAICompatibleGenerationProvider
from app.providers.embeddings import EmbeddingError, EmbeddingTimeoutError
from app.providers.reranking import RerankConfigurationError, RerankInputError, RerankTrace
from app.providers.generation import (
    GeneratedAnswer,
    GenerationConfigurationError,
    GenerationError,
    GenerationProvider,
    GenerationSource,
    GenerationTimeoutError,
)
from app.memory.contracts import AnswerDetail


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}",
    tags=["answers"],
)


class ConfiguredGenerationProvider:
    """有证据需要生成时才读取生成服务配置；空检索结果可直接弃答。"""

    def __init__(self, *, request_observer: Callable[[dict], None] | None = None) -> None:
        self._request_observer = request_observer

    def generate_answer(self, question: str, sources: Sequence[GenerationSource]) -> GeneratedAnswer:
        return self._provider().generate_answer(question, sources)

    def generate_answer_with_preferences(
        self, question: str, sources: Sequence[GenerationSource], answer_detail: AnswerDetail,
    ) -> GeneratedAnswer:
        return self._provider().generate_answer_with_preferences(question, sources, answer_detail)

    def _provider(self) -> OpenAICompatibleGenerationProvider:
        try:
            settings = Settings()
            if not settings.generation_base_url or not settings.generation_api_key or not settings.generation_model:
                raise GenerationConfigurationError("生成服务的地址、密钥和模型尚未配置")
            provider = OpenAICompatibleGenerationProvider(
                base_url=settings.generation_base_url,
                api_key=settings.generation_api_key,
                model=settings.generation_model,
                timeout_seconds=settings.generation_timeout_seconds,
                total_timeout_seconds=settings.generation_total_timeout_seconds,
                max_tokens=settings.generation_max_tokens,
                request_observer=self._request_observer,
            )
        except (GenerationError, TypeError, ValueError):
            raise GenerationConfigurationError("生成服务配置无效或尚未完成") from None
        return provider


def get_generation_provider() -> GenerationProvider:
    return ConfiguredGenerationProvider()


GenerationDep = Annotated[GenerationProvider, Depends(get_generation_provider)]


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARACTERS, strict=True, description="本次问题，最多 2000 字符")
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K, strict=True, description="送入上下文选择的最终候选数，不代表必须引用的段落数")
    strategy: SearchStrategy = Field(default="vector", description="选择 vector 余弦检索或 hybrid 混合检索")
    rerank: bool = Field(default=False, strict=True, description="显式开启模型重排，随后仍需证据与引用校验")
    candidate_k: int = Field(default=20, ge=1, le=20, strict=True, description="开启重排时的召回候选量，必须不少于 top_k；关闭时忽略")

    @model_validator(mode="after")
    def check_candidates(self) -> "AnswerRequest":
        if self.rerank and self.candidate_k < self.top_k:
            raise ValueError("candidate_k 不能少于 top_k")
        return self

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AnswerCitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    quote: str
    start_char: int = Field(description="引文在解析后文本中的起点，包含该字符")
    end_char: int = Field(description="引文在解析后文本中的终点，不包含该字符")


class AnswerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    answer: str
    abstained: bool
    citations: list[AnswerCitationRead]
    reranking: RerankTrace | None = None


@router.post("/answers", response_model=AnswerRead, response_model_exclude_none=True)
def answer(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    payload: AnswerRequest,
    session: DbSession,
    embedding_provider: EmbeddingDep,
    generation_provider: GenerationDep,
    rerank_provider: RerankDep,
) -> AnswerResult:
    try:
        return answer_knowledge_base(
            session, embedding_provider, generation_provider, workspace_id, knowledge_base_id,
            payload.question, top_k=payload.top_k, strategy=payload.strategy,
            max_context_characters=Settings().generation_max_context_characters,
            rerank=payload.rerank, candidate_k=payload.candidate_k, rerank_provider=rerank_provider,
        )
    except RetrievalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在") from None
    except RetrievalConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="查询模型与知识库向量配置不一致") from None
    except RetrievalCapacityError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="当前混合检索支持最多 2000 个可检索文本块、100 万正文字符，请使用较小知识库或选择 vector 策略") from None
    except (RetrievalInputError, AnswerContextLimitError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="问答参数无效或文本块超过上下文预算") from None
    except RerankInputError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="重排参数无效或候选正文超过 20000 字符；请减少候选数或关闭重排") from None
    except RerankConfigurationError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="当前重排基线复用生成服务，请检查生成服务及重排等待时间配置") from None
    except GenerationConfigurationError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="请检查生成服务的接口地址、API 密钥和模型配置") from None
    except (EmbeddingTimeoutError, GenerationTimeoutError):
        logger.warning("问答模型请求超时，知识库 ID：%s", knowledge_base_id)
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="模型服务请求超时") from None
    except (EmbeddingError, GenerationError) as exc:
        logger.warning("问答模型调用或结果异常，知识库 ID：%s，错误类型：%s", knowledge_base_id, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="模型服务调用或返回结果异常") from None
    except RetrievalStateError:
        logger.error("问答检索数据异常，知识库 ID：%s", knowledge_base_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="知识库向量数据异常") from None
