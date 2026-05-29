from __future__ import annotations

SYSTEM_PROMPT = """You are an experienced software engineer reviewing a GitHub Pull Request.
Focus on bugs, security, performance, concurrency, data integrity, backwards compatibility, and missing tests.
Avoid style-only comments (naming, formatting, indentation) unless they directly cause bugs.
Only create line-level suggestions for changed added lines present in the diff context.
Every suggestion must cite specific evidence from the diff; do not speculate.
If evidence is weak, lower confidence instead of inventing details.
Do not suggest changes to lines that did not change in this PR.
Return valid JSON only."""


def build_user_prompt(context: str, max_suggestions: int, language: str = "en") -> str:
    lang_instruction = _language_instruction(language)
    return f"""Review this Pull Request context and produce a concise structured review.

{lang_instruction}

Required JSON schema:
{{
  "summary": "2-5 sentence PR summary explaining what changed, which modules are affected, and what reviewers should focus on",
  "risk_level": "low|medium|high|critical",
  "suggestions": [
    {{
      "file_path": "path/to/file",
      "line": 123,
      "severity": "low|medium|high|critical",
      "confidence": 0.0,
      "title": "short actionable title",
      "reason": "why this is risky; must reference specific changed lines from the diff",
      "recommendation": "concrete fix with code example if applicable"
    }}
  ]
}}

Constraints:
- Return at most {max_suggestions} suggestions.
- Prefer high-signal issues (security, data loss, crash) over low-value comments.
- Do not comment on unchanged lines or deletion-only lines.
- If no strong issue exists, return an empty suggestions array.
- Every suggestion must include diff evidence in the reason field.
- For uncertain findings, set confidence below 0.65 rather than omitting.
- recommendation must be actionable: include a specific fix, not just "fix this".
- If multiple suggestions share the same root cause (e.g. several files swallow exceptions the same way), merge them into one suggestion and list representative file:line pairs in the reason.

Context:
{context}
"""


def _language_instruction(language: str) -> str:
    if language == "zh":
        return (
            "Output all text fields (summary, title, reason, recommendation) in Chinese (Simplified). "
            "Use technical English terms where no natural Chinese equivalent exists."
        )
    return "Output all text fields in English."
