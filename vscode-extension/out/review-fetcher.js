"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.fetchReview = fetchReview;
const git_1 = require("./git");
// ── Parser ─────────────────────────────────────────────
/**
 * Parse a bot review comment body into structured suggestion data.
 *
 * Expected format (from workflow):
 * ```
 * **high** (92%): title text
 *
 * reason text
 *
 * > recommendation text
 * ```
 */
function parseSuggestion(comment) {
    const body = comment.body;
    // Parse header: **severity** (confidence%): title
    const headerMatch = body.match(/^\*\*(\w+)\*\*\s*\((\d+)%\):\s*(.+)$/m);
    if (!headerMatch)
        return null;
    const severity = normalizeSeverity(headerMatch[1]);
    const confidence = parseInt(headerMatch[2], 10) / 100;
    const title = headerMatch[3].trim();
    // Extract reason: text between title line and "> recommendation"
    const rest = body.slice(headerMatch.index + headerMatch[0].length);
    let reason = "";
    let recommendation = "";
    // Split on the > recommendation line
    const recIdx = rest.indexOf("\n\n> ");
    if (recIdx !== -1) {
        reason = rest.slice(0, recIdx).trim();
        recommendation = rest.slice(recIdx + 3).replace(/^>\s?/gm, "").trim();
    }
    else {
        reason = rest.trim();
    }
    return {
        file_path: comment.path,
        line: comment.line ?? comment.original_line ?? null,
        severity,
        confidence,
        title,
        reason,
        recommendation,
    };
}
function normalizeSeverity(s) {
    const lower = s.toLowerCase();
    if (["critical", "high", "medium", "low"].includes(lower)) {
        return lower;
    }
    return "medium";
}
// ── Review meta parser ────────────────────────────────
/**
 * Parse the review meta table from the bot summary comment (markdown).
 * Expected format:
 * | 审查目标 Commit | - / [sha](url) / `sha` |
 * | 触发事件 | - / `value` |
 * | Workflow 运行 | - / [view run](url) |
 * | 更新时间 | - / timestamp |
 * | 审查模式 | ... |
 */
function parseReviewMeta(summaryMd) {
    if (!summaryMd)
        return null;
    const meta = {};
    // Match markdown table rows with key-value pairs
    // Chinese header row pattern
    const tableRegex = /^\|\s*(.+?)\s*\|\s*(.+?)\s*\|$/gm;
    let match;
    while ((match = tableRegex.exec(summaryMd)) !== null) {
        const key = match[1].trim();
        const rawValue = match[2].trim();
        if (rawValue === "-" || rawValue === "")
            continue;
        if (key.includes("Commit") || key.includes("提交")) {
            // Extract SHA from link: [abc1234](url) or plain `abc1234`
            const shaMatch = rawValue.match(/\[([a-f0-9]+)\]\(.+?\)/) ?? rawValue.match(/`([a-f0-9]+)`/);
            meta.reviewed_commit = shaMatch ? shaMatch[1] : rawValue;
        }
        else if (key.includes("触发事件") || key.includes("Trigger")) {
            meta.trigger_event = rawValue.replace(/`/g, "");
        }
        else if (key.includes("Workflow") || key.includes("运行")) {
            const urlMatch = rawValue.match(/\[.+?\]\((.+?)\)/);
            meta.workflow_run_url = urlMatch ? urlMatch[1] : rawValue;
        }
        else if (key.includes("更新时间") || key.includes("Updated")) {
            meta.updated_at = rawValue;
        }
        else if (key.includes("审查模式") || key.includes("Review Mode")) {
            meta.review_mode = rawValue.includes("增量") ? "incremental" : "full_pr";
        }
    }
    // Return null if no fields were parsed
    if (Object.keys(meta).length === 0)
        return null;
    return meta;
}
/** Extract risk level from summary markdown. */
function parseRiskLevel(summaryMd) {
    if (!summaryMd)
        return null;
    const m = summaryMd.match(/整体风险[|* ]+`?\*{0,2}(\w+)\*{0,2}`?/);
    if (m)
        return m[1].toUpperCase();
    // Try English pattern
    const en = summaryMd.match(/Risk[|* ]+`?\*{0,2}(\w+)\*{0,2}`?/i);
    return en ? en[1].toUpperCase() : null;
}
// ── Main fetcher ───────────────────────────────────────
/**
 * Full pipeline: detect branch → find PR → fetch review comments → parse.
 * Returns null if no open PR for the current branch.
 */
async function fetchReview(cwd) {
    const branch = await (0, git_1.getCurrentBranch)(cwd);
    if (!branch)
        return null;
    const pr = await (0, git_1.getPRForBranch)(branch, cwd);
    if (!pr || pr.state !== "OPEN")
        return null;
    // Fetch bot summary comment & inline comments in parallel
    const [comments, summary, run] = await Promise.all([
        (0, git_1.getBotReviewComments)(pr.owner, pr.repo, pr.number, cwd),
        (0, git_1.getBotSummaryComment)(pr.owner, pr.repo, pr.number, cwd),
        (0, git_1.getLatestWorkflowRun)(branch, cwd),
    ]);
    const suggestions = [];
    for (const c of comments) {
        const parsed = parseSuggestion(c);
        if (parsed)
            suggestions.push(parsed);
    }
    return {
        pr,
        suggestions,
        summary,
        workflowRunUrl: run?.url ?? null,
        workflowStatus: run
            ? (run.conclusion ?? run.status)
            : null,
        reviewMeta: parseReviewMeta(summary),
        riskLevel: parseRiskLevel(summary),
    };
}
//# sourceMappingURL=review-fetcher.js.map