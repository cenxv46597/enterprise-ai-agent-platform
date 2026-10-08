"""Validated application settings from environment variables and .env."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Enterprise AI Agent Platform"
    app_env: Literal["local", "test", "production"] = "local"
    database_url: str | None = None
    upload_root: Path = REPOSITORY_ROOT / "uploads"
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)

    model_config = SettingsConfigDict(
        env_prefix="EAAP_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
