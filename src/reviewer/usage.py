"""Provider usage accounting.

The raw `usage` dictionary each response carries is the only trustworthy record
of what a run cost, so it is summarised here instead of being re-estimated. Cache
counters come straight from the provider and are reported, never inferred.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cache_tokens(usage: Mapping[str, object]) -> tuple[int | None, int | None]:
    """Return (hit, miss) prompt tokens for the providers we speak to.

    DeepSeek reports `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`;
    OpenAI-compatible endpoints report `prompt_tokens_details.cached_tokens`.
    A provider that reports neither leaves both as None rather than zero, so an
    unknown cache state never looks like a perfect miss.
    """
    hit = _int(usage.get("prompt_cache_hit_tokens"))
    miss = _int(usage.get("prompt_cache_miss_tokens"))
    if hit is None and miss is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            hit = _int(details.get("cached_tokens"))
    if hit is None and miss is None:
        return None, None
    hit = hit or 0
    if miss is None:
        prompt = _int(usage.get("prompt_tokens"))
        miss = max(0, (prompt or 0) - hit)
    return hit, miss


def summarize_usage(records: Iterable[Mapping[str, object]]) -> dict[str, int | float]:
    """Summarise recorded responses; only complete usage triples count as known."""
    prompt = completion = total = 0
    hit = miss = 0
    cache_reported = False
    known = unknown = 0
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, Mapping):
            unknown += 1
            continue
        p, c, t = (
            _int(usage.get("prompt_tokens")),
            _int(usage.get("completion_tokens")),
            _int(usage.get("total_tokens")),
        )
        if p is None or c is None or t is None:
            unknown += 1
            continue
        known += 1
        prompt += p
        completion += c
        total += t
        h, m = _cache_tokens(usage)
        if h is not None and m is not None:
            cache_reported = True
            hit += h
            miss += m
    summary: dict[str, int | float] = {
        "usage_responses_known": known,
        "usage_responses_unknown": unknown,
        "usage_prompt_tokens": prompt,
        "usage_completion_tokens": completion,
        "usage_total_tokens": total,
    }
    if cache_reported and hit + miss > 0:
        summary["cache_hit_tokens"] = hit
        summary["cache_miss_tokens"] = miss
        summary["cache_hit_rate"] = round(hit / (hit + miss), 4)
    return summary
