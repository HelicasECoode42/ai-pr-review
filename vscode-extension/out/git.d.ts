/** Get the current git branch name. */
export declare function getCurrentBranch(cwd?: string): Promise<string | null>;
/** Parse remote URL into owner/repo. Handles both HTTPS and SSH remotes. */
export declare function getRepoSlug(cwd?: string): Promise<string | null>;
export interface PRInfo {
    owner: string;
    repo: string;
    number: number;
    title: string;
    state: string;
    url: string;
    headRefName: string;
}
/** Get the current commit SHA. */
export declare function getCurrentCommit(cwd?: string): Promise<string | null>;
/** Find the open PR for the current branch. Returns null if none. */
export declare function getPRForBranch(branch: string, cwd?: string): Promise<PRInfo | null>;
/**
 * Find an open PR associated with a commit. This covers detached HEAD, a local
 * main branch checked out at a PR commit, and branch names that differ locally.
 */
export declare function getPRForCommit(commit: string, cwd?: string): Promise<PRInfo | null>;
export interface GitHubReviewComment {
    id: number;
    path: string;
    line: number | null;
    original_line?: number | null;
    body: string;
    user: {
        login: string;
    };
    created_at: string;
    html_url: string;
}
/**
 * Fetch review comments posted by github-actions[bot] on a PR.
 * These are the inline code comments with structured suggestion data.
 */
export declare function getBotReviewComments(owner: string, repo: string, prNumber: number, cwd?: string): Promise<GitHubReviewComment[]>;
/**
 * Fetch the bot summary comment marker and body.
 */
export declare function getBotSummaryComment(owner: string, repo: string, prNumber: number, cwd?: string): Promise<string | null>;
/**
 * Get the latest AI PR Review workflow run status.
 */
export declare function getLatestWorkflowRun(branch: string, cwd?: string): Promise<{
    status: string;
    conclusion: string | null;
    url: string;
} | null>;
/**
 * Download the latest workflow artifact report for a branch and return
 * reports/pr-review.json. Returns null when no suitable run/artifact exists.
 */
export declare function getLatestReportArtifactJson(branch: string, cwd?: string): Promise<string | null>;
