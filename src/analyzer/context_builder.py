from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, PullRequest, RiskFinding

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


@dataclass
class ReviewContext:
    text: str
    truncated: bool
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    # (file_path, reason)


def _skip_patch_reason(filename: str) -> str | None:
    try:
        name = filename.split("/")[-1]
        if name in LOCKFILE_NAMES:
            return "lockfile"
        for prefix, reason in SKIP_PATCH_REASONS.items():
            if filename.startswith(prefix):
                return reason
        return None
    except Exception:
        return None


def build_review_context(
    pr: Optional[PullRequest],
    files: Optional[list[ChangedFile]],
    findings: Optional[list[RiskFinding]],
    max_patch_chars: int = 24_000,
) -> ReviewContext:
    # 防御性输入校验
    if pr is None:
        # 创建空 PR 占位符
        pr = PullRequest(
            repo="unknown",
            number=0,
            title="",
            body="",
            author="",
            base_ref="",
            head_ref="",
            html_url=None,
        )
    if files is None:
        files = []
    if findings is None:
        findings = []

    # 安全获取 PR 属性
    def safe_pr_attr(attr: str, default: str = "unknown") -> str:
        try:
            val = getattr(pr, attr, default)
            return val if val is not None else default
        except Exception:
            return default

    parts: list[str] = [
        "# Pull Request",
        f"Repo: {safe_pr_attr('repo')}",
        f"Number: {safe_pr_attr('number', '0')}",
        f"Title: {safe_pr_attr('title', '')}",
        f"Author: {safe_pr_attr('author', 'unknown')}",
        f"Base: {safe_pr_attr('base_ref', 'unknown')}",
        f"Head: {safe_pr_attr('head_ref', 'unknown')}",
        "",
        "## Description",
        pr.body or "(empty)" if hasattr(pr, "body") else "(empty)",
        "",
        "## Changed files",
    ]

    # 文件列表（安全遍历）
    for file in files:
        try:
            status = getattr(file, "status", "unknown")
            if hasattr(status, "value"):
                status = status.value
            parts.append(
                f"- {file.filename} [{status}] +{file.additions}/-{file.deletions}"
            )
        except Exception as e:
            print(f"[WARN] 添加文件信息失败: {e}")
            continue

    # 规则发现（安全遍历）
    if findings:
        parts.extend(["", "## Rule findings"])
        for finding in findings:
            try:
                line = f":{finding.line}" if finding.line else ""
                parts.append(
                    f"- {finding.severity.value} {finding.file_path}{line} "
                    f"{finding.rule_id}: {finding.title}. Evidence: {finding.evidence}"
                )
            except Exception as e:
                print(f"[WARN] 添加规则发现失败: {e}")
                continue

    parts.extend(["", "## Patches"])
    patch_budget = max_patch_chars
    truncated = False
    ordered_files = sorted(
        files,
        key=lambda f: (
            not any(r.file_path == f.filename for r in findings),
            -(f.additions + f.deletions),
        ),
    )
    skipped_files: list[tuple[str, str]] = []
    lockfiles_skipped = 0

    for file in ordered_files:
        try:
            reason = _skip_patch_reason(file.filename)
            if reason is not None:
                skipped_files.append((file.filename, reason))
                if reason == "lockfile":
                    lockfiles_skipped += 1
                continue

            # 安全解析 hunks
            try:
                hunks = parse_file_hunks(file)
            except Exception as e:
                print(f"[WARN] 解析文件 {file.filename} 的 hunks 失败: {e}")
                continue

            if not hunks:
                continue

            file_text = "\n".join(h.raw for h in hunks)
            if len(file_text) > patch_budget:
                file_text = file_text[:patch_budget] + "\n...[truncated]"
            parts.extend(["", f"### {file.filename}", "```diff", file_text, "```"])
            patch_budget -= len(file_text)
            if patch_budget <= 0:
                parts.append("\nPatch budget exhausted. Remaining files omitted.")
                truncated = True
                break
        except Exception as e:
            print(f"[WARN] 处理文件 {file.filename} 的 patch 时出错: {e}")
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

    return ReviewContext(
        text="\n".join(parts),
        truncated=truncated,
        skipped_files=skipped_files,
    )