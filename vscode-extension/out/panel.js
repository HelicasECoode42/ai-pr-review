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
    const riskColor = risk === "HIGH" ? "#e74c3c" : risk === "MEDIUM" ? "#f39c12" : "#27ae60";
    function esc(s) {
        if (!s)
            return "-";
        return s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
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
    if (meta?.workflow_run_url) {
        metaRows += formatMetaRow("Workflow Run", `<a href="${esc(meta.workflow_run_url)}">view run ↗</a>`);
    }
    if (meta?.updated_at) {
        metaRows += formatMetaRow("Updated At", esc(meta.updated_at));
    }
    if (meta?.review_mode) {
        const mode = meta.review_mode === "incremental" ? "Incremental" : "Full PR";
        metaRows += formatMetaRow("Review Mode", mode);
    }
    // Build suggestions list
    function severityBadge(s) {
        const colors = {
            critical: "#e74c3c",
            high: "#e67e22",
            medium: "#f1c40f",
            low: "#95a5a6",
        };
        const c = colors[s] ?? "#95a5a6";
        return `<span class="badge" style="background:${c}">${s.toUpperCase()}</span>`;
    }
    let suggestionsHtml = "";
    if (result.suggestions.length === 0) {
        suggestionsHtml = `<p class="empty">No suggestions — clean review ✅</p>`;
    }
    else {
        for (let i = 0; i < result.suggestions.length; i++) {
            const s = result.suggestions[i];
            const confidence = Math.round(s.confidence * 100);
            suggestionsHtml += `
        <div class="suggestion">
          <div class="suggestion-header">
            <span class="index">#${i + 1}</span>
            ${severityBadge(s.severity)}
            <span class="confidence">${confidence}%</span>
            <span class="title">${esc(s.title)}</span>
          </div>
          <div class="suggestion-location">
            📁 <code>${esc(s.file_path)}</code>${s.line ? ` : <code>L${s.line}</code>` : ""}
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
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; img-src ${webview.cspSource} https:;">
  <style>
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
    .header .pr-link { font-size: 12px; color: var(--vscode-textLink-foreground); text-decoration: none; }
    .risk-badge {
      display: inline-block; padding: 2px 10px; border-radius: 10px;
      font-size: 11px; font-weight: 700; color: #fff;
    }
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
    .confidence { font-size: 11px; color: var(--vscode-descriptionForeground); }
    .title { font-weight: 600; }
    .suggestion-location { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 4px; }
    .suggestion-reason { font-size: 12px; margin-bottom: 4px; }
    .suggestion-rec { font-size: 12px; color: var(--vscode-textLink-foreground); }
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
  <span class="risk-badge" style="background:${riskColor}">${risk}</span>

  ${metaRows ? `<section><h3>📋 Review Metadata</h3><table>${metaRows}</table></section>` : ""}

  ${result.summary ? `<section><h3>📝 Summary</h3><p style="font-size:12px;white-space:pre-wrap;">${esc(result.summary.slice(0, 500))}${result.summary.length > 500 ? "..." : ""}</p></section>` : ""}

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
    constructor(extensionUri, onRefresh) {
        this._extensionUri = extensionUri;
        this._onRefresh = onRefresh;
    }
    async show(result) {
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
                        if (r && this._panel) {
                            this._panel.webview.html = getWebviewHtml(r, this._panel.webview);
                        }
                        break;
                    }
                    case "openPr": {
                        if (result?.pr?.url && /^https?:\/\//i.test(result.pr.url)) {
                            vscode.env.openExternal(vscode.Uri.parse(result.pr.url));
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