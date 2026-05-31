import * as vscode from "vscode";
import type { ReviewResult } from "./review-fetcher";
export declare class ReviewPanelProvider {
    static readonly viewType = "ai-pr-review.panel";
    private _panel;
    private _extensionUri;
    private _onRefresh;
    private _onResultChanged;
    private _lastResult;
    private _currentDecoration;
    private _decorationDisposable;
    constructor(extensionUri: vscode.Uri, onRefresh: () => Promise<ReviewResult | null>, onResultChanged: (result: ReviewResult) => void);
    /** Ensure the webview panel is created, with message handler bound exactly once. */
    private _ensurePanel;
    show(result: ReviewResult): Promise<void>;
    showLoading(): Promise<void>;
    private _cleanupDecoration;
    dispose(): void;
}
