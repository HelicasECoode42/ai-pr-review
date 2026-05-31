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
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const review_fetcher_1 = require("./review-fetcher");
const diagnostics_1 = require("./diagnostics");
const panel_1 = require("./panel");
let diagnosticCollection;
let statusBar;
let reviewPanel;
function getWorkspaceRoot() {
    const activeUri = vscode.window.activeTextEditor?.document.uri;
    if (activeUri) {
        const folder = vscode.workspace.getWorkspaceFolder(activeUri);
        if (folder)
            return folder.uri.fsPath;
    }
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}
// ── Status bar ──────────────────────────────────────────
function createStatusBar() {
    const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    item.command = "ai-pr-review.openPanel";
    return item;
}
function updateStatusBar(result, error, loading) {
    if (!statusBar)
        return;
    if (loading) {
        statusBar.text = "$(sync~spin) AI Review: loading...";
        statusBar.tooltip = "Fetching review results...";
        statusBar.backgroundColor = undefined;
        return;
    }
    if (error) {
        statusBar.text = "$(error) AI Review";
        statusBar.tooltip = error;
        statusBar.backgroundColor = undefined;
        return;
    }
    if (!result) {
        statusBar.text = "$(circle-slash) AI Review: no PR";
        statusBar.tooltip =
            "No open pull request for this branch. Push and create a PR first.";
        statusBar.backgroundColor = undefined;
        return;
    }
    const count = result.suggestions.length;
    const critical = result.suggestions.filter((s) => s.severity === "critical" || s.severity === "high").length;
    if (count === 0) {
        statusBar.text = "$(check) AI Review: clean";
        statusBar.tooltip = "No suggestions from AI review.";
        statusBar.backgroundColor = undefined;
    }
    else {
        statusBar.text = `$(warning) AI Review: ${count} issues`;
        if (critical > 0) {
            statusBar.text += ` / ${critical} high`;
        }
        const runInfo = result.workflowRunUrl
            ? `\n\nWorkflow: ${result.workflowRunUrl}`
            : "";
        let metaInfo = "";
        if (result.reviewMeta?.reviewed_commit) {
            metaInfo += `\nCommit: ${result.reviewMeta.reviewed_commit.slice(0, 7)}`;
        }
        if (result.reviewMeta?.updated_at) {
            metaInfo += `\nUpdated: ${result.reviewMeta.updated_at}`;
        }
        statusBar.tooltip =
            `PR #${result.pr.number}: ${result.pr.title}\n${count} suggestion(s) total, ${critical} high/critical` +
                metaInfo +
                runInfo;
        statusBar.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
    }
}
// ── Core logic ─────────────────────────────────────────
let lastReviewResult = null;
async function loadReview() {
    if (!statusBar)
        return;
    updateStatusBar(null, undefined, true);
    try {
        const result = await (0, review_fetcher_1.fetchReview)(getWorkspaceRoot());
        lastReviewResult = result;
        if (!diagnosticCollection)
            return;
        diagnosticCollection.clear();
        if (result && result.suggestions.length > 0) {
            const byFile = (0, diagnostics_1.buildDiagnostics)(result.suggestions);
            (0, diagnostics_1.applyDiagnostics)(diagnosticCollection, byFile);
        }
        updateStatusBar(result);
        // Update panel if open
        if (result && reviewPanel) {
            await reviewPanel.show(result);
        }
        // Notify user
        if (result) {
            const n = result.suggestions.length;
            if (n > 0) {
                vscode.window.showInformationMessage(`AI PR Review: ${n} issue(s) loaded for PR #${result.pr.number}`);
            }
        }
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        updateStatusBar(null, `Failed: ${msg}`);
        vscode.window.showErrorMessage(`AI PR Review: ${msg}`);
    }
}
// ── Manual file load (backward compat) ──────────────────
async function loadReportFile() {
    try {
        const uris = await vscode.window.showOpenDialog({
            canSelectFiles: true,
            canSelectFolders: false,
            canSelectMany: false,
            filters: { "AI PR Review Report": ["json"] },
            openLabel: "Load Report",
            title: "Select a pr-review.json report file",
        });
        if (!uris || uris.length === 0)
            return;
        const raw = await vscode.workspace.fs.readFile(uris[0]);
        const json = new TextDecoder("utf-8").decode(raw);
        const result = (0, diagnostics_1.parseAndCreateDiagnostics)(json);
        if (result instanceof Error) {
            vscode.window.showErrorMessage(`AI PR Review: ${result.message}`);
            return;
        }
        if (!diagnosticCollection)
            return;
        (0, diagnostics_1.applyDiagnostics)(diagnosticCollection, result);
        let total = 0;
        for (const diags of result.values())
            total += diags.length;
        vscode.window.showInformationMessage(total === 0
            ? `AI PR Review: No suggestions found in ${vscode.workspace.asRelativePath(uris[0])}`
            : `AI PR Review: Loaded ${total} suggestion(s) from ${vscode.workspace.asRelativePath(uris[0])}`);
    }
    catch (err) {
        vscode.window.showErrorMessage(`AI PR Review: ${err instanceof Error ? err.message : String(err)}`);
    }
}
function clearDiagnostics() {
    diagnosticCollection?.clear();
    lastReviewResult = null;
    if (statusBar) {
        statusBar.text = "$(circle-slash) AI Review";
        statusBar.tooltip = "Diagnostics cleared. Click to refresh.";
        statusBar.backgroundColor = undefined;
    }
    vscode.window.showInformationMessage("AI PR Review: Diagnostics cleared.");
}
async function openPanel() {
    if (!reviewPanel)
        return;
    if (lastReviewResult) {
        await reviewPanel.show(lastReviewResult);
    }
    else {
        await reviewPanel.showLoading();
        await loadReview();
        // After loading, show results in the panel if they arrived
        if (lastReviewResult && reviewPanel) {
            await reviewPanel.show(lastReviewResult);
        }
    }
}
// ── Extension lifecycle ─────────────────────────────────
function activate(context) {
    // Diagnostic collection
    diagnosticCollection = vscode.languages.createDiagnosticCollection((0, diagnostics_1.getCollectionName)());
    context.subscriptions.push(diagnosticCollection);
    // Status bar
    statusBar = createStatusBar();
    statusBar.show();
    context.subscriptions.push(statusBar);
    // Review panel provider
    reviewPanel = new panel_1.ReviewPanelProvider(context.extensionUri, () => (0, review_fetcher_1.fetchReview)(getWorkspaceRoot()));
    context.subscriptions.push(reviewPanel);
    // Commands
    context.subscriptions.push(vscode.commands.registerCommand("ai-pr-review.refresh", loadReview), vscode.commands.registerCommand("ai-pr-review.loadReport", loadReportFile), vscode.commands.registerCommand("ai-pr-review.clearDiagnostics", clearDiagnostics), vscode.commands.registerCommand("ai-pr-review.openPanel", () => openPanel()));
    // Auto-load on activation (after a short delay to let workspace settle)
    setTimeout(() => loadReview(), 500);
}
function deactivate() {
    diagnosticCollection?.clear();
    diagnosticCollection?.dispose();
    statusBar?.dispose();
}
//# sourceMappingURL=extension.js.map