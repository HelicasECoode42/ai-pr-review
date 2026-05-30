import * as vscode from "vscode";
import type { ReviewReport, ReviewSuggestion } from "./report";

const DIAGNOSTIC_COLLECTION = "ai-pr-review";

/**
 * Map a ReviewSuggestion severity to VS Code DiagnosticSeverity.
 */
function mapSeverity(severity: string): vscode.DiagnosticSeverity {
  switch (severity) {
    case "critical":
    case "high":
      return vscode.DiagnosticSeverity.Error;
    case "medium":
      return vscode.DiagnosticSeverity.Warning;
    case "low":
    default:
      return vscode.DiagnosticSeverity.Information;
  }
}

/**
 * Create one VS Code Diagnostic from a ReviewSuggestion.
 *
 * The range covers the target line; if line is null, we show the diagnostic
 * at the beginning of the file (position 0,0).
 */
function suggestionToDiagnostic(
  suggestion: ReviewSuggestion,
  documentUri: vscode.Uri,
): vscode.Diagnostic | null {
  const severity = mapSeverity(suggestion.severity);
  const confidence = Math.round(suggestion.confidence * 100);

  // Build message: severity badge + title + reason + recommendation
  const parts = [`[${suggestion.severity.toUpperCase()}] ${confidence}% — ${suggestion.title}`];
  if (suggestion.reason) {
    parts.push(`Reason: ${suggestion.reason}`);
  }
  if (suggestion.recommendation) {
    parts.push(`💡 ${suggestion.recommendation}`);
  }
  const message = parts.join("\n");

  // Determine range
  let range: vscode.Range;
  if (suggestion.line !== null && suggestion.line > 0) {
    const lineIdx = suggestion.line - 1; // GitHub lines are 1-based
    range = new vscode.Range(lineIdx, 0, lineIdx, Number.MAX_SAFE_INTEGER);
  } else {
    range = new vscode.Range(0, 0, 0, 0);
  }

  const diagnostic = new vscode.Diagnostic(range, message, severity);
  diagnostic.source = "AI PR Review";
  diagnostic.code = {
    value: suggestion.title.slice(0, 50),
    target: vscode.Uri.parse(
      `https://github.com/ai-pr-review/suggestion?title=${encodeURIComponent(suggestion.title)}`,
    ),
  };
  return diagnostic;
}

/**
 * Parse a ReviewReport JSON and create per-file Diagnostic arrays.
 */
export function parseAndCreateDiagnostics(
  json: string,
): Map<string, vscode.Diagnostic[]> | Error {
  let report: ReviewReport;
  try {
    report = JSON.parse(json) as ReviewReport;
  } catch (e) {
    return new Error(`Failed to parse report JSON: ${e instanceof Error ? e.message : String(e)}`);
  }

  if (!report.suggestions || !Array.isArray(report.suggestions)) {
    return new Error("Invalid report format: missing suggestions array.");
  }

  const byFile = new Map<string, vscode.Diagnostic[]>();
  const workspaceFolders = vscode.workspace.workspaceFolders ?? [];
  const root = workspaceFolders.length > 0 ? workspaceFolders[0].uri : undefined;

  for (const s of report.suggestions) {
    // Try to resolve the file path relative to workspace root
    let fileUri: vscode.Uri;
    if (root) {
      fileUri = vscode.Uri.joinPath(root, s.file_path);
    } else {
      fileUri = vscode.Uri.file(s.file_path);
    }

    const diag = suggestionToDiagnostic(s, fileUri);
    if (!diag) continue;

    const key = fileUri.fsPath;
    if (!byFile.has(key)) {
      byFile.set(key, []);
    }
    byFile.get(key)!.push(diag);
  }

  return byFile;
}

/**
 * Apply diagnostics to the collection — clears old, sets new per file.
 */
export function applyDiagnostics(
  collection: vscode.DiagnosticCollection,
  byFile: Map<string, vscode.Diagnostic[]>,
): void {
  // Clear all existing diagnostics from this collection
  collection.clear();

  // Build the new set per file URI
  const entries: [vscode.Uri, vscode.Diagnostic[]][] = [];
  for (const [fsPath, diags] of byFile) {
    entries.push([vscode.Uri.file(fsPath), diags]);
  }

  collection.set(entries);
}

/**
 * Get the singleton diagnostic collection name.
 */
export function getCollectionName(): string {
  return DIAGNOSTIC_COLLECTION;
}
