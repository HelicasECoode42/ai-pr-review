import { type PRInfo } from "./git";
import type { ReviewMeta } from "./report";
export interface ParsedSuggestion {
    file_path: string;
    line: number | null;
    severity: "critical" | "high" | "medium" | "low";
    confidence: number;
    title: string;
    reason: string;
    recommendation: string;
}
export interface ReviewResult {
    pr: PRInfo;
    suggestions: ParsedSuggestion[];
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
