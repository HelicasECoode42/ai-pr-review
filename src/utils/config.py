from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    review_model: str = "gpt-4.1-mini"
    request_timeout_seconds: float = 45.0
    model_connect_timeout_seconds: float = 10.0
    model_read_timeout_seconds: float = 180.0
    max_suggestions: int = 20
    max_suggestions_per_file: int = 5
    min_comment_confidence: float = 0.65
    verify_rule_signals: bool = False
    enable_critic: bool = False
    enable_repository_review: bool = True
    grounded_missing_evidence_followup: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "GROUNDED_MISSING_EVIDENCE_FOLLOWUP", "grounded_missing_evidence_followup",
        ),
    )
    repository_evidence_tokens: int = 6000
    # Additive budget for documentation-only changes. Measured on the dev set:
    # 1200 tokens recovered 3 more gold lines (S 72.55% -> 75.49%). Off by default
    # because it grows the prompt without a paid D/P run proving the trade is
    # worth it; enable per experiment.
    documentation_patch_tokens: int = 0
    # Complete system+user request input, conservatively estimated in UTF-8
    # bytes. This is a request guard, not a tokenizer or billing-token count.
    max_input_token_upper_bound: int | None = Field(
        default=200_000,
        gt=0,
        validation_alias=AliasChoices(
            "MAX_INPUT_TOKEN_UPPER_BOUND", "max_input_token_upper_bound",
        ),
    )
    max_review_http_attempts: int = Field(default=16, ge=1)
    max_review_timeout_seconds: float = Field(default=300.0, gt=0)
    model_output_token_limit: int = Field(default=32768, ge=1)
    # Legacy context diagnostic cap retained for callers using the old setting.
    max_review_input_tokens: int | None = None
    strategy_policy_store_dir: str | None = None
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
    # Per-unit checkpoints land here, one file per frozen head SHA, so a run that
    # dies mid-way resumes instead of re-paying for finished units.
    unit_checkpoint_dir: str = "reports/unit-checkpoints"
    # Knowledge layer (P3): where the playbook lives and how many entries to
    # offer one review. Empty path disables recall.
    playbook_path: str = "knowledge/playbook.md"
    playbook_recall_k: int = 5
    # Confidence floor for publishing an inline comment. It is stamped into the
    # report so the workflow reads one source instead of hardcoding its own.
    publish_min_confidence: float = 0.80
    # Candidate lifecycle records are append-only and local to this checkout by
    # default. Set DECISION_LEDGER_ENABLED=false to keep them in the report only.
    decision_ledger_enabled: bool = True
    decision_ledger_path: str = "reports/decision-ledger.jsonl"
    review_reasoning_effort: str | None = None
    # DeepSeek-specific Chat Completions switch. None preserves provider defaults;
    # callers pass it only when explicitly configured.
    review_thinking_enabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REVIEW_THINKING_ENABLED", "review_thinking_enabled",
        ),
    )
    critic_model: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        env_ignore_empty=True,
        populate_by_name=True,
    )


def get_settings() -> Settings:
    return Settings()


def is_deepseek_chat_model(model: str, base_url: str) -> bool:
    """Return whether the official DeepSeek Chat Completions API owns this model id."""
    endpoint = urlsplit(base_url)
    return (
        endpoint.scheme == "https"
        and endpoint.hostname == "api.deepseek.com"
        and endpoint.username is None
        and model in {
            "deepseek-flash",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-v4-flash-vision-exp",
        }
    )


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
