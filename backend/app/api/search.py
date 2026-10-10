"""限定工作空间和知识库的语义检索接口。"""

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.documents import EmbeddingDep
from app.api.workspaces import DbSession
from app.knowledge.retrieval import (
    MAX_QUERY_CHARACTERS,
    MAX_TOP_K,
    RetrievalConflictError,
    RetrievalInputError,
    RetrievalNotFoundError,
    RetrievalStateError,
    SearchResult,
    search_knowledge_base,
)
from app.providers.embeddings import EmbeddingError, EmbeddingTimeoutError


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}",
    tags=["search"],
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=MAX_QUERY_CHARACTERS, strict=True,
        description="去掉首尾空白后的检索问题，最多 2000 字符",
    )
    top_k: int = Field(
        default=5, ge=1, le=MAX_TOP_K, strict=True,
        description="最多返回的段落数，候选不足时返回实际数量",
    )

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SearchHitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    content: str
    start_char: int
    end_char: int
    distance: float = Field(
        ge=0, le=2, allow_inf_nan=False,
        description="余弦距离，越小越接近，范围 0 至 2；不是答案正确率",
    )


class SearchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric: Literal["cosine_distance"]
    model: str | None
    dimensions: int | None
    results: list[SearchHitRead]


@router.post("/search", response_model=SearchRead)
def search(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    payload: SearchRequest,
    session: DbSession,
    provider: EmbeddingDep,
) -> SearchResult:
    try:
        return search_knowledge_base(
            session, provider, workspace_id, knowledge_base_id, payload.query, top_k=payload.top_k
        )
    except RetrievalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在") from None
    except RetrievalConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="查询模型与知识库向量配置不一致，请检查模型配置",
        ) from None
    except RetrievalInputError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="检索参数无效") from None
    except EmbeddingTimeoutError:
        logger.warning("查询向量生成超时，知识库 ID：%s", knowledge_base_id)
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="向量服务请求超时") from None
    except EmbeddingError as exc:
        logger.warning("查询向量生成失败，知识库 ID：%s，错误类型：%s", knowledge_base_id, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="向量服务调用或返回结果异常") from None
    except RetrievalStateError:
        logger.error("检索数据状态异常，知识库 ID：%s", knowledge_base_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="知识库向量数据异常，请检查数据库记录",
        ) from None
