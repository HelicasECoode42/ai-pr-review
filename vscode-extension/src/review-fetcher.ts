import * as vscode from "vscode";
import {
  getCurrentBranch,
  getPRForBranch,
  getBotReviewComments,
  getBotSummaryComment,
  getLatestWorkflowRun,
  type PRInfo,
  type GitHubReviewComment,
} from "./git";

// ── Types ──────────────────────────────────────────────

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
}

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
function parseSuggestion(
  comment: GitHubReviewComment,
): ParsedSuggestion | null {
  const body = comment.body;
  // Parse header: **severity** (confidence%): title
  const headerMatch = body.match(
    /^\*\*(\w+)\*\*\s*\((\d+)%\):\s*(.+)$/m,
  );
  if (!headerMatch) return null;

  const severity = normalizeSeverity(headerMatch[1]);
  const confidence = parseInt(headerMatch[2], 10) / 100;
  const title = headerMatch[3].trim();

  // Extract reason: text between title line and "> recommendation"
  const rest = body.slice(headerMatch.index! + headerMatch[0].length);

  let reason = "";
  let recommendation = "";

  // Split on the > recommendation line
  const recIdx = rest.indexOf("\n\n> ");
  if (recIdx !== -1) {
    reason = rest.slice(0, recIdx).trim();
    recommendation = rest.slice(recIdx + 3).replace(/^>\s?/gm, "").trim();
  } else {
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

function normalizeSeverity(s: string): ParsedSuggestion["severity"] {
  const lower = s.toLowerCase();
  if (["critical", "high", "medium", "low"].includes(lower)) {
    return lower as ParsedSuggestion["severity"];
  }
  return "medium";
}

// ── Main fetcher ───────────────────────────────────────

/**
 * Full pipeline: detect branch → find PR → fetch review comments → parse.
 * Returns null if no open PR for the current branch.
 */
export async function fetchReview(
  cwd?: string,
): Promise<ReviewResult | null> {
  const branch = await getCurrentBranch(cwd);
  if (!branch) return null;

  const pr = await getPRForBranch(branch, cwd);
  if (!pr || pr.state !== "OPEN") return null;

  // Fetch bot summary comment & inline comments in parallel
  const [comments, summary, run] = await Promise.all([
    getBotReviewComments(pr.owner, pr.repo, pr.number, cwd),
    getBotSummaryComment(pr.owner, pr.repo, pr.number, cwd),
    getLatestWorkflowRun(branch, cwd),
  ]);

  const suggestions: ParsedSuggestion[] = [];
  for (const c of comments) {
    const parsed = parseSuggestion(c);
    if (parsed) suggestions.push(parsed);
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
