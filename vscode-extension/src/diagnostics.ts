import * as path from "path";
import * as vscode from "vscode";
import type { ReviewReport, ReviewSuggestion } from "./report";

const DIAGNOSTIC_COLLECTION = "ai-pr-review";

/** Map severity string → VS Code DiagnosticSeverity. */
function mapSeverity(
  severity: string,
): vscode.DiagnosticSeverity {
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

/** Build a human-readable diagnostic message. */
function buildMessage(s: ReviewSuggestion): string {
  const confidence = Math.round(s.confidence * 100);
  const parts = [
    `[${s.severity.toUpperCase()}] ${confidence}% — ${s.title}`,
  ];
  if (s.reason) parts.push(`Reason: ${s.reason}`);
  if (s.recommendation) parts.push(`💡 ${s.recommendation}`);
  return parts.join("\n");
}

/** Determine VS Code Range from a line number (1-based, GitHub convention). */
function lineToRange(line: number | null): vscode.Range {
  if (line !== null && line > 0) {
    const idx = line - 1;
    return new vscode.Range(idx, 0, idx, 1000);
  }
  return new vscode.Range(0, 0, 0, 0);
}

/** Convert a single suggestion to a vscode.Diagnostic. */
export function suggestionToDiagnostic(
  s: ReviewSuggestion,
  fileUri: vscode.Uri,
): vscode.Diagnostic {
  const diag = new vscode.Diagnostic(
    lineToRange(s.line),
    buildMessage(s),
    mapSeverity(s.severity),
  );
  diag.source = "AI PR Review";
  diag.code = s.title.slice(0, 50);
  return diag;
}

/** Resolve a file_path to an absolute vscode.Uri. */
export function resolveFileUri(filePath: string): vscode.Uri {
  if (path.isAbsolute(filePath)) return vscode.Uri.file(filePath);

  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0) {
    return vscode.Uri.joinPath(folders[0].uri, filePath);
  }
  return vscode.Uri.file(filePath);
}

// ── From parsed suggestions (GitHub inline comments) ───

/** Build per-file Diagnostic map from ReviewSuggestion[]. */
export function buildDiagnostics(
  suggestions: ReviewSuggestion[],
): Map<string, vscode.Diagnostic[]> {
  const byFile = new Map<string, vscode.Diagnostic[]>();

  for (const s of suggestions) {
    const uri = resolveFileUri(s.file_path);
    const diag = suggestionToDiagnostic(s, uri);
    const key = uri.fsPath;

    if (!byFile.has(key)) byFile.set(key, []);
    byFile.get(key)!.push(diag);
  }
  return byFile;
}

// ── From JSON report file (manual load, kept for backward compat) ──

/** Parse JSON report and build per-file diagnostics. */
export function parseAndCreateDiagnostics(
  json: string,
): Map<string, vscode.Diagnostic[]> | Error {
  let report: ReviewReport;
  try {
    report = JSON.parse(json) as ReviewReport;
  } catch (e) {
    return new Error(
      `Failed to parse report JSON: ${e instanceof Error ? e.message : String(e)}`,
    );
  }

  if (!report.suggestions || !Array.isArray(report.suggestions)) {
    return new Error("Invalid report format: missing suggestions array.");
  }

  return buildDiagnostics(report.suggestions);
}

/** Clear and set diagnostics on the collection. */
export function applyDiagnostics(
  collection: vscode.DiagnosticCollection,
  byFile: Map<string, vscode.Diagnostic[]>,
): void {
  collection.clear();
  const entries: [vscode.Uri, vscode.Diagnostic[]][] = [];
  for (const [fsPath, diags] of byFile) {
    entries.push([vscode.Uri.file(fsPath), diags]);
  }
  collection.set(entries);
}

/** The singleton diagnostic collection name. */
export function getCollectionName(): string {
  return DIAGNOSTIC_COLLECTION;
}
