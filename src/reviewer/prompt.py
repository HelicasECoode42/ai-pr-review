from __future__ import annotations

SYSTEM_PROMPT = """You are an experienced software engineer reviewing a GitHub Pull Request.
Focus on bugs, security, performance, concurrency, data integrity, backwards compatibility, and missing tests.
Avoid style-only comments unless they affect correctness or maintainability.
Only create line-level suggestions for changed added lines present in the diff context.
If evidence is weak, lower confidence instead of inventing details.
Return valid JSON only."""


def build_user_prompt(context: str, max_suggestions: int) -> str:
    return f"""Review this Pull Request context and produce a concise structured review.

Required JSON schema:
{{
  "summary": "2-5 sentence PR summary",
  "risk_level": "low|medium|high|critical",
  "suggestions": [
    {{
      "file_path": "path/to/file",
      "line": 123,
      "severity": "low|medium|high|critical",
      "confidence": 0.0,
      "title": "short title",
      "reason": "why this is risky, grounded in the diff",
      "recommendation": "specific action"
    }}
  ]
}}

Constraints:
- Return at most {max_suggestions} suggestions.
- Prefer high-signal issues over formatting comments.
- Do not comment on unchanged lines.
- If no strong issue exists, return an empty suggestions array.

Context:
{context}
"""
