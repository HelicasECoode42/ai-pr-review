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
/** Find the open PR for the current branch. Returns null if none. */
export declare function getPRForBranch(branch: string, cwd?: string): Promise<PRInfo | null>;
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
