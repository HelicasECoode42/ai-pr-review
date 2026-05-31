from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    review_model: str = "gpt-4.1-mini"
    request_timeout_seconds: float = 45.0
    max_suggestions: int = 20
    max_suggestions_per_file: int = 5
    min_comment_confidence: float = 0.65

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )


def get_settings() -> Settings:
    return Settings()


import re

# CJK Unicode ranges
_CJK_RE = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff'  # CJK Unified + Ext A + Compat
    r'\u3040-\u309f\u30a0-\u30ff'                    # Hiragana + Katakana
    r'\uac00-\ud7af]'                                   # Hangul
)

def detect_language(title: str = "", body: str = "") -> str:
    """Detect output language from PR title and body.

    Returns 'zh' if CJK characters make up >20% of the text, otherwise 'en'.
    """
    text = (title or "") + " " + (body or "")
    # Strip whitespace for ratio calculation
    stripped = text.strip()
    if not stripped:
        return "en"
    cjk_chars = len(_CJK_RE.findall(stripped))
    ratio = cjk_chars / len(stripped)
    return "zh" if ratio > 0.2 else "en"
