/** TypeScript interfaces matching ReviewReport JSON schema from src/models.py. */
export interface PullRequest {
    repo: string;
    number: number;
    title: string;
    body?: string | null;
    author?: string | null;
    base_ref?: string | null;
    head_ref?: string | null;
    head_sha?: string | null;
    html_url?: string | null;
}
export interface ChangedFile {
    filename: string;
    status: string;
    additions: number;
    deletions: number;
    changes: number;
    patch?: string | null;
    previous_filename?: string | null;
}
export interface ReviewSuggestion {
    file_path: string;
    line: number | null;
    severity: "critical" | "high" | "medium" | "low";
    confidence: number;
    title: string;
    reason: string;
    recommendation: string;
}
export interface CompletenessItem {
    item: string;
    status: "success" | "partial" | "failed" | "skipped";
    detail: string;
}
export interface ReviewReport {
    pr: PullRequest;
    files: ChangedFile[];
    summary: string;
    risk_level: string;
    rule_findings: unknown[];
    suggestions: ReviewSuggestion[];
    used_ai: boolean;
    ai_failure_reason?: string | null;
    analysis_warnings: string[];
    context_truncated: boolean;
    hidden_suggestions_count: number;
    skipped_context_files: unknown[];
    hidden_rule_findings_count: number;
    reviewer_version: string;
    execution_status: string;
    degradation_reason?: string | null;
    report_confidence: string;
    completeness: CompletenessItem[];
}
