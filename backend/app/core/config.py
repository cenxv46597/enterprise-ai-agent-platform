"""Validated application settings from environment variables and .env."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Enterprise AI Agent Platform"
    app_env: Literal["local", "test", "production"] = "local"

    model_config = SettingsConfigDict(
        env_prefix="EAAP_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
