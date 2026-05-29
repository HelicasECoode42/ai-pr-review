from __future__ import annotations

from src.models import ReviewReport, ReviewSuggestion, Severity

# ── Chinese rule text mappings ────────────────────────────

_RULE_ZH: dict[str, tuple[str, str]] = {
    "sql-string-concat": (
        "可能存在 SQL 注入风险",
        "请使用参数化查询或 ORM 查询构建器，避免字符串拼接。",
    ),
    "shell-execution": (
        "Shell 命令执行代码有变更",
        "请校验用户输入，避免使用 shell=True 或拼接命令字符串。",
    ),
    "dynamic-execution": (
        "引入了动态代码执行",
        "请避免使用 eval/exec，或对输入做严格沙箱化与校验。",
    ),
    "secret-logging": (
        "可能记录敏感信息",
        "请勿将 token、密码、密钥等写入日志，输出前脱敏处理。",
    ),
    "swallowed-exception": (
        "异常处理可能隐藏故障",
        "请记录足够上下文，必要时重新抛出或返回明确错误。",
    ),
    "test-skip": (
        "引入了测试跳过",
        "请确认跳过原因已明确说明并有后续跟进计划。",
    ),
    "risk-path": (
        "高风险路径有变更",
        "请仔细审查权限、数据完整性和回滚行为。",
    ),
    "test-assertion-removed": (
        "测试断言被移除",
        "请确认对应覆盖被等价替代，或说明断言过时的原因。",
    ),
}

_SEVERITY_ZH: dict[Severity, str] = {
    Severity.CRITICAL: "严重",
    Severity.HIGH: "高",
    Severity.MEDIUM: "中",
    Severity.LOW: "低",
}


def render_markdown(report: ReviewReport, language: str = "en") -> str:
    zh = language == "zh"
    T = _Translator(zh)

    title_line = f"# {T.t('AI PR Review 报告')}: {report.pr.repo}#{report.pr.number}"
    if not zh:
        title_line = f"# AI PR Review: {report.pr.repo}#{report.pr.number}"

    lines = [title_line, "", f"## {T.t('PR 概览')}", ""]

    additions = sum(f.additions for f in report.files)
    deletions = sum(f.deletions for f in report.files)

    lines.extend(
        [
            f"| {T.t('字段')} | {T.t('值')} |",
            "|---|---|",
            f"| {T.t('仓库')} | `{report.pr.repo}` |",
            f"| PR | [#{report.pr.number}]({report.pr.html_url or '#'}) |",
            f"| {T.t('标题')} | {report.pr.title} |",
            f"| {T.t('作者')} | {report.pr.author or T.t('unknown')} |",
            f"| {T.t('基准分支')} | `{report.pr.base_ref or T.t('unknown')}` |",
            f"| {T.t('源分支')} | `{report.pr.head_ref or T.t('unknown')}` |",
            f"| {T.t('变更文件数')} | {len(report.files)} |",
            f"| {T.t('新增 / 删除')} | +{additions} / -{deletions} |",
            f"| {T.t('整体风险')} | **`{report.risk_level.value.upper()}`** |",
            f"| {T.t('是否使用 AI')} | {T.t('yes') if report.used_ai else T.t('no')} |",
        ]
    )

    if report.ai_failure_reason:
        lines.append(f"| {T.t('AI 失败原因')} | {report.ai_failure_reason} |")
    if report.context_truncated:
        lines.append(
            f"| {T.t('上下文')} | {T.t('由于 PR diff 较大，部分 patch 上下文已被裁剪')} |"
        )

    # ── risk summary ──
    severity_counts = _count_by_severity(report.suggestions)
    lines.extend(["", f"### {T.t('风险统计')}", ""])
    if severity_counts and any(severity_counts.values()):
        for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW):
            count = severity_counts.get(sev, 0)
            if count > 0:
                label = _SEVERITY_ZH.get(sev, sev.value) if zh else sev.value.upper()
                lines.append(f"- **{label}**: {count} {T.t('条建议')}")
    else:
        lines.append(T.t("未检测到风险项。"))

    # ── summary ──
    lines.extend(["", f"## {T.t('变更总结')}", "", report.summary])

    # ── changed files ──
    lines.extend(["", f"## {T.t('文件变更')}", ""])
    header_status = T.t("状态")
    header_pm = "+/-"
    lines.append(f"| {T.t('文件')} | {header_status} | {header_pm} |")
    lines.append("|---|---|---|")
    for file in report.files:
        lines.append(
            f"| `{file.filename}` | `{file.status.value}` "
            f"| +{file.additions}/-{file.deletions} |"
        )
    lines.append(
        f"| **{T.t('合计')}** ({len(report.files)} {T.t('个文件')}) "
        f"| | **+{additions}/-{deletions}** |"
    )

    # ── review scope ──
    if report.skipped_context_files:
        lines.extend(["", f"## {T.t('评审范围')}", ""])
        included_count = len(report.files) - len(report.skipped_context_files)
        if zh:
            lines.append(
                f"本次 PR 共变更 {len(report.files)} 个文件，"
                f"其中 {included_count} 个进入 AI patch 上下文，"
                f"{len(report.skipped_context_files)} 个仅展示变更统计。"
            )
        else:
            lines.append(
                f"This PR changed {len(report.files)} file(s); "
                f"{included_count} included in AI patch context, "
                f"{len(report.skipped_context_files)} shown as statistics only."
            )
        lines.extend(["", f"| {T.t('文件')} | {T.t('处理方式')} | {T.t('原因')} |"])
        lines.append("|---|---|---|")
        for skipped in report.skipped_context_files:
            reason_zh = _skip_reason_zh(skipped.reason)
            reason_display = reason_zh if zh else skipped.reason
            lines.append(
                f"| `{skipped.file_path}` | "
                f"{T.t('跳过 patch')} | {reason_display} |"
            )

    # ── suggestions ──
    lines.extend(["", f"## {T.t('评审建议')}", ""])
    if not report.suggestions:
        lines.append(T.t("未生成高信号建议。"))
    else:
        lines.append(
            f"| # | {T.t('严重程度')} | {T.t('位置')} "
            f"| {T.t('置信度')} | {T.t('标题')} |"
        )
        lines.append("|---|---|---|---|---|")
        for index, s in enumerate(report.suggestions, start=1):
            location = s.file_path
            if s.line is not None:
                location = f"{location}:{s.line}"
            sev_display = _SEVERITY_ZH.get(s.severity, s.severity.value) if zh else s.severity.value.upper()
            lines.append(
                f"| {index} | `{sev_display}` "
                f"| `{location}` | {s.confidence:.0%} | {s.title} |"
            )

        lines.extend(["", "---", ""])
        for index, s in enumerate(report.suggestions, start=1):
            location = s.file_path
            if s.line is not None:
                location = f"{location}:{s.line}"
            sev_display = _SEVERITY_ZH.get(s.severity, s.severity.value) if zh else s.severity.value.upper()
            lines.extend(
                [
                    f"### {index}. [{sev_display}] {s.title}",
                    "",
                    f"- **{T.t('位置')}**: `{location}`",
                    f"- **{T.t('置信度')}**: {s.confidence:.0%}",
                    f"- **{T.t('原因')}**: {s.reason}",
                    f"- **{T.t('建议')}**: {s.recommendation}",
                    "",
                    "<details>",
                    f"<summary>{T.t('可复制 GitHub 评论')}</summary>",
                    "",
                    f"**{s.severity.value}**: {s.title}",
                    "",
                    f"> {s.reason}",
                    "",
                    f"{T.t('建议')}: {s.recommendation}",
                    "",
                    "</details>",
                    "",
                ]
            )

    # ── rule findings ──
    visible_findings = [f for f in report.rule_findings if f.confidence >= 0.65]
    hidden_findings = [f for f in report.rule_findings if f.confidence < 0.65]
    lines.extend(["", f"## {T.t('规则扫描结果')}", ""])
    if not report.rule_findings:
        lines.append(T.t("未命中规则。"))
    elif not visible_findings:
        lines.append(T.t("所有规则命中均为低置信度，已隐藏。"))
    else:
        lines.append(
            f"| {T.t('严重程度')} | {T.t('规则')} | {T.t('位置')} | {T.t('发现')} |"
        )
        lines.append("|---|---|---|---|")
        for finding in visible_findings:
            location = finding.file_path
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            sev_display = _SEVERITY_ZH.get(finding.severity, finding.severity.value) if zh else finding.severity.value
            zh_title = _RULE_ZH.get(finding.rule_id, (finding.title,))[0]
            display_title = zh_title if zh else finding.title
            lines.append(
                f"| `{sev_display}` | `{finding.rule_id}` "
                f"| `{location}` | {display_title} |"
            )

    total_hidden = report.hidden_suggestions_count + len(hidden_findings)
    # ── analysis notes ──
    if report.analysis_warnings or total_hidden > 0:
        lines.extend(["", f"## {T.t('分析备注')}", ""])
        if report.hidden_suggestions_count > 0:
            lines.append(
                f"- {report.hidden_suggestions_count} "
                f"{T.t('条低置信度或重复建议已从主结果中隐藏')}"
            )
        if hidden_findings:
            lines.append(
                f"- {len(hidden_findings)} "
                f"{T.t('条低置信度规则命中已从主结果中隐藏')}"
            )
        for warning in report.analysis_warnings:
            lines.append(f"- {_translate_warning(warning, zh)}")

    return "\n".join(lines).rstrip() + "\n"


class _Translator:
    def __init__(self, zh: bool) -> None:
        self._zh = zh
        self._map: dict[str, str] = {
            # section headers
            "PR 概览": "PR Overview",
            "风险统计": "Risk Summary",
            "变更总结": "Executive Summary",
            "文件变更": "Changed Files",
            "评审建议": "Review Suggestions",
            "规则扫描结果": "Rule Findings",
            "分析备注": "Analysis Notes",
            # table fields
            "字段": "Field",
            "值": "Value",
            "仓库": "Repository",
            "标题": "Title",
            "作者": "Author",
            "基准分支": "Base",
            "源分支": "Head",
            "变更文件数": "Files changed",
            "新增 / 删除": "Additions / Deletions",
            "整体风险": "Overall risk",
            "是否使用 AI": "AI used",
            "AI 失败原因": "AI failure",
            "上下文": "Context",
            "由于 PR diff 较大，部分 patch 上下文已被裁剪": (
                "Patch context was truncated; some files not analyzed by AI"
            ),
            # risk summary
            "条建议": "suggestion(s)",
            "未检测到风险项。": "No risk items detected.",
            # changed files
            "文件": "File",
            "状态": "Status",
            "合计": "Total",
            "个文件": "files",
            # suggestions
            "未生成高信号建议。": "No high-signal suggestions generated.",
            "严重程度": "Severity",
            "位置": "Location",
            "置信度": "Confidence",
            "原因": "Reason",
            "建议": "Recommendation",
            "可复制 GitHub 评论": "Suggested GitHub comment",
            # rule findings
            "规则": "Rule",
            "发现": "Finding",
            "未命中规则。": "No rule findings.",
            # analysis notes
            "条低置信度或重复建议已从主结果中隐藏": (
                "low-confidence or duplicate suggestion(s) hidden from main results"
            ),
            "条低置信度规则命中已从主结果中隐藏": (
                "low-confidence rule finding(s) hidden from main results"
            ),
            "所有规则命中均为低置信度，已隐藏。": (
                "All rule findings are low-confidence and have been hidden."
            ),
            # review scope
            "评审范围": "Review Scope",
            "处理方式": "Action",
            "跳过 patch": "patch skipped",
            # misc
            "unknown": "unknown",
            "yes": "yes",
            "no": "no",
        }

    def t(self, zh_key: str) -> str:
        if self._zh:
            return zh_key
        return self._map.get(zh_key, zh_key)


_WARNING_ZH_MAP: dict[str, str] = {
    "Patch context was truncated to fit token budget; some files were not analyzed by AI.": (
        "由于 token 预算限制，Patch 上下文已被裁剪，部分文件未经 AI 分析。"
    ),
    "AI review unavailable": "AI Review 不可用",
    "Showing rule-based analysis only.": "仅展示规则分析结果。",
    "unchanged line": "未变更行",
    "or duplicate": "或重复",
    "suggestion(s) filtered out (low confidence, unchanged line, or duplicate)": (
        "条建议已过滤（低置信度、未变更行或重复）"
    ),
    "low confidence": "低置信度",
    "low-confidence": "低置信度",
}


def _translate_warning(warning: str, zh: bool) -> str:
    if not zh:
        return warning
    result = warning
    for en, zh_text in _WARNING_ZH_MAP.items():
        result = result.replace(en, zh_text)
    return result


def _skip_reason_zh(reason: str) -> str:
    mapping = {
        "lockfile": "lockfile，仅展示变更统计",
        "demo report (generated artifact)": "示例报告，避免评审生成内容",
        "generated report": "本地生成报告，仅展示变更统计",
    }
    return mapping.get(reason, reason)


def _count_by_severity(suggestions: list[ReviewSuggestion]) -> dict[Severity, int]:
    counts: dict[Severity, int] = {}
    for s in suggestions:
        counts[s.severity] = counts.get(s.severity, 0) + 1
    return counts
