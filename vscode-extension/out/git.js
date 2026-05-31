"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.getCurrentBranch = getCurrentBranch;
exports.getRepoSlug = getRepoSlug;
exports.getCurrentCommit = getCurrentCommit;
exports.getPRForBranch = getPRForBranch;
exports.getPRForCommit = getPRForCommit;
exports.getBotReviewComments = getBotReviewComments;
exports.getBotSummaryComment = getBotSummaryComment;
exports.getLatestWorkflowRun = getLatestWorkflowRun;
exports.getLatestReportArtifactJson = getLatestReportArtifactJson;
const child_process_1 = require("child_process");
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
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
/**
 * Sanitize a branch name for safe interpolation into shell commands.
 * Only allows characters valid in git branch names.
 */
function sanitizeBranch(branch) {
    // Restrict to git-ref-safe characters: alphanumeric, dash, dot, underscore, slash
    return branch.replace(/[^a-zA-Z0-9._/-]/g, "");
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
function toPRInfo(pr, repoSlug) {
    const [owner, repo] = repoSlug.split("/");
    return {
        owner,
        repo,
        number: pr.number,
        title: pr.title,
        state: pr.state.toUpperCase(),
        url: pr.url,
        headRefName: pr.headRefName,
    };
}
/** Get the current commit SHA. */
async function getCurrentCommit(cwd) {
    try {
        return await run("git rev-parse HEAD", cwd);
    }
    catch {
        return null;
    }
}
/** Find the open PR for the current branch. Returns null if none. */
async function getPRForBranch(branch, cwd) {
    const repoSlug = await getRepoSlug(cwd);
    if (!repoSlug)
        return null;
    // Search for PRs with this branch as head
    const prs = await tryJson(`gh pr list --head "${sanitizeBranch(branch)}" --json number,title,state,url,headRefName,headRepositoryOwner --limit 1`, cwd);
    if (!prs || prs.length === 0)
        return null;
    return toPRInfo(prs[0], repoSlug);
}
/**
 * Find an open PR associated with a commit. This covers detached HEAD, a local
 * main branch checked out at a PR commit, and branch names that differ locally.
 */
async function getPRForCommit(commit, cwd) {
    const repoSlug = await getRepoSlug(cwd);
    if (!repoSlug)
        return null;
    const prs = await tryJson(`gh api -H "Accept: application/vnd.github+json" "repos/${repoSlug}/commits/${commit}/pulls"`, cwd);
    const pr = prs?.find((item) => item.state.toUpperCase() === "OPEN");
    return pr ? toPRInfo(pr, repoSlug) : null;
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
    const runs = await tryJson(`gh run list --workflow="ai-pr-review.yml" --branch="${sanitizeBranch(branch)}" --limit=1 --json status,conclusion,url`, cwd) ?? [];
    if (!runs || runs.length === 0)
        return null;
    const r = runs[0];
    return { status: r.status, conclusion: r.conclusion, url: r.url };
}
/**
 * Download the latest workflow artifact report for a branch and return
 * reports/pr-review.json. Returns null when no suitable run/artifact exists.
 */
async function getLatestReportArtifactJson(branch, cwd) {
    const runs = await tryJson(`gh run list --workflow="ai-pr-review.yml" --branch="${sanitizeBranch(branch)}" --limit=5 --json databaseId,status,conclusion,createdAt,event`, cwd) ?? [];
    for (const workflowRun of runs) {
        if (workflowRun.status !== "completed")
            continue;
        const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-pr-review-"));
        try {
            await run(`gh run download ${workflowRun.databaseId} --name ai-pr-review-report --dir "${tempDir}"`, cwd);
            const reportPath = path.join(tempDir, "pr-review.json");
            if (fs.existsSync(reportPath)) {
                return fs.readFileSync(reportPath, "utf8");
            }
        }
        catch {
            // Try the next run; artifact upload may have been skipped on failure.
        }
        finally {
            fs.rmSync(tempDir, { recursive: true, force: true });
        }
    }
    return null;
}
//# sourceMappingURL=git.js.map