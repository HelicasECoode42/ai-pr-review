"""Two-tier scanning: sweep everything cheaply, review only what looks suspicious.

The budget cannot cover a large diff, and covering it with the expensive path is
unaffordable. So the first tier is deterministic and free - the rule and AST
scanners over every changed hunk - and it produces a suspicion score. The second
tier is the model, and it only sees the hunks the first tier flagged.

The guarantee this buys is different from hunk coverage, and it is stated as such:
**every hunk is swept**, and the fraction that reaches deep review is reported
along with why the rest did not. That is the only shape in which "cheap" and
"nothing silently skipped" hold at the same time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models import ChangedFile, DiffHunk, RiskFinding

_SENSITIVE = re.compile(
    r"\b(auth|token|secret|password|credential|permission|admin|sql|exec|eval|"
    r"subprocess|pickle|shell|deserial|session|cookie|redirect|path)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TriagePolicy:
    rule_hit_weight: float = 10.0
    # A sensitive token alone must reach the threshold: auth, token, secret, sql,
    # exec. Weighting it below the threshold silently excluded exactly the hunks
    # most likely to carry the defects this tool is for.
    sensitive_weight: float = 10.0
    churn_weight: float = 0.1
    public_weight: float = 1.0
    threshold: float = 10.0

    def score(self, *, rule_hits: int, sensitive: bool, churn: int,
              public_interface: bool) -> float:
        return (
            self.rule_hit_weight * rule_hits
            + (self.sensitive_weight if sensitive else 0.0)
            + self.churn_weight * churn
            + (self.public_weight if public_interface else 0.0)
        )


@dataclass
class TieredScan:
    swept: list[tuple[str, DiffHunk]] = field(default_factory=list)
    suspicious: list[tuple[str, DiffHunk, float, tuple[str, ...]]] = field(default_factory=list)
    uncovered_reasons: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict:
        swept_count = len(self.swept)
        deep = len(self.suspicious)
        return {
            "hunks_swept": swept_count,
            "hunks_deep_review": deep,
            "deep_share": round(deep / swept_count, 4) if swept_count else 0.0,
            "swept_share": 1.0 if swept_count else 1.0,
            "not_deep_reasons": dict(sorted(self.uncovered_reasons.items())),
        }


def _public(path: str) -> bool:
    return len([p for p in path.split("/") if p]) <= 2


def _hunk_text(hunk: DiffHunk) -> str:
    """The hunk's own added and removed lines, as the parser recorded them."""
    # The parser yields plain strings here while the field is annotated as a
    # richer type, so accept either rather than assuming one.
    def text(item: object) -> str:
        return item.content if hasattr(item, "content") else str(item)

    return "\n".join(text(line) for line in [*hunk.added_lines, *hunk.removed_lines])


def triage(
    files: list[ChangedFile],
    hunks_by_file: dict[str, list[DiffHunk]],
    findings: list[RiskFinding] | None = None,
    *,
    policy: TriagePolicy | None = None,
    budget: int | None = None,
) -> TieredScan:
    """Sweep every hunk; flag the ones worth an expensive look.

    `budget` caps how many flagged hunks go forward, and a capped hunk is counted
    under ``not_deep_reasons['budget_exhausted']`` rather than disappearing.
    """
    policy = policy or TriagePolicy()
    # A rule finding is attributed to its file: it usually points at one line, but
    # a hunk whose span does not cover it would otherwise be lost, and the point of
    # tier one is that nothing is lost.
    hit_lines: dict[str, list[int]] = {}
    for finding in findings or []:
        hit_lines.setdefault(finding.file_path, []).append(finding.line or 0)

    scan = TieredScan()
    flagged: list[tuple[str, DiffHunk, float, tuple[str, ...]]] = []
    for file in files:
        for hunk in hunks_by_file.get(file.filename, []):
            scan.swept.append((file.filename, hunk))
            span_start = hunk.new_start
            span_end = hunk.new_start + max(hunk.new_count, 1)
            file_hits = hit_lines.get(file.filename, [])
            in_span = sum(1 for line in file_hits if line and span_start <= line < span_end)
            # The first hunk of a flagged file carries the file-level hit so that a
            # finding whose line does not fall in any span still reaches deep review.
            first_hunk = hunks_by_file.get(file.filename, [None])[0] is hunk
            rule_hits = in_span + (1 if (file_hits and not any(
                line and span_start <= line < span_end for line in file_hits
            ) and first_hunk) else 0)
            text = _hunk_text(hunk)
            sensitive = bool(_SENSITIVE.search(text))
            churn = hunk.new_count + hunk.old_count
            score = policy.score(rule_hits=rule_hits, sensitive=sensitive, churn=churn,
                                 public_interface=_public(file.filename))
            reasons: list[str] = []
            if rule_hits:
                reasons.append(f"rule_hits={rule_hits}")
            if sensitive:
                reasons.append("sensitive_token")
            if _public(file.filename):
                reasons.append("public_interface")
            if score >= policy.threshold:
                flagged.append((file.filename, hunk, round(score, 2), tuple(reasons)))
            else:
                scan.uncovered_reasons["below_threshold"] = (
                    scan.uncovered_reasons.get("below_threshold", 0) + 1
                )
    flagged.sort(key=lambda item: (-item[2], item[0], item[1].new_start))
    if budget is not None and len(flagged) > budget:
        for _path, _hunk, _score, _reasons in flagged[budget:]:
            scan.uncovered_reasons["budget_exhausted"] = (
                scan.uncovered_reasons.get("budget_exhausted", 0) + 1
            )
        flagged = flagged[:budget]
    scan.suspicious = flagged
    return scan
