import * as vscode from "vscode";
import type { ReviewResult } from "./review-fetcher";
export declare class ReviewPanelProvider {
    static readonly viewType = "ai-pr-review.panel";
    private _panel;
    private _extensionUri;
    private _onRefresh;
    private _lastResult;
    constructor(extensionUri: vscode.Uri, onRefresh: () => Promise<ReviewResult | null>);
    show(result: ReviewResult): Promise<void>;
    showLoading(): Promise<void>;
    dispose(): void;
}
