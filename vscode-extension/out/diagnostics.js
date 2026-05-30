"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.parseAndCreateDiagnostics = parseAndCreateDiagnostics;
exports.applyDiagnostics = applyDiagnostics;
exports.getCollectionName = getCollectionName;
const vscode = __importStar(require("vscode"));
const DIAGNOSTIC_COLLECTION = "ai-pr-review";
/**
 * Map a ReviewSuggestion severity to VS Code DiagnosticSeverity.
 */
function mapSeverity(severity) {
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
function suggestionToDiagnostic(suggestion, documentUri) {
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
    let range;
    if (suggestion.line !== null && suggestion.line > 0) {
        const lineIdx = suggestion.line - 1; // GitHub lines are 1-based
        range = new vscode.Range(lineIdx, 0, lineIdx, Number.MAX_SAFE_INTEGER);
    }
    else {
        range = new vscode.Range(0, 0, 0, 0);
    }
    const diagnostic = new vscode.Diagnostic(range, message, severity);
    diagnostic.source = "AI PR Review";
    diagnostic.code = {
        value: suggestion.title.slice(0, 50),
        target: vscode.Uri.parse(`https://github.com/ai-pr-review/suggestion?title=${encodeURIComponent(suggestion.title)}`),
    };
    return diagnostic;
}
/**
 * Parse a ReviewReport JSON and create per-file Diagnostic arrays.
 */
function parseAndCreateDiagnostics(json) {
    let report;
    try {
        report = JSON.parse(json);
    }
    catch (e) {
        return new Error(`Failed to parse report JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
    if (!report.suggestions || !Array.isArray(report.suggestions)) {
        return new Error("Invalid report format: missing suggestions array.");
    }
    const byFile = new Map();
    const workspaceFolders = vscode.workspace.workspaceFolders ?? [];
    const root = workspaceFolders.length > 0 ? workspaceFolders[0].uri : undefined;
    for (const s of report.suggestions) {
        // Try to resolve the file path relative to workspace root
        let fileUri;
        if (root) {
            fileUri = vscode.Uri.joinPath(root, s.file_path);
        }
        else {
            fileUri = vscode.Uri.file(s.file_path);
        }
        const diag = suggestionToDiagnostic(s, fileUri);
        if (!diag)
            continue;
        const key = fileUri.fsPath;
        if (!byFile.has(key)) {
            byFile.set(key, []);
        }
        byFile.get(key).push(diag);
    }
    return byFile;
}
/**
 * Apply diagnostics to the collection — clears old, sets new per file.
 */
function applyDiagnostics(collection, byFile) {
    // Clear all existing diagnostics from this collection
    collection.clear();
    // Build the new set per file URI
    const entries = [];
    for (const [fsPath, diags] of byFile) {
        entries.push([vscode.Uri.file(fsPath), diags]);
    }
    collection.set(entries);
}
/**
 * Get the singleton diagnostic collection name.
 */
function getCollectionName() {
    return DIAGNOSTIC_COLLECTION;
}
//# sourceMappingURL=diagnostics.js.map