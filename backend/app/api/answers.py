"""基于指定知识库的证据问答接口。"""

from collections.abc import Sequence
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.documents import EmbeddingDep
from app.api.workspaces import DbSession
from app.core.config import Settings
from app.knowledge.answers import AnswerContextLimitError, AnswerResult, answer_knowledge_base
from app.knowledge.retrieval import (
    MAX_QUERY_CHARACTERS,
    MAX_TOP_K,
    RetrievalConflictError,
    RetrievalInputError,
    RetrievalNotFoundError,
    RetrievalStateError,
)
from app.providers.compatible_generation import OpenAICompatibleGenerationProvider
from app.providers.embeddings import EmbeddingError, EmbeddingTimeoutError
from app.providers.generation import (
    GeneratedAnswer,
    GenerationConfigurationError,
    GenerationError,
    GenerationProvider,
    GenerationSource,
    GenerationTimeoutError,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}",
    tags=["answers"],
)


class ConfiguredGenerationProvider:
    """有证据需要生成时才读取生成服务配置；空检索结果可直接弃答。"""

    def generate_answer(self, question: str, sources: Sequence[GenerationSource]) -> GeneratedAnswer:
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
            )
        except (GenerationError, TypeError, ValueError):
            raise GenerationConfigurationError("生成服务配置无效或尚未完成") from None
        return provider.generate_answer(question, sources)


def get_generation_provider() -> GenerationProvider:
    return ConfiguredGenerationProvider()


GenerationDep = Annotated[GenerationProvider, Depends(get_generation_provider)]


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARACTERS, strict=True, description="本次问题，最多 2000 字符")
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K, strict=True, description="检索候选数，不代表必须引用的段落数")

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


@router.post("/answers", response_model=AnswerRead)
def answer(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    payload: AnswerRequest,
    session: DbSession,
    embedding_provider: EmbeddingDep,
    generation_provider: GenerationDep,
) -> AnswerResult:
    try:
        return answer_knowledge_base(
            session, embedding_provider, generation_provider, workspace_id, knowledge_base_id,
            payload.question, top_k=payload.top_k,
            max_context_characters=Settings().generation_max_context_characters,
        )
    except RetrievalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在") from None
    except RetrievalConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="查询模型与知识库向量配置不一致") from None
    except (RetrievalInputError, AnswerContextLimitError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="问答参数无效或文本块超过上下文预算") from None
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
