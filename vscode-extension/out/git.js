"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getCurrentBranch = getCurrentBranch;
exports.getRepoSlug = getRepoSlug;
exports.getPRForBranch = getPRForBranch;
exports.getBotReviewComments = getBotReviewComments;
exports.getBotSummaryComment = getBotSummaryComment;
exports.getLatestWorkflowRun = getLatestWorkflowRun;
const child_process_1 = require("child_process");
const util_1 = require("util");
const execAsync = (0, util_1.promisify)(child_process_1.exec);
/** Execute a command and return trimmed stdout. */
async function run(cmd, cwd) {
    const { stdout } = await execAsync(cmd, {
        cwd,
        timeout: 15_000,
    });
    return stdout.trim();
}
/** Run a command that returns JSON. Returns null on any error. */
async function tryJson(cmd, cwd) {
    try {
        const out = await run(cmd, cwd);
        return JSON.parse(out);
    }
    catch {
        return null;
    }
}
// ── Git ────────────────────────────────────────────────
/** Get the current git branch name. */
async function getCurrentBranch(cwd) {
    try {
        return await run("git rev-parse --abbrev-ref HEAD", cwd);
    }
    catch {
        return null;
    }
}
/** Parse remote URL into owner/repo. Handles both HTTPS and SSH remotes. */
async function getRepoSlug(cwd) {
    try {
        const url = await run("git remote get-url origin", cwd);
        // HTTPS: https://github.com/owner/repo.git
        // SSH:   git@github.com:owner/repo.git
        const match = url.match(/github\.com[:/](.+?)\/(.+?)(?:\.git)?$/);
        if (!match)
            return null;
        return `${match[1]}/${match[2]}`;
    }
    catch {
        return null;
    }
}
/** Find the open PR for the current branch. Returns null if none. */
async function getPRForBranch(branch, cwd) {
    // Search for PRs with this branch as head
    const prs = await tryJson(`gh pr list --head "${branch}" --json number,title,state,url,headRefName,headRepositoryOwner --limit 1`, cwd);
    if (!prs || prs.length === 0)
        return null;
    const pr = prs[0];
    const repo = await getRepoSlug(cwd);
    if (!repo)
        return null;
    const [owner, repoName] = repo.split("/");
    return {
        owner: pr.headRepositoryOwner?.login ?? owner,
        repo: repoName,
        number: pr.number,
        title: pr.title,
        state: pr.state.toUpperCase(),
        url: pr.url,
        headRefName: pr.headRefName,
    };
}
/**
 * Fetch review comments posted by github-actions[bot] on a PR.
 * These are the inline code comments with structured suggestion data.
 */
async function getBotReviewComments(owner, repo, prNumber, cwd) {
    const all = await tryJson(`gh api "repos/${owner}/${repo}/pulls/${prNumber}/comments?per_page=100&sort=created&direction=desc"`, cwd) ?? [];
    // Filter to bot comments only, most recent first
    return all.filter((c) => c.user?.login === "github-actions[bot]" ||
        c.user?.login === "github-actions");
}
/**
 * Fetch the bot summary comment marker and body.
 */
async function getBotSummaryComment(owner, repo, prNumber, cwd) {
    const comments = await tryJson(`gh api "repos/${owner}/${repo}/issues/${prNumber}/comments?per_page=50&sort=created&direction=desc"`, cwd) ?? [];
    for (const c of comments) {
        if (c.user?.login === "github-actions[bot]" &&
            c.body?.includes("<!-- ai-pr-review-bot -->")) {
            return c.body;
        }
    }
    return null;
}
/**
 * Get the latest AI PR Review workflow run status.
 */
async function getLatestWorkflowRun(branch, cwd) {
    const runs = await tryJson(`gh run list --workflow="ai-pr-review.yml" --branch="${branch}" --event=pull_request --limit=1 --json status,conclusion,url`, cwd) ?? [];
    if (!runs || runs.length === 0)
        return null;
    const r = runs[0];
    return { status: r.status, conclusion: r.conclusion, url: r.url };
}
//# sourceMappingURL=git.js.map