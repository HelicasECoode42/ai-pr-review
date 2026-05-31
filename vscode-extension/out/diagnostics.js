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
exports.suggestionToDiagnostic = suggestionToDiagnostic;
exports.resolveFileUri = resolveFileUri;
exports.buildDiagnostics = buildDiagnostics;
exports.parseAndCreateDiagnostics = parseAndCreateDiagnostics;
exports.applyDiagnostics = applyDiagnostics;
exports.getCollectionName = getCollectionName;
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
const DIAGNOSTIC_COLLECTION = "ai-pr-review";
/** Map severity string → VS Code DiagnosticSeverity. */
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
/** Build a human-readable diagnostic message. */
function buildMessage(s) {
    const confidence = Math.round(s.confidence * 100);
    const parts = [
        `[${s.severity.toUpperCase()}] ${confidence}% — ${s.title}`,
    ];
    if (s.reason)
        parts.push(`Reason: ${s.reason}`);
    if (s.recommendation)
        parts.push(`💡 ${s.recommendation}`);
    return parts.join("\n");
}
/** Determine VS Code Range from a line number (1-based, GitHub convention). */
function lineToRange(line) {
    if (line !== null && line > 0) {
        const idx = line - 1;
        return new vscode.Range(idx, 0, idx, 1000);
    }
    return new vscode.Range(0, 0, 0, 0);
}
/** Convert a single suggestion to a vscode.Diagnostic. */
function suggestionToDiagnostic(s, fileUri) {
    const diag = new vscode.Diagnostic(lineToRange(s.line), buildMessage(s), mapSeverity(s.severity));
    diag.source = "AI PR Review";
    diag.code = s.title.slice(0, 50);
    return diag;
}
/** Resolve a file_path to an absolute vscode.Uri. */
function resolveFileUri(filePath) {
    if (path.isAbsolute(filePath))
        return vscode.Uri.file(filePath);
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        return vscode.Uri.joinPath(folders[0].uri, filePath);
    }
    return vscode.Uri.file(filePath);
}
// ── From parsed suggestions (GitHub inline comments) ───
/** Build per-file Diagnostic map from ReviewSuggestion[]. */
function buildDiagnostics(suggestions) {
    const byFile = new Map();
    for (const s of suggestions) {
        const uri = resolveFileUri(s.file_path);
        const diag = suggestionToDiagnostic(s, uri);
        const key = uri.fsPath;
        if (!byFile.has(key))
            byFile.set(key, []);
        byFile.get(key).push(diag);
    }
    return byFile;
}
// ── From JSON report file (manual load, kept for backward compat) ──
/** Parse JSON report and build per-file diagnostics. */
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
    return buildDiagnostics(report.suggestions);
}
/** Clear and set diagnostics on the collection. */
function applyDiagnostics(collection, byFile) {
    collection.clear();
    const entries = [];
    for (const [fsPath, diags] of byFile) {
        entries.push([vscode.Uri.file(fsPath), diags]);
    }
    collection.set(entries);
}
/** The singleton diagnostic collection name. */
function getCollectionName() {
    return DIAGNOSTIC_COLLECTION;
}
//# sourceMappingURL=diagnostics.js.map