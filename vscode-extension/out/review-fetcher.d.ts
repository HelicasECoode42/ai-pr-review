import { type PRInfo } from "./git";
import type { ReviewMeta, ReviewSuggestion } from "./report";
export interface ReviewResult {
    pr: PRInfo;
    suggestions: ReviewSuggestion[];
    summary: string | null;
    workflowRunUrl: string | null;
    workflowStatus: string | null;
    reviewMeta: ReviewMeta | null;
    riskLevel: string | null;
}
/**
 * Full pipeline: detect branch → find PR → fetch review comments → parse.
 * Returns null if no open PR for the current branch.
 */
export declare function fetchReview(cwd?: string): Promise<ReviewResult | null>;
