import * as vscode from "vscode";
/**
 * Parse a ReviewReport JSON and create per-file Diagnostic arrays.
 */
export declare function parseAndCreateDiagnostics(json: string): Map<string, vscode.Diagnostic[]> | Error;
/**
 * Apply diagnostics to the collection — clears old, sets new per file.
 */
export declare function applyDiagnostics(collection: vscode.DiagnosticCollection, byFile: Map<string, vscode.Diagnostic[]>): void;
/**
 * Get the singleton diagnostic collection name.
 */
export declare function getCollectionName(): string;
