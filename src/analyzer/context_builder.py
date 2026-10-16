from __future__ import annotations

import logging
import re
from functools import lru_cache
from dataclasses import dataclass, field
from enum import Enum

from src.analyzer.diff_parser import parse_file_hunks
from src.application.protocol import (
    BudgetLine,
    BudgetReport,
    ChunkCoverage,
    ChunkCoverageSummary,
    Omission,
)
from src.models import ChangedFile, PullRequest, RiskFinding, FileStatus, StepStatus
from src.github.client import GitHubClient, GitHubApiError
from src.utils.config import get_settings
from pathlib import Path, PurePosixPath

import os
import tiktoken
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _token_encoding():
    """Load tiktoken lazily; offline execution falls back conservatively."""
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning(
            "cl100k_base tokenizer unavailable; using UTF-8 byte upper bound: %s",
            exc,
        )
        return None


def count_tokens(text: str) -> int:
    """Count cl100k tokens, or use a conservative UTF-8 byte upper bound."""
    encoding = _token_encoding()
    if encoding is None:
        return len((text or "").encode("utf-8"))
    return len(encoding.encode(text or "", disallowed_special=()))


def tokenizer_name() -> str:
    return "cl100k_base" if _token_encoding() is not None else "utf8_byte_upper_bound"


TRUNCATION_MARKER = "\n...[truncated]"


def truncate_to_token_budget(text: str, budget: int) -> tuple[str, int, bool]:
    """Return text fitted to a token budget, consumed tokens and truncation flag."""
    if budget <= 0:
        return "", 0, bool(text)
    encoding = _token_encoding()
    if encoding is None:
        tokens = list((text or "").encode("utf-8"))
    else:
        tokens = encoding.encode(text or "", disallowed_special=())
    if len(tokens) <= budget:
        return text, len(tokens), False
    marker = TRUNCATION_MARKER
    marker_tokens = (
        list(marker.encode("utf-8"))
        if encoding is None
        else encoding.encode(marker, disallowed_special=())
    )
    body_budget = max(0, budget - len(marker_tokens))
    if encoding is None:
        body = bytes(tokens[:body_budget]).decode("utf-8", errors="ignore")
    else:
        body = encoding.decode(tokens[:body_budget])
    fitted = body + marker
    return fitted, min(budget, count_tokens(fitted)), True


def _covered_chars(fitted: str, clipped: bool, full_length: int) -> int:
    """Characters of the original text that the fitted context provably keeps.

    A fitted body is a token-aligned prefix of the input, so the character count
    is an upper bound on what the model actually sees. A hunk straddling it must
    therefore be treated as partial, never as complete.
    """
    if not clipped:
        return full_length
    if fitted.endswith(TRUNCATION_MARKER):
        return len(fitted) - len(TRUNCATION_MARKER)
    return len(fitted)


def _hunk_buckets(hunk_texts: list[str], covered_chars: int) -> tuple[int, int, int]:
    """Split hunks into (complete, partial, omitted) for a prefix cover of their join."""
    complete = partial = omitted = 0
    offset = 0
    for raw in hunk_texts:
        end = offset + len(raw)
        if end <= covered_chars:
            complete += 1
        elif offset < covered_chars:
            partial += 1
        else:
            omitted += 1
        offset = end + 1  # the "\n" used to join hunks into one file_text
    return complete, partial, omitted


def _cover(entries: dict[str, ChunkCoverage], path: str, **fields) -> None:
    """Record what happened to one changed file without ever dropping the entry."""
    base = entries.get(path) or ChunkCoverage(path=path)
    entries[path] = base.model_copy(update=fields)



LOCKFILE_NAMES = {
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
}

SKIP_PATCH_PREFIXES = (
    "docs/demo/",
    "reports/",
)

SKIP_PATCH_REASONS: dict[str, str] = {
    "docs/demo/": "demo report (generated artifact)",
    "reports/": "generated report",
}

# Deliberately narrower than repository_context.safe_source_path: a reviewer still
# needs to see go.mod, requirements.txt or a stylesheet, so only secret material
# and checked-in build output are refused here.
_VENDORED_DIRS = {".git", "node_modules", "vendor", "dist", "build"}
_SECRET_ADJACENT_NAMES = {".npmrc", ".pypirc", ".netrc", ".htpasswd", "id_rsa", "id_ed25519"}
_SECRET_ADJACENT_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk"}
_DOCUMENTATION_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})


def _documentation_reason(filename: str) -> str | None:
    """Documentation has no runtime behaviour, so it must not eat patch budget.

    Kept out of ``_skip_patch_reason`` on purpose: that helper also decides
    whether a path is safe to index, and documentation still belongs in the
    repository index even when it is not reviewed line by line.
    """
    if PurePosixPath(filename).suffix.lower() in _DOCUMENTATION_SUFFIXES:
        return "documentation_only_change"
    return None


def _secret_adjacent_reason(filename: str) -> str | None:
    """Why a changed path must not be pasted into a prompt or a public comment."""
    path = PurePosixPath(filename)
    if any(part in _VENDORED_DIRS for part in path.parts):
        return "vendored_path_not_analyzed"
    if path.name.startswith(".env") or path.name in _SECRET_ADJACENT_NAMES:
        return "secret_adjacent_path_not_analyzed"
    if path.suffix.lower() in _SECRET_ADJACENT_SUFFIXES:
        return "secret_adjacent_path_not_analyzed"
    return None


@dataclass
class ReviewContext:
    text: str
    truncated: bool
    status: StepStatus = StepStatus.SUCCESS
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    token_count: int = 0
    patch_token_budget: int = 0
    # Every file the patch context did not fully include, with why. `skipped_files`
    # keeps its published meaning (file absent from the patch context); `omissions`
    # also records clipping and full-file substitution.
    omissions: list[Omission] = field(default_factory=list)
    budgets: BudgetReport = field(
        default_factory=lambda: BudgetReport(scope="build_review_context")
    )
    # One entry per changed file; a file still marked "not_processed" at the end
    # is an accounting bug and shows up in coverage_summary.unaccounted_files.
    coverage: tuple[ChunkCoverage, ...] = ()
    coverage_summary: ChunkCoverageSummary = field(
        default_factory=ChunkCoverageSummary
    )




# File extensions that are typically binary and should not be parsed for patches
_BINARY_EXTENSIONS: set[str] = {
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".webp", ".tiff", ".psd",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    # Compilation artifacts
    ".pyc", ".pyo", ".class", ".o", ".obj", ".lib", ".dll", ".so", ".dylib",
    ".exe", ".wasm",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Media
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg",
    # Data / assets
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".ico", ".cur",
    ".db", ".sqlite",
    # Lockfiles (already handled elsewhere)
    ".lock",
}


def _has_binary_extension(filename: str) -> bool:
    """Check if file has a binary extension that should be skipped."""
    _, ext = os.path.splitext(filename.lower())
    return ext in _BINARY_EXTENSIONS


def _patch_has_encoding_issues(filename: str, patch_text: str | None) -> bool:
    """Check if patch content has problematic non-UTF-8 artifacts.

    Returns True if the file should be skipped due to encoding issues.
    """
    if not patch_text:
        return False
    # Check for high density of replacement characters (\\ufffd)
    replacement_count = patch_text.count("\ufffd")
    if replacement_count > 0 and len(patch_text) > 0:
        ratio = replacement_count / len(patch_text)
        # If more than 0.5% of characters are replacements, likely an encoding mismatch
        if ratio > 0.005:
            logger.debug(f"Encoding issue detected in {filename}: {replacement_count} replacement chars ({ratio:.1%})")
            return True
    # Check for NUL bytes (common in binary files misidentified as text)
    if "\x00" in patch_text:
        return True
    return False



def _skip_patch_reason(filename: str) -> str | None:
    try:
        if filename.startswith("docs/evaluation/") and filename.endswith(".json"):
            return "evaluation data (quoted external code, not executable source)"
        name = filename.split("/")[-1]
        if name in LOCKFILE_NAMES:
            return "lockfile"
        for prefix, reason in SKIP_PATCH_REASONS.items():
            if filename.startswith(prefix):
                return reason
        return None
    except Exception:
        return None


_CONTEXT_PACK_TOKEN_BUDGET = 1000


def _load_context_pack_text() -> str:
    """Load the project Review Guide as context pack text."""
    guide_path = Path(__file__).resolve().parent.parent.parent / "docs" / "review-guide.md"
    try:
        with open(guide_path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        logger.debug("docs/review-guide.md not found; skipping context pack")
        return ""


def _get_relevant_function_index(changed_files: list[str]) -> str:
    """Load functions-index.md and extract entries for changed files."""
    index_path = Path(__file__).resolve().parent.parent.parent / "docs" / "functions-index.md"
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, OSError):
        logger.debug("docs/functions-index.md not found; skipping function index")
        return ""

    # Normalise file paths (both Windows \\ and POSIX /)
    changed_set = set()
    for cf in changed_files:
        normalised = cf.replace("\\", "/")
        changed_set.add(normalised)
        changed_set.add(cf)

    sections = content.split("\n## ")
    relevant_lines: list[str] = ["## Function Index (relevant files only)"]

    for section in sections:
        if not section.strip():
            continue
        # First line is the heading like `src\analyzer\context_builder.py`
        first_line = section.split("\n")[0].strip().strip("`")
        # Check if this section's file is in the changed files
        if any(cf in first_line or cf.replace("/", "\\") in first_line for cf in changed_set):
            relevant_lines.append("")
            relevant_lines.append(section.strip())

    if len(relevant_lines) <= 1:
        return ""

    return "\n".join(relevant_lines)


def _build_context_pack(
    changed_files: list[str],
    budget: int = _CONTEXT_PACK_TOKEN_BUDGET,
    small_pr: bool = False,
) -> str:
    """Build Context Pack string from review guide and function index.

    For small PRs (≤5 files), the full review guide is injected instead of
    a condensed summary, giving the model complete project conventions context.
    """
    parts: list[str] = []
    remaining = budget

    # 1. Review Guide (prioritised: project conventions)
    guide = _load_context_pack_text()
    if guide and remaining > 0:
        if small_pr:
            # Small PR — inject full guide for richer context
            guide_text = guide
            guide_text, _, _ = truncate_to_token_budget(guide_text, remaining)
        else:
            # Large PR — condensed version (headers + key rules only)
            guide_lines = guide.split("\n")
            condensed: list[str] = []
            for line in guide_lines:
                if line.startswith("#") or line.startswith("- **") or line.startswith("|"):
                    condensed.append(line)
            guide_text = "\n".join(condensed)
            guide_text, _, _ = truncate_to_token_budget(guide_text, remaining)
        parts.append("## Project Review Conventions")
        parts.append(guide_text)
        remaining -= count_tokens(guide_text)

    # 2. Function index for changed files
    if remaining > 0:
        func_index = _get_relevant_function_index(changed_files)
        if func_index:
            func_index, _, _ = truncate_to_token_budget(func_index, remaining)
            parts.append(func_index)
            remaining -= count_tokens(func_index)

    result = "\n\n".join(parts)
    if result:
        result = "\n\n---\n\n" + result + "\n\n---"
    return result


_FROZEN_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")


def _is_frozen_ref(ref: str | None) -> bool:
    """Only an immutable 40-hex commit identifies the code being reviewed."""
    return bool(ref) and _FROZEN_SHA_RE.match(ref or "") is not None


def _fetch_repo_file_text(repo: str, path: str, ref: str | None) -> str | None:
    """Read one file at an immutable commit; never a different ref.

    A branch name, a missing SHA or a failed read all return None. The earlier
    behaviour of retrying on `main` is deliberately gone: it could put code that
    is not part of the reviewed revision into the review context while the
    report still claimed to describe the PR.
    """
    if not _is_frozen_ref(ref):
        logger.debug("Refusing to read %s at non-frozen ref %r", path, ref)
        return None
    settings = get_settings()
    try:
        with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
            try:
                return gh.get_file_contents(repo, path, ref=ref)
            except GitHubApiError as e:
                logger.debug(f"Failed to fetch {repo}/{path}@{ref}: {e}")
                return None
    except Exception as e:
        logger.warning(f"Error while fetching file contents for {repo}/{path}: {e}")
        return None


_PARTIAL_INCLUSION_REASONS = frozenset(
    {
        "patch_clipped",
        "full_file_substituted_for_missing_patch",
        "full_file_substituted_for_truncated_hunk",
    }
)


def partial_inclusion_counts(context: ReviewContext) -> dict[str, int]:
    """Files that reached the prompt only in part, by reason.

    These are not skipped files: they are present but incomplete, which is why
    they need their own line instead of hiding behind the truncated flag.
    """
    counts: dict[str, int] = {}
    for omission in context.omissions:
        if omission.reason in _PARTIAL_INCLUSION_REASONS:
            counts[omission.reason] = counts.get(omission.reason, 0) + 1
    return counts


def partial_inclusion_warning(context: ReviewContext) -> str | None:
    """One warning line naming every partial inclusion, or None when there is none."""
    counts = partial_inclusion_counts(context)
    if not counts:
        return None
    detail = ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
    return f"{sum(counts.values())} file(s) only partly in the AI patch context: {detail}"


def build_review_context(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding] | None = None,
    *,
    include_rule_findings: bool = False,
    prioritize_rule_files: bool = False,
    include_context_pack: bool = True,
    allow_remote_file_fetch: bool = True,
    max_patch_tokens: int = 6_000,
) -> ReviewContext:
    # Input validation: require a valid PullRequest object
    if not isinstance(pr, PullRequest):
        raise TypeError("pr must be a PullRequest instance")
    if files is None:
        files = []
    if findings is None:
        findings = []

    # Detect empty/invalid PR (e.g., from failed API call)
    if (hasattr(pr, 'title') and not pr.title and pr.number == 0) or not pr.repo:
        error_text = (
            "# Analysis Failed\n\n"
            "The pull request information could not be retrieved from GitHub API.\n"
            "Possible reasons: invalid token, network issue, or the PR does not exist.\n"
            "No analysis was performed."
        )
        return ReviewContext(
            text=error_text,
            truncated=False,
            status=StepStatus.FAILED,
            token_count=count_tokens(error_text),
            patch_token_budget=max_patch_tokens,
        )

    def safe_attr(obj, attr, default):
        try:
            val = getattr(obj, attr, default)
            return val if val is not None else default
        except Exception:
            return default

    parts: list[str] = [
        "# Pull Request",
        f"Repo: {safe_attr(pr, 'repo', 'unknown')}",
        f"Number: {safe_attr(pr, 'number', 0)}",
        f"Title: {safe_attr(pr, 'title', '')}",
        f"Author: {safe_attr(pr, 'author', 'unknown')}",
        f"Base: {safe_attr(pr, 'base_ref', 'unknown')}",
        f"Head: {safe_attr(pr, 'head_ref', 'unknown')}",
        "",
        "## Description",
        safe_attr(pr, 'body', '(empty)') or '(empty)',
        "",
        "## Changed files",
    ]

    # Spend the budget on what matters. The over-budget policy below clips in file
    # order, so the selection rule is the order: files whose hunks the free rule/AST
    # sweep flagged come first, then the largest. Suspicion is a priority, not a
    # filter - a diff with nothing flagged must still reach the model, which is why
    # nothing is dropped here.
    parsed_hunks = {file.filename: parse_file_hunks(file) for file in files}
    triage_priority: dict[str, int] = {}
    if max_patch_tokens > 0 and count_tokens(
        "\n".join(file.patch or "" for file in files)
    ) > max_patch_tokens:
        from src.analyzer.tiered_scan import triage

        flagged = {
            (path, hunk.new_start)
            for path, hunk, _score, _reasons in triage(files, parsed_hunks, findings).suspicious
        }
        priority: dict[str, tuple[int, int]] = {}
        for file in files:
            hunks = parsed_hunks.get(file.filename, [])
            priority[file.filename] = (
                sum(1 for h in hunks if (file.filename, h.new_start) in flagged),
                sum(h.new_count + h.old_count for h in hunks),
            )
        files = sorted(files, key=lambda file: priority.get(file.filename, (0, 0)), reverse=True)
        triage_priority = {path: values[0] for path, values in priority.items()}
    for file in files:
        try:
            status = getattr(file, 'status', 'unknown')
            if hasattr(status, 'value'):
                status = status.value
            parts.append(
                f"- {file.filename} [{status}] +{file.additions}/-{file.deletions}"
            )
        except Exception as e:
            logger.warning(f"Failed to add file info for {getattr(file, 'filename', '?')}: {e}")
            continue

    # ── Context Pack injection ──
    context_pack = ""
    if include_context_pack:
        context_pack = _build_context_pack([f.filename for f in files], small_pr=len(files) <= 5)
    if context_pack:
        parts.append(context_pack)

    if include_rule_findings and findings:
        parts.extend(["", "## Rule findings"])
        for finding in findings:
            try:
                line = f":{finding.line}" if finding.line else ""
                parts.append(
                    f"- {finding.severity.value} {finding.file_path}{line} "
                    f"{finding.rule_id}: {finding.title}. Evidence: {finding.evidence}"
                )
            except Exception as e:
                logger.warning(f"Failed to add finding: {e}")
                continue

    parts.extend(["", "## Patches"])
    patch_budget = max_patch_tokens
    truncated = False
    # Triage leads only when the over-budget pass found suspicious hunks;
    # production path and size remain deterministic tie-breaks. A large file
    # still cannot monopolize more than its per-file share.
    ordered_files = sorted(files, key=lambda f: (
        -triage_priority.get(f.filename, 0),
        prioritize_rule_files and not any(r.file_path == f.filename for r in findings),
        not f.filename.startswith(("src/", ".github/", "vscode-extension/src/")),
        -(f.additions + f.deletions), f.filename,
    ))
    per_file_budget = max(1, max_patch_tokens // min(8, max(1, len(files))))
    skipped_files: list[tuple[str, str]] = []
    extra_omissions: list[Omission] = []
    # A file never touched by the loop stays "not_processed" and is reported as
    # unaccounted, so a missing branch cannot silently drop a changed file.
    coverage_by_path = {
        file.filename: ChunkCoverage(path=file.filename, status="excluded", reason="not_processed")
        for file in files
    }
    max_file_tokens = 0
    lockfiles_skipped = 0
    partial_flag = False

    for file in ordered_files:
        try:
            reason = _skip_patch_reason(file.filename)
            if reason is None:
                reason = _secret_adjacent_reason(file.filename)
            if reason is None:
                reason = _documentation_reason(file.filename)
            if reason is not None:
                skipped_files.append((file.filename, reason))
                _cover(coverage_by_path, file.filename, status="excluded", reason=reason)
                if reason == "lockfile":
                    lockfiles_skipped += 1
                continue

            if patch_budget <= 0:
                # Count the dropped hunks even though they are not rendered: a
                # shrinking budget must not shrink the denominator, or every
                # coverage ratio silently starts lying.
                dropped = parse_file_hunks(file)
                skipped_files.append((file.filename, "patch_budget_exhausted"))
                _cover(
                    coverage_by_path,
                    file.filename,
                    status="excluded",
                    reason="patch_budget_exhausted",
                    hunks_total=len(dropped),
                    hunks_omitted=len(dropped),
                )
                truncated = True
                partial_flag = True
                continue

            if file.patch and any(line.startswith(("Binary files ", "GIT binary patch"))
                                  for line in file.patch.splitlines()):
                skipped_files.append((file.filename, "binary_content_not_analyzed"))
                _cover(
                    coverage_by_path,
                    file.filename,
                    status="excluded",
                    reason="binary_content_not_analyzed",
                )
                partial_flag = True
                continue

            # Check for binary or encoding issues before processing
            if _has_binary_extension(file.filename) or _patch_has_encoding_issues(file.filename, file.patch):
                skipped_files.append((file.filename, "encoding_skipped"))
                _cover(
                    coverage_by_path, file.filename, status="excluded", reason="encoding_skipped"
                )
                partial_flag = True
                continue

            parse_failed = False
            try:
                hunks = parse_file_hunks(file)
            except Exception as e:
                logger.warning(f"Failed to parse hunks for {file.filename}: {e}")
                hunks = []
                parse_failed = True
                partial_flag = True

            # If hunks empty and either parsing failed, patch missing, or file added/removed, try fallback to full file content
            if not hunks and (parse_failed or file.patch is None or file.patch == "" or file.status in (FileStatus.ADDED, FileStatus.REMOVED)):
                # Only immutable commits may be read: a branch name would mix
                # code that is not part of the reviewed revision into the context.
                if file.status == FileStatus.ADDED:
                    ref_try = pr.head_sha
                elif file.status == FileStatus.REMOVED:
                    ref_try = pr.base_sha
                else:
                    ref_try = pr.head_sha or pr.base_sha

                file_contents = None
                if allow_remote_file_fetch and _is_frozen_ref(ref_try):
                    try:
                        file_contents = _fetch_repo_file_text(pr.repo, file.filename, ref_try)
                    except Exception as e:
                        logger.debug(f"Fetching file contents fallback failed for {file.filename}: {e}")

                if file_contents:
                    file_text, consumed, clipped = truncate_to_token_budget(
                        file_contents, min(patch_budget, per_file_budget)
                    )
                    truncated = truncated or clipped
                    partial_flag = partial_flag or clipped
                    max_file_tokens = max(max_file_tokens, consumed)
                    extra_omissions.append(
                        Omission(
                            path=file.filename,
                            reason="full_file_substituted_for_missing_patch",
                            detail=(
                                "no usable diff hunk; frozen whole file substituted"
                                f" (truncated={clipped})"
                            ),
                        )
                    )
                    parts.extend(["", f"### {file.filename}", "```", file_text, "```"])
                    _cover(
                        coverage_by_path,
                        file.filename,
                        mode="substituted",
                        status="partial" if clipped else "included",
                        hunks_total=0,
                        reason="full_file_substituted_for_missing_patch",
                    )
                    patch_budget -= consumed
                    if patch_budget <= 0:
                        parts.append("\nPatch budget exhausted. Remaining files omitted.")
                        truncated = True
                        partial_flag = True
                    continue
                else:
                    # Couldn't fetch full file; mark partial and continue
                    miss_reason = (
                        "patch_unavailable" if _is_frozen_ref(ref_try) else "no_frozen_ref"
                    )
                    logger.warning(
                        f"Could not retrieve full contents for {file.filename} during fallback"
                    )
                    partial_flag = True
                    skipped_files.append((file.filename, miss_reason))
                    _cover(
                        coverage_by_path, file.filename, status="excluded", reason=miss_reason
                    )
                    continue

            if not hunks:
                reason = "patch_parse_failed" if parse_failed else "patch_without_hunks"
                partial_flag = True
                skipped_files.append((file.filename, reason))
                _cover(coverage_by_path, file.filename, status="excluded", reason=reason)
                continue

            hunk_texts = [h.raw for h in hunks]
            file_text = "\n".join(hunk_texts)
            # If hunks seem incomplete (e.g., new_count differs from added lines), attempt to fetch full file
            total_added = sum(len(h.added_lines) for h in hunks)
            total_hunk_lines = sum(h.new_count for h in hunks)
            substituted_reason = None
            if allow_remote_file_fetch and total_hunk_lines > 0 and total_added < total_hunk_lines // 2:
                # Hunk likely truncated: read the frozen file and record that the
                # context now carries whole-file code instead of the diff.
                file_contents = _fetch_repo_file_text(
                    pr.repo, file.filename, pr.head_sha
                ) or _fetch_repo_file_text(pr.repo, file.filename, pr.base_sha)
                if file_contents:
                    file_text = file_contents
                    substituted_reason = "full_file_substituted_for_truncated_hunk"
                    extra_omissions.append(
                        Omission(
                            path=file.filename,
                            reason=substituted_reason,
                            detail=(
                                f"diff hunk showed {total_added} added of"
                                f" {total_hunk_lines} new lines"
                            ),
                        )
                    )

            limit = min(patch_budget, per_file_budget)
            file_text, consumed, clipped = truncate_to_token_budget(file_text, limit)
            truncated = truncated or clipped
            partial_flag = partial_flag or clipped
            max_file_tokens = max(max_file_tokens, consumed)
            if substituted_reason is not None:
                _cover(
                    coverage_by_path,
                    file.filename,
                    mode="substituted",
                    status="partial" if clipped else "included",
                    hunks_total=len(hunks),
                    reason=substituted_reason,
                )
            else:
                complete, part, omitted_hunks = _hunk_buckets(
                    hunk_texts,
                    _covered_chars(file_text, clipped, len("\n".join(hunk_texts))),
                )
                if not part and not omitted_hunks:
                    chunk_status, chunk_reason = "included", None
                elif complete or part:
                    chunk_status, chunk_reason = "partial", "patch_clipped"
                else:
                    # Nothing of this file is provably in the prompt: saying
                    # "partial" here would overstate what the model can see.
                    chunk_status, chunk_reason = "excluded", "patch_budget_too_small"
                _cover(
                    coverage_by_path,
                    file.filename,
                    status=chunk_status,
                    reason=chunk_reason,
                    hunks_total=len(hunks),
                    hunks_complete=complete,
                    hunks_partial=part,
                    hunks_omitted=omitted_hunks,
                )
            if clipped:
                extra_omissions.append(
                    Omission(
                        path=file.filename,
                        reason="patch_clipped",
                        detail=f"kept {consumed} tokens of this file's diff under the per-file cap",
                    )
                )
            parts.extend(["", f"### {file.filename}", "```diff", file_text, "```"])
            patch_budget -= consumed
            if patch_budget <= 0:
                parts.append("\nPatch budget exhausted. Remaining files omitted.")
                truncated = True
                partial_flag = True
        except Exception as e:
            logger.warning(f"Failed to process patch for {getattr(file, 'filename', '?')}: {e}")
            partial_flag = True
            failed_path = getattr(file, "filename", "?")
            skipped_files.append((failed_path, "patch_processing_error"))
            _cover(
                coverage_by_path, failed_path, status="excluded", reason="patch_processing_error"
            )
            continue

    if lockfiles_skipped > 0 or skipped_files:
        parts.append("")
        if lockfiles_skipped > 0:
            parts.append(
                f"({lockfiles_skipped} lockfile(s) excluded from patch context: "
                f"only change statistics are shown.)"
            )
        for fpath, freason in skipped_files:
            if freason != "lockfile":
                parts.append(
                    f"(`{fpath}` excluded from patch context "
                    f"({freason}): only change statistics are shown.)"
                )

    # Determine final status
    if partial_flag:
        status = StepStatus.PARTIAL
    else:
        status = StepStatus.SUCCESS

    # Additional check: if there are no files, it's partial
    if not files:
        status = StepStatus.PARTIAL

    context_text = "\n".join(parts)
    # One ledger for the whole artifact: exclusions published to the report plus
    # the partial-inclusion cases the report does not show as skipped files.
    omissions = [Omission(path=fpath, reason=freason) for fpath, freason in skipped_files]
    seen = {(o.path, o.reason) for o in omissions}
    for extra in extra_omissions:
        if (extra.path, extra.reason) not in seen:
            omissions.append(extra)
            seen.add((extra.path, extra.reason))
    budgets = BudgetReport(
        scope="build_review_context",
        lines=(
            BudgetLine(
                name="context_pack",
                limit=_CONTEXT_PACK_TOKEN_BUDGET,
                used=count_tokens(context_pack),
                covers="project conventions and function index prepended to the prompt",
            ),
            BudgetLine(
                name="patch_context",
                limit=max_patch_tokens,
                used=max(0, max_patch_tokens - max(patch_budget, 0)),
                covers=(
                    "patch and substituted-file fragments put in the user prompt; excludes "
                    "the system prompt, repository evidence reads and model output"
                ),
            ),
            BudgetLine(
                name="per_file_patch",
                limit=per_file_budget,
                used=max_file_tokens,
                covers="cap on a single file's share of the patch context",
                aggregate=False,
            ),
        ),
        omissions=tuple(omissions),
    )
    coverage = tuple(coverage_by_path[file.filename] for file in files)
    coverage_summary = ChunkCoverageSummary.from_entries(
        [file.filename for file in files], coverage
    )
    return ReviewContext(
        text=context_text,
        truncated=truncated,
        status=status,
        skipped_files=skipped_files,
        token_count=count_tokens(context_text),
        patch_token_budget=max_patch_tokens,
        omissions=omissions,
        budgets=budgets,
        coverage=coverage,
        coverage_summary=coverage_summary,
    )
