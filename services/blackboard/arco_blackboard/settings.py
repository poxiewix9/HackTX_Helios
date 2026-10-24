"""Runtime settings for the blackboard service, read from environment / .env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://arco:arco_dev_password@localhost:5432/arco",
        description="SQLAlchemy URL (psycopg3 driver). Overridden by DATABASE_URL.",
    )
    redis_url: str | None = Field(
        default=None,
        description="redis://host:port/db. Unset or unreachable -> LISTEN/NOTIFY fallback.",
    )
    blackboard_port: int = Field(default=8001, ge=1, le=65535)
    arco_log_level: str = "INFO"
    arco_log_json: bool = True


def get_settings() -> Settings:
    return Settings()
