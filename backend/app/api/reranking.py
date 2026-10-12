"""延迟读取已有生成服务配置，用于显式开启的重排基线。"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends

from app.core.config import Settings
from app.providers.compatible_reranking import CompatibleRerankProvider
from app.providers.reranking import (
    RerankCandidate, RerankConfigurationError, RerankProvider, RerankResult,
)


class ConfiguredRerankProvider:
    def rerank(self, question: str, candidates: Sequence[RerankCandidate]) -> RerankResult:
        try:
            settings = Settings()
            if not settings.generation_base_url or not settings.generation_api_key or not settings.generation_model:
                raise RerankConfigurationError("请先配置生成服务，用于当前重排基线")
            provider = CompatibleRerankProvider(
                base_url=settings.generation_base_url,
                api_key=settings.generation_api_key,
                model=settings.generation_model,
                timeout_seconds=settings.rerank_timeout_seconds,
                total_timeout_seconds=settings.rerank_total_timeout_seconds,
                max_tokens=settings.rerank_max_tokens,
            )
        except (RerankConfigurationError, TypeError, ValueError):
            raise RerankConfigurationError("重排服务配置无效或尚未完成") from None
        return provider.rerank(question, candidates)


def get_rerank_provider() -> RerankProvider:
    return ConfiguredRerankProvider()


RerankDep = Annotated[RerankProvider, Depends(get_rerank_provider)]
