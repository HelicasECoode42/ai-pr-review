from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    review_model: str = "gpt-4.1-mini"
    request_timeout_seconds: float = 45.0
    max_suggestions: int = 20
    min_comment_confidence: float = 0.65

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )


def get_settings() -> Settings:
    return Settings()
