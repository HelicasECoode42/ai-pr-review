import * as vscode from "vscode";
import type { ReviewResult } from "./review-fetcher";
import type { ReviewMeta } from "./report";
import { resolveFileUri } from "./diagnostics";

// ── HTML template ──────────────────────────────────────

function getWebviewHtml(
  result: ReviewResult,
  webview: vscode.Webview,
): string {
  const nonce = getNonce();
  const meta = result.reviewMeta;
  const risk = result.riskLevel ?? "N/A";
  const riskClass = risk === "HIGH" ? "risk-high" : risk === "MEDIUM" ? "risk-medium" : "risk-low";

  function esc(s: string | null | undefined): string {
    if (!s) return "-";
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
      .replace(/`/g, "&#96;");
  }

  function formatMetaRow(label: string, value: string): string {
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
    metaRows += formatMetaRow(
      "Workflow Run",
      `<a href="${esc(meta.workflow_run_url)}">view run ↗</a>`,
    );
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
  } else {
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
            <button class="open-code-btn"
              data-file="${esc(s.file_path)}" data-line="${s.line ?? 0}"
              data-severity="${esc(s.severity)}" data-title="${esc(s.title)}"
              data-reason="${esc(s.reason)}" data-recommendation="${esc(s.recommendation)}"
            >📍 Open Code</button>
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
    // Open Code buttons — use data attributes instead of inline onclick (CSP-safe)
    document.querySelectorAll('.open-code-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const filePath = btn.getAttribute('data-file');
        const line = parseInt(btn.getAttribute('data-line') || '0', 10);
        vscode.postMessage({cmd:'openCode', filePath, line,
          // Pass risk info so the extension can show it as a diagnostic at the jumped line
          severity: btn.getAttribute('data-severity'),
          title: btn.getAttribute('data-title'),
          reason: btn.getAttribute('data-reason'),
          recommendation: btn.getAttribute('data-recommendation'),
        });
      });
    });
  </script>
</body>
</html>`;
}

function getNonce(): string {
  let text = "";
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}

// ── Panel provider ─────────────────────────────────────

export class ReviewPanelProvider {
  public static readonly viewType = "ai-pr-review.panel";
  private _panel: vscode.WebviewPanel | undefined;
  private _extensionUri: vscode.Uri;
  private _onRefresh: () => Promise<ReviewResult | null>;
  private _onResultChanged: (result: ReviewResult) => void;
  private _lastResult: ReviewResult | null = null;
  private _currentDecoration: vscode.TextEditorDecorationType | undefined;
  private _decorationDisposable: vscode.Disposable | undefined;

  constructor(
    extensionUri: vscode.Uri,
    onRefresh: () => Promise<ReviewResult | null>,
    onResultChanged: (result: ReviewResult) => void,
  ) {
    this._extensionUri = extensionUri;
    this._onRefresh = onRefresh;
    this._onResultChanged = onResultChanged;
  }

  /** Ensure the webview panel is created, with message handler bound exactly once. */
  private _ensurePanel(): vscode.WebviewPanel {
    if (!this._panel) {
      this._panel = vscode.window.createWebviewPanel(
        ReviewPanelProvider.viewType,
        "AI PR Review",
        vscode.ViewColumn.Beside,
        {
          enableScripts: true,
          retainContextWhenHidden: true,
          localResourceRoots: [this._extensionUri],
        },
      );
      this._panel.onDidDispose(() => { this._panel = undefined; });

      // Message handler registered ONCE — always available regardless of
      // whether the panel was created via show() or showLoading() first.
      this._panel.webview.onDidReceiveMessage(async (msg) => {
        switch (msg.cmd) {
          case "refresh": {
            const r = await this._onRefresh();
            if (r) {
              this._onResultChanged(r);
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
            const { filePath, line, severity, title, reason, recommendation } = msg;
            if (!filePath) {
              vscode.window.showWarningMessage("AI PR Review: No file path in this suggestion.");
              break;
            }
            try {
              const fileUri = resolveFileUri(filePath);
              const doc = await vscode.workspace.openTextDocument(fileUri);
              const editor = await vscode.window.showTextDocument(doc, { preserveFocus: false });

              if (line > 0) {
                const pos = new vscode.Position(line - 1, 0);
                const lineEnd = doc.lineAt(pos).range.end;
                const range = new vscode.Range(pos, lineEnd);

                editor.selection = new vscode.Selection(pos, pos);
                editor.revealRange(range, vscode.TextEditorRevealType.InCenter);

                // Build hover message with full problem context
                const sevIcon = severity === 'critical' || severity === 'high'
                  ? '🔴' : severity === 'medium' ? '🟡' : '🟢';
                const hoverMarkdown = new vscode.MarkdownString();
                hoverMarkdown.isTrusted = true;
                hoverMarkdown.supportHtml = true;
                hoverMarkdown.appendMarkdown(
                  `### ${sevIcon} [${(severity || '?').toUpperCase()}] ${title || '(no title)'}\n\n`
                );
                if (reason) {
                  hoverMarkdown.appendMarkdown(`---\n\n**📋 Reason:** ${reason}\n\n`);
                }
                if (recommendation) {
                  hoverMarkdown.appendMarkdown(`---\n\n**💡 Recommendation:** ${recommendation}\n\n`);
                }
                hoverMarkdown.appendMarkdown(`---\n\n📁 *${filePath}:${line}*`);

                // Show decoration at the target line with hover message
                this._cleanupDecoration();
                this._currentDecoration = vscode.window.createTextEditorDecorationType({
                  backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
                  borderColor: new vscode.ThemeColor('editorInfo.foreground'),
                  borderStyle: 'solid',
                  borderWidth: '0 0 0 2px',
                  isWholeLine: true,
                  overviewRulerColor: new vscode.ThemeColor('editorInfo.foreground'),
                  overviewRulerLane: vscode.OverviewRulerLane.Right,
                });
                editor.setDecorations(this._currentDecoration, [
                  { range, hoverMessage: hoverMarkdown },
                ]);

                // Auto-clear decoration when user moves cursor away
                const targetLine = line;
                this._decorationDisposable?.dispose();
                this._decorationDisposable = vscode.window.onDidChangeTextEditorSelection((e) => {
                  if (e.textEditor === editor && e.selections[0]?.active.line !== targetLine - 1) {
                    this._cleanupDecoration();
                    this._decorationDisposable?.dispose();
                    this._decorationDisposable = undefined;
                  }
                });
              } else {
                vscode.window.showInformationMessage(
                  `AI PR Review: Opened ${filePath} (no line number specified).`
                );
              }
            } catch (err: unknown) {
              const msg = err instanceof Error ? err.message : String(err);
              vscode.window.showErrorMessage(
                `AI PR Review: Cannot open ${filePath} — ${msg}`
              );
            }
            break;
          }
        }
      });
    }
    return this._panel;
  }

  async show(result: ReviewResult): Promise<void> {
    this._lastResult = result;
    const panel = this._ensurePanel();
    panel.webview.html = getWebviewHtml(result, panel.webview);
    panel.reveal();
  }

  async showLoading(): Promise<void> {
    const panel = this._ensurePanel();
    panel.webview.html = `<html><body style="font-family:var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-sideBar-background);display:flex;align-items:center;justify-content:center;height:100vh;"><p>⏳ Loading review results...</p></body></html>`;
    panel.reveal();
  }

  private _cleanupDecoration(): void {
    this._currentDecoration?.dispose();
    this._currentDecoration = undefined;
  }

  dispose(): void {
    this._cleanupDecoration();
    this._decorationDisposable?.dispose();
    this._panel?.dispose();
  }
}
