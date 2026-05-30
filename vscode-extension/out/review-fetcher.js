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
    };
}
//# sourceMappingURL=review-fetcher.js.map