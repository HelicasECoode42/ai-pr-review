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
exports.ReviewPanelProvider = void 0;
const vscode = __importStar(require("vscode"));
// ── HTML template ──────────────────────────────────────
function getWebviewHtml(result, webview) {
    const nonce = getNonce();
    const meta = result.reviewMeta;
    const risk = result.riskLevel ?? "N/A";
    const riskClass = risk === "HIGH" ? "risk-high" : risk === "MEDIUM" ? "risk-medium" : "risk-low";
    function esc(s) {
        if (!s)
            return "-";
        return s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;")
            .replace(/`/g, "&#96;");
    }
    function formatMetaRow(label, value) {
        return `<tr><td class="label">${label}</td><td>${value}</td></tr>`;
    }
    // Build review meta rows
    let metaRows = "";
    if (meta?.reviewed_commit) {
        const sha = meta.reviewed_commit.slice(0, 7);
        metaRows += formatMetaRow("Reviewed Commit", `<code>${esc(sha)}</code>`);
    }
    if (meta?.trigger_event) {
        metaRows += formatMetaRow("Trigger Event", `<code>${esc(meta.trigger_event)}</code>`);
    }
    if (meta?.workflow_run_url && /^https?:\/\//i.test(meta.workflow_run_url)) {
        metaRows += formatMetaRow("Workflow Run", `<a href="${esc(meta.workflow_run_url)}">view run ↗</a>`);
    }
    if (meta?.updated_at) {
        metaRows += formatMetaRow("Updated At", esc(meta.updated_at));
    }
    if (meta?.review_mode) {
        const mode = meta.review_mode === "incremental" ? "Incremental" : "Full PR";
        metaRows += formatMetaRow("Review Mode", mode);
    }
    let suggestionsHtml = "";
    if (result.suggestions.length === 0) {
        suggestionsHtml = `<p class="empty">No suggestions — clean review ✅</p>`;
    }
    else {
        for (let i = 0; i < result.suggestions.length; i++) {
            const s = result.suggestions[i];
            const confidence = Math.round(s.confidence * 100);
            const sevClass = `sev-${s.severity}`;
            suggestionsHtml += `
        <div class="suggestion">
          <div class="suggestion-header">
            <span class="index">#${i + 1}</span>
            <span class="badge ${sevClass}">${s.severity.toUpperCase()}</span>
            <span class="confidence">${confidence}%</span>
            <span class="title">${esc(s.title)}</span>
          </div>
          <div class="suggestion-location">
            📁 <code>${esc(s.file_path)}</code>${s.line ? ` : <code>L${s.line}</code>` : ""}
            <button class="open-code-btn" onclick="
              vscode.postMessage({cmd:'openCode', filePath:'${esc(s.file_path)}', line:${s.line ?? 0}})
            ">📍 Open Code</button>
          </div>
          ${s.reason ? `<div class="suggestion-reason">${esc(s.reason)}</div>` : ""}
          ${s.recommendation ? `<div class="suggestion-rec">💡 ${esc(s.recommendation)}</div>` : ""}
        </div>`;
        }
    }
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'nonce-${nonce}'; script-src 'nonce-${nonce}' 'strict-dynamic'; img-src ${webview.cspSource} https:;">
  <style nonce="${nonce}">
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family, -apple-system, sans-serif);
      font-size: 13px;
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      padding: 16px;
      line-height: 1.5;
    }
    .header {
      display: flex; align-items: center; gap: 10px; margin-bottom: 16px;
      padding-bottom: 12px; border-bottom: 1px solid var(--vscode-panel-border);
    }
    .header h2 { font-size: 16px; font-weight: 600; }
    .risk-badge {
      display: inline-block; padding: 2px 10px; border-radius: 10px;
      font-size: 11px; font-weight: 700; color: #fff;
    }
    .risk-high { background: #e74c3c; }
    .risk-medium { background: #f39c12; }
    .risk-low { background: #27ae60; }
    section { margin-bottom: 16px; }
    section h3 {
      font-size: 13px; font-weight: 600; margin-bottom: 8px;
      color: var(--vscode-sideBarTitle-foreground);
      text-transform: uppercase; letter-spacing: 0.5px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    td { padding: 4px 8px; border-bottom: 1px solid var(--vscode-panel-border); }
    td.label { width: 130px; color: var(--vscode-descriptionForeground); }
    code {
      font-family: var(--vscode-editor-font-family, monospace);
      font-size: 11px; background: var(--vscode-textCodeBlock-background);
      padding: 1px 4px; border-radius: 3px;
    }
    a { color: var(--vscode-textLink-foreground); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
    .toolbar button {
      padding: 4px 12px; border: none; border-radius: 3px; cursor: pointer;
      font-size: 12px; background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
    }
    .toolbar button:hover { background: var(--vscode-button-secondaryHoverBackground); }
    .toolbar button.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .toolbar button.primary:hover { background: var(--vscode-button-hoverBackground); }
    .suggestion {
      padding: 10px; margin-bottom: 8px; border-radius: 4px;
      background: var(--vscode-editor-background);
      border-left: 3px solid var(--vscode-textLink-foreground);
    }
    .suggestion-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
    .index { font-weight: 600; color: var(--vscode-descriptionForeground); min-width: 24px; }
    .badge { display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 700; color: #fff; }
    .sev-critical { background: #e74c3c; }
    .sev-high { background: #e67e22; }
    .sev-medium { background: #f1c40f; }
    .sev-low { background: #95a5a6; }
    .confidence { font-size: 11px; color: var(--vscode-descriptionForeground); }
    .title { font-weight: 600; }
    .suggestion-location { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 4px; }
    .suggestion-reason { font-size: 12px; margin-bottom: 4px; }
    .suggestion-rec { font-size: 12px; color: var(--vscode-textLink-foreground); }
    .open-code-btn {
      font-size: 11px; background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground); border: none;
      padding: 1px 8px; border-radius: 3px; cursor: pointer; margin-left: 8px;
    }
    .open-code-btn:hover { background: var(--vscode-button-secondaryHoverBackground); }
    .summary-text { font-size: 12px; white-space: pre-wrap; }
    .empty { color: var(--vscode-descriptionForeground); font-style: italic; padding: 20px 0; }
  </style>
</head>
<body>
  <div class="toolbar">
    <button class="primary" id="refresh-btn">🔄 Refresh</button>
    <button id="open-pr-btn">🔗 Open PR</button>
  </div>

  <div class="header">
    <h2>PR #${result.pr.number}: ${esc(result.pr.title)}</h2>
  </div>
  <span class="risk-badge ${riskClass}">${esc(risk)}</span>

  ${metaRows ? `<section><h3>📋 Review Metadata</h3><table>${metaRows}</table></section>` : ""}

  ${result.summary ? `<section><h3>📝 Summary</h3><p class="summary-text">${esc(result.summary.slice(0, 500))}${result.summary.length > 500 ? "..." : ""}</p></section>` : ""}

  <section><h3>🔍 Suggestions (${result.suggestions.length})</h3>${suggestionsHtml}</section>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.getElementById('refresh-btn').addEventListener('click', () => vscode.postMessage({cmd:'refresh'}));
    document.getElementById('open-pr-btn').addEventListener('click', () => vscode.postMessage({cmd:'openPr'}));
  </script>
</body>
</html>`;
}
function getNonce() {
    let text = "";
    const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    for (let i = 0; i < 32; i++) {
        text += possible.charAt(Math.floor(Math.random() * possible.length));
    }
    return text;
}
// ── Panel provider ─────────────────────────────────────
class ReviewPanelProvider {
    static viewType = "ai-pr-review.panel";
    _panel;
    _extensionUri;
    _onRefresh;
    _lastResult = null;
    constructor(extensionUri, onRefresh) {
        this._extensionUri = extensionUri;
        this._onRefresh = onRefresh;
    }
    async show(result) {
        this._lastResult = result;
        if (!this._panel) {
            this._panel = vscode.window.createWebviewPanel(ReviewPanelProvider.viewType, "AI PR Review", vscode.ViewColumn.Beside, {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [this._extensionUri],
            });
            this._panel.onDidDispose(() => { this._panel = undefined; });
            this._panel.webview.onDidReceiveMessage(async (msg) => {
                switch (msg.cmd) {
                    case "refresh": {
                        const r = await this._onRefresh();
                        if (r) {
                            this._lastResult = r;
                        }
                        if (r && this._panel) {
                            this._panel.webview.html = getWebviewHtml(r, this._panel.webview);
                        }
                        break;
                    }
                    case "openPr": {
                        const url = this._lastResult?.pr?.url;
                        if (url && /^https?:\/\//i.test(url)) {
                            vscode.env.openExternal(vscode.Uri.parse(url));
                        }
                        break;
                    }
                    case "openCode": {
                        const { filePath, line } = msg;
                        const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
                        if (workspaceRoot && filePath) {
                            const fileUri = vscode.Uri.joinPath(workspaceRoot, filePath);
                            const doc = await vscode.workspace.openTextDocument(fileUri);
                            const editor = await vscode.window.showTextDocument(doc, { preserveFocus: false });
                            if (line > 0) {
                                const pos = new vscode.Position(line - 1, 0);
                                editor.selection = new vscode.Selection(pos, pos);
                                editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
                            }
                        }
                        break;
                    }
                }
            });
        }
        this._panel.webview.html = getWebviewHtml(result, this._panel.webview);
        this._panel.reveal();
    }
    async showLoading() {
        if (!this._panel) {
            this._panel = vscode.window.createWebviewPanel(ReviewPanelProvider.viewType, "AI PR Review", vscode.ViewColumn.Beside, {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [this._extensionUri],
            });
            this._panel.onDidDispose(() => { this._panel = undefined; });
        }
        this._panel.webview.html = `<html><body style="font-family:var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-sideBar-background);display:flex;align-items:center;justify-content:center;height:100vh;"><p>⏳ Loading review results...</p></body></html>`;
        this._panel.reveal();
    }
    dispose() {
        this._panel?.dispose();
    }
}
exports.ReviewPanelProvider = ReviewPanelProvider;
//# sourceMappingURL=panel.js.map