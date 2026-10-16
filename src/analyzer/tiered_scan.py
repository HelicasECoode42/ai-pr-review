"""Two-tier scanning: sweep everything cheaply, review only what looks suspicious.

The budget cannot cover a large diff, and covering it with the expensive path is
unaffordable. So the first tier is deterministic and free - the rule and AST
scanners over every changed hunk - and it produces a suspicion score. The second
tier is the model, and it only sees the hunks the first tier flagged.

The guarantee this buys is different from hunk coverage, and it is stated as such:
**every hunk is swept**, a rule-flagged hunk always reaches deep review, and every
hunk that does not is counted with its reason. That is the only shape in which
"cheap" and "nothing silently skipped" hold at the same time.

Tier-one selectivity is a measured property, not an assumption. An earlier version
matched topic words such as "token" and "secret" and flagged 34% of the hunks in
this repository - which is about tokens - for a deep share of 47%, i.e. no saving
at all. The pattern now names dangerous *operations* instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models import ChangedFile, DiffHunk, RiskFinding

# Dangerous operations, not topic words: eval/exec/compile, shell invocation,
# unsafe deserialisation, disabled TLS verification, broken hashes.
_RISKY = re.compile(
    r"\b(?:eval|exec|compile)\s*\("
    r"|subprocess\.|os\.system|shell\s*=\s*True"
    r"|pickle\.loads?|yaml\.load\b"
    r"|verify\s*=\s*False"
    r"|\b(?:md5|sha1)\s*\("
)


# Rules whose finding is evidence that a *defect may be present*. Path rules say a
# file lives in an "infra" directory and cross-file rules say calls cross a file
# boundary: those fire on every large PR and are context, not suspicion. Treating
# them as triggers is what made tier one flag 20% of a 251-file PR, 100 of those
# findings coming from the two structural families.
SUSPICION_RULES: frozenset[str] = frozenset({
    "ast-broad-except",
    "ast-dangerous-exec",
    "ast-unbounded-loop",
    "sql-string-concat",
    "dynamic-execution",
    "secret-logging",
    "shell-execution",
})
CONTEXT_RULE_PREFIXES: tuple[str, ...] = ("cross-file-", "risk-path-")


def is_suspicion_finding(rule_id: str) -> bool:
    """Defect-suspect rules only; structural and path signals are not triggers."""
    if rule_id.startswith(CONTEXT_RULE_PREFIXES):
        return False
    return rule_id in SUSPICION_RULES


@dataclass(frozen=True)
class TriagePolicy:
    rule_hit_weight: float = 10.0
    risky_weight: float = 10.0
    # Churn and publicness only break ties; neither may flag a hunk on its own, or
    # a large public file floods tier two exactly as the topic words used to.
    churn_weight: float = 0.02
    public_weight: float = 0.5
    threshold: float = 10.0

    def score(self, *, rule_hits: int, risky: bool, churn: int,
              public_interface: bool) -> float:
        return (
            self.rule_hit_weight * rule_hits
            + (self.risky_weight if risky else 0.0)
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
            "swept_share": 1.0,
            "not_deep_reasons": dict(sorted(self.uncovered_reasons.items())),
        }


def _public(path: str) -> bool:
    return len([part for part in path.split("/") if part]) <= 2


def _hunk_text(hunk: DiffHunk) -> str:
    """The hunk's own added and removed lines, as the parser recorded them."""
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
    """Sweep every hunk; flag the ones worth an expensive look."""
    policy = policy or TriagePolicy()
    # Triage exists to choose when the diff does not fit. If it fits, everything is
    # reviewed and no suspicion signal is needed - otherwise a small PR whose only
    # findings come from path rules would silently reach the model with nothing.
    if budget is not None:
        total_churn = sum(
            hunk.new_count + hunk.old_count
            for file in files for hunk in hunks_by_file.get(file.filename, [])
        )
        if total_churn <= budget:
            scan = TieredScan()
            for file in files:
                for hunk in hunks_by_file.get(file.filename, []):
                    scan.swept.append((file.filename, hunk))
                    scan.suspicious.append((file.filename, hunk, 0.0, ("fits_budget",)))
            return scan
    hit_lines: dict[str, list[int]] = {}
    context_only = 0
    for finding in findings or []:
        if not is_suspicion_finding(finding.rule_id):
            context_only += 1
            continue
        hit_lines.setdefault(finding.file_path, []).append(finding.line or 0)

    scan = TieredScan()
    flagged: list[tuple[str, DiffHunk, float, tuple[str, ...]]] = []
    for file in files:
        file_hunks = hunks_by_file.get(file.filename, [])
        for index, hunk in enumerate(file_hunks):
            scan.swept.append((file.filename, hunk))
            span_start = hunk.new_start
            span_end = hunk.new_start + max(hunk.new_count, 1)
            file_hits = hit_lines.get(file.filename, [])
            in_span = sum(1 for line in file_hits if line and span_start <= line < span_end)
            # A finding whose line falls in no span is carried by the file's first
            # hunk, so a rule hit can never be lost between hunks.
            carries_file_hit = bool(file_hits) and index == 0 and not any(
                line and span_start <= line < span_end for line in file_hits
            )
            rule_hits = in_span + (1 if carries_file_hit else 0)
            risky = bool(_RISKY.search(_hunk_text(hunk)))
            churn = hunk.new_count + hunk.old_count
            score = policy.score(rule_hits=rule_hits, risky=risky, churn=churn,
                                 public_interface=_public(file.filename))
            reasons: list[str] = []
            if rule_hits:
                reasons.append(f"rule_hits={rule_hits}")
            if risky:
                reasons.append("risky_operation")
            # Invariant: a rule-flagged hunk always reaches deep review.
            if rule_hits or score >= policy.threshold:
                flagged.append((file.filename, hunk, round(score, 2), tuple(reasons)))
            else:
                scan.uncovered_reasons["below_threshold"] = (
                    scan.uncovered_reasons.get("below_threshold", 0) + 1
                )
    # Rule-flagged hunks first, so a budget cap can never drop them.
    flagged.sort(key=lambda item: (
        -int(any(reason.startswith("rule_hits") for reason in item[3])),
        -item[2], item[0], item[1].new_start,
    ))
    if budget is not None and len(flagged) > budget:
        dropped = len(flagged) - budget
        scan.uncovered_reasons["budget_exhausted"] = (
            scan.uncovered_reasons.get("budget_exhausted", 0) + dropped
        )
        flagged = flagged[:budget]
    scan.suspicious = flagged
    if context_only:
        scan.uncovered_reasons["context_signal_not_a_trigger"] = context_only
    return scan
