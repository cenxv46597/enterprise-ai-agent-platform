"""从环境变量与本机 .env 读取并校验应用配置。"""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Enterprise AI Agent Platform"
    app_env: Literal["local", "test", "production"] = "local"
    database_url: str | None = None
    jwt_secret_key: SecretStr | None = None
    web_origin: str = "http://127.0.0.1:5173"
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

    @model_validator(mode="after")
    def validate_web_origin(self) -> "Settings":
        try:
            origin = urlsplit(self.web_origin)
            port = origin.port
            if (origin.scheme not in {"http", "https"} or not origin.hostname
                    or origin.username is not None or origin.password is not None
                    or origin.path or origin.query or origin.fragment
                    or self.web_origin != f"{origin.scheme}://{origin.netloc}"
                    or (port is not None and not 1 <= port <= 65535)
                    or any(char.isspace() or ord(char) < 32 for char in self.web_origin)):
                raise ValueError("invalid origin")
        except ValueError:
            raise ValueError("网页来源必须是没有路径、凭据或查询的完整 HTTP(S) origin") from None
        if self.app_env == "production" and (
            "web_origin" not in self.model_fields_set or origin.scheme != "https"
        ):
            raise ValueError("生产环境必须显式配置 HTTPS 网页来源 EAAP_WEB_ORIGIN")
        return self

    model_config = SettingsConfigDict(
        env_prefix="EAAP_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
