from __future__ import annotations

import re

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
    verify_rule_signals: bool = False
    enable_critic: bool = False
    enable_repository_review: bool = True
    repository_evidence_tokens: int = 6000
    # Optional run-level cap on model input tokens (patch context + evidence).
    # None means measured and reported, but not enforced.
    max_review_input_tokens: int | None = None
    # Per-unit review (P4). OFF by default: measured on a 251-file PR it covered
    # 9/146 units (6%) for 558,691 tokens (~4.5 CNY) - less coverage than the
    # truncation path for tens of times the cost. Enable only when full coverage
    # matters more than price. Never reviews more than this many units.
    unit_review_max_units: int = 0
    unit_review_workers: int = 4
    # Dispatch-time cost ceiling (H13). The per-unit path is refused when its
    # estimated cost exceeds this, and the refusal is recorded in the report.
    unit_review_cost_ceiling_tokens: int = 400_000
    # Per-unit evidence budget. Smaller than the single-pass budget because a unit
    # is small and every round re-renders whatever is in here.
    unit_evidence_tokens: int = 3000
    # Knowledge layer (P3): where the playbook lives and how many entries to
    # offer one review. Empty path disables recall.
    playbook_path: str = "knowledge/playbook.md"
    playbook_recall_k: int = 5
    # Confidence floor for publishing an inline comment. It is stamped into the
    # report so the workflow reads one source instead of hardcoding its own.
    publish_min_confidence: float = 0.80
    review_reasoning_effort: str | None = None
    critic_model: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        env_ignore_empty=True,
    )


def get_settings() -> Settings:
    return Settings()


# CJK Unicode ranges
_CJK_RE = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff'  # CJK Unified + Ext A + Compat
    r'\u3040-\u309f\u30a0-\u30ff'                    # Hiragana + Katakana
    r'\uac00-\ud7af]'                                   # Hangul
)

def detect_output_language(title: str = "", body: str = "") -> str:
    """Detect output language from PR title and body.

    Returns 'zh' if CJK characters make up >20% of the text, otherwise 'en'.
    """
    text = (title or "") + " " + (body or "")
    # Strip whitespace for ratio calculation
    stripped = text.strip()
    meaningful = re.sub(r"\s+", "", stripped)
    if not meaningful:
        return "en"
    cjk_chars = len(_CJK_RE.findall(meaningful))
    ratio = cjk_chars / len(meaningful)
    return "zh" if ratio > 0.2 else "en"
