import * as vscode from "vscode";
import type { ReviewSuggestion } from "./report";
/** Convert a single suggestion to a vscode.Diagnostic. */
export declare function suggestionToDiagnostic(s: ReviewSuggestion, fileUri: vscode.Uri): vscode.Diagnostic;
/** Resolve a file_path to an absolute vscode.Uri. */
export declare function resolveFileUri(filePath: string): vscode.Uri;
/** Build per-file Diagnostic map from ReviewSuggestion[]. */
export declare function buildDiagnostics(suggestions: ReviewSuggestion[]): Map<string, vscode.Diagnostic[]>;
/** Parse JSON report and build per-file diagnostics. */
export declare function parseAndCreateDiagnostics(json: string): Map<string, vscode.Diagnostic[]> | Error;
/** Clear and set diagnostics on the collection. */
export declare function applyDiagnostics(collection: vscode.DiagnosticCollection, byFile: Map<string, vscode.Diagnostic[]>): void;
/** The singleton diagnostic collection name. */
export declare function getCollectionName(): string;
