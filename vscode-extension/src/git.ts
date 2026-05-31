import { exec } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { promisify } from "util";

const execAsync = promisify(exec);

/** Execute a command and return trimmed stdout. */
async function run(cmd: string, cwd?: string): Promise<string> {
  const { stdout } = await execAsync(cmd, {
    cwd,
    timeout: 15_000,
  });
  return stdout.trim();
}

/** Run a command that returns JSON. Returns null on any error. */
async function tryJson<T>(cmd: string, cwd?: string): Promise<T | null> {
  try {
    const out = await run(cmd, cwd);
    return JSON.parse(out) as T;
  } catch {
    return null;
  }
}

// ── Git ────────────────────────────────────────────────

/** Get the current git branch name. */
export async function getCurrentBranch(cwd?: string): Promise<string | null> {
  try {
    return await run("git rev-parse --abbrev-ref HEAD", cwd);
  } catch {
    return null;
  }
}

/**
 * Sanitize a branch name for safe interpolation into shell commands.
 * Only allows characters valid in git branch names.
 */
function sanitizeBranch(branch: string): string {
  // Restrict to git-ref-safe characters: alphanumeric, dash, dot, underscore, slash
  return branch.replace(/[^a-zA-Z0-9._/-]/g, "");
}

/** Parse remote URL into owner/repo. Handles both HTTPS and SSH remotes. */
export async function getRepoSlug(cwd?: string): Promise<string | null> {
  try {
    const url = await run("git remote get-url origin", cwd);
    // HTTPS: https://github.com/owner/repo.git
    // SSH:   git@github.com:owner/repo.git
    const match = url.match(/github\.com[:/](.+?)\/(.+?)(?:\.git)?$/);
    if (!match) return null;
    return `${match[1]}/${match[2]}`;
  } catch {
    return null;
  }
}

// ── gh CLI ─────────────────────────────────────────────

export interface PRInfo {
  owner: string;
  repo: string;
  number: number;
  title: string;
  state: string;
  url: string;
  headRefName: string;
}

interface GhPullRequest {
  number: number;
  title: string;
  state: string;
  url: string;
  headRefName: string;
}

function toPRInfo(pr: GhPullRequest, repoSlug: string): PRInfo {
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
export async function getCurrentCommit(cwd?: string): Promise<string | null> {
  try {
    return await run("git rev-parse HEAD", cwd);
  } catch {
    return null;
  }
}

/** Find the open PR for the current branch. Returns null if none. */
export async function getPRForBranch(
  branch: string,
  cwd?: string,
): Promise<PRInfo | null> {
  const repoSlug = await getRepoSlug(cwd);
  if (!repoSlug) return null;

  // Search for PRs with this branch as head
  const prs: GhPullRequest[] | null = await tryJson(
    `gh pr list --head "${sanitizeBranch(branch)}" --json number,title,state,url,headRefName,headRepositoryOwner --limit 1`,
    cwd,
  );

  if (!prs || prs.length === 0) return null;

  return toPRInfo(prs[0], repoSlug);
}

/**
 * Find an open PR associated with a commit. This covers detached HEAD, a local
 * main branch checked out at a PR commit, and branch names that differ locally.
 */
export async function getPRForCommit(
  commit: string,
  cwd?: string,
): Promise<PRInfo | null> {
  const repoSlug = await getRepoSlug(cwd);
  if (!repoSlug) return null;

  const prs: GhPullRequest[] | null = await tryJson(
    `gh api -H "Accept: application/vnd.github+json" "repos/${repoSlug}/commits/${commit}/pulls"`,
    cwd,
  );

  const pr = prs?.find((item) => item.state.toUpperCase() === "OPEN");
  return pr ? toPRInfo(pr, repoSlug) : null;
}

export interface GitHubReviewComment {
  id: number;
  path: string;
  line: number | null;
  original_line?: number | null;
  body: string;
  user: { login: string };
  created_at: string;
  html_url: string;
}

/**
 * Fetch review comments posted by github-actions[bot] on a PR.
 * These are the inline code comments with structured suggestion data.
 */
export async function getBotReviewComments(
  owner: string,
  repo: string,
  prNumber: number,
  cwd?: string,
): Promise<GitHubReviewComment[]> {
  const all: GitHubReviewComment[] = await tryJson(
    `gh api "repos/${owner}/${repo}/pulls/${prNumber}/comments?per_page=100&sort=created&direction=desc"`,
    cwd,
  ) ?? [];

  // Filter to bot comments only, most recent first
  return all.filter((c) =>
    c.user?.login === "github-actions[bot]" ||
    c.user?.login === "github-actions"
  );
}

/**
 * Fetch the bot summary comment marker and body.
 */
export async function getBotSummaryComment(
  owner: string,
  repo: string,
  prNumber: number,
  cwd?: string,
): Promise<string | null> {
  const comments: { user?: { login: string }; body: string }[] =
    await tryJson(
      `gh api "repos/${owner}/${repo}/issues/${prNumber}/comments?per_page=50&sort=created&direction=desc"`,
      cwd,
    ) ?? [];

  for (const c of comments) {
    if (
      c.user?.login === "github-actions[bot]" &&
      c.body?.includes("<!-- ai-pr-review-bot -->")
    ) {
      return c.body;
    }
  }
  return null;
}

/**
 * Get the latest AI PR Review workflow run status.
 */
export async function getLatestWorkflowRun(
  branch: string,
  cwd?: string,
): Promise<{ status: string; conclusion: string | null; url: string } | null> {
  const runs: {
    status: string;
    conclusion: string | null;
    url: string;
  }[] = await tryJson(
    `gh run list --workflow="ai-pr-review.yml" --branch="${sanitizeBranch(branch)}" --limit=1 --json status,conclusion,url`,
    cwd,
  ) ?? [];

  if (!runs || runs.length === 0) return null;
  const r = runs[0];
  return { status: r.status, conclusion: r.conclusion, url: r.url };
}

/**
 * Download the latest workflow artifact report for a branch and return
 * reports/pr-review.json. Returns null when no suitable run/artifact exists.
 */
export async function getLatestReportArtifactJson(
  branch: string,
  cwd?: string,
): Promise<string | null> {
  const runs: { databaseId: number; status: string }[] = await tryJson(
    `gh run list --workflow="ai-pr-review.yml" --branch="${sanitizeBranch(branch)}" --limit=5 --json databaseId,status,conclusion,createdAt,event`,
    cwd,
  ) ?? [];

  for (const workflowRun of runs) {
    if (workflowRun.status !== "completed") continue;

    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-pr-review-"));
    try {
      await run(
        `gh run download ${workflowRun.databaseId} --name ai-pr-review-report --dir "${tempDir}"`,
        cwd,
      );

      const reportPath = path.join(tempDir, "pr-review.json");
      if (fs.existsSync(reportPath)) {
        return fs.readFileSync(reportPath, "utf8");
      }
    } catch {
      // Try the next run; artifact upload may have been skipped on failure.
    } finally {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  }

  return null;
}
