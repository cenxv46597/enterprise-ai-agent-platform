"""从环境变量与本机 .env 读取并校验应用配置。"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Enterprise AI Agent Platform"
    app_env: Literal["local", "test", "production"] = "local"
    database_url: str | None = None
    upload_root: Path = REPOSITORY_ROOT / "uploads"
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = Field(default=1024, ge=1024, le=1024)
    embedding_batch_size: int = Field(default=10, ge=1, le=10)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=120)
    embedding_total_timeout_seconds: float = Field(default=120, gt=0, le=300)
    embedding_max_chunks: int = Field(default=200, ge=1, le=1000)
    generation_base_url: str | None = None
    generation_api_key: SecretStr | None = None
    generation_model: str | None = None
    generation_timeout_seconds: float = Field(default=30, gt=0, le=120)
    generation_total_timeout_seconds: float = Field(default=60, gt=0, le=300)
    generation_max_tokens: int = Field(default=2000, ge=1, le=8192)
    generation_max_context_characters: int = Field(default=12000, ge=1, le=50000)
    rerank_timeout_seconds: float = Field(default=15, gt=0, le=120)
    rerank_total_timeout_seconds: float = Field(default=20, gt=0, le=120)
    rerank_max_tokens: int = Field(default=512, ge=128, le=2048)
    agent_timeout_seconds: float = Field(default=15, gt=0, le=120)
    agent_total_timeout_seconds: float = Field(default=20, gt=0, le=120)
    agent_max_tokens: int = Field(default=512, ge=128, le=2048)
    agent_protocol: Literal["native", "json"] = "native"
    mcp_tool_timeout_seconds: float = Field(default=30, gt=0, le=180, allow_inf_nan=False)
    review_base_url: str | None = None
    review_api_key: SecretStr | None = None
    review_model: str | None = None
    review_timeout_seconds: float = Field(default=30, gt=0, le=120)
    review_total_timeout_seconds: float = Field(default=60, gt=0, le=300)
    review_max_tokens: int = Field(default=3000, ge=1, le=8192)

    model_config = SettingsConfigDict(
        env_prefix="EAAP_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
