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
const diagnostics_1 = require("./diagnostics");
let diagnosticCollection;
function activate(context) {
    // Create the diagnostic collection
    diagnosticCollection = vscode.languages.createDiagnosticCollection((0, diagnostics_1.getCollectionName)());
    context.subscriptions.push(diagnosticCollection);
    // Command: Load Report
    const loadCmd = vscode.commands.registerCommand("ai-pr-review.loadReport", async () => {
        try {
            // Let user pick a JSON report file
            const uris = await vscode.window.showOpenDialog({
                canSelectFiles: true,
                canSelectFolders: false,
                canSelectMany: false,
                filters: {
                    "AI PR Review Report": ["json"],
                },
                openLabel: "Load Report",
                title: "Select a pr-review.json report file",
            });
            if (!uris || uris.length === 0)
                return;
            const fileUri = uris[0];
            const raw = await vscode.workspace.fs.readFile(fileUri);
            const json = Buffer.from(raw).toString("utf-8");
            const result = (0, diagnostics_1.parseAndCreateDiagnostics)(json);
            if (result instanceof Error) {
                vscode.window.showErrorMessage(`AI PR Review: ${result.message}`);
                return;
            }
            if (!diagnosticCollection)
                return;
            (0, diagnostics_1.applyDiagnostics)(diagnosticCollection, result);
            // Count total diagnostics
            let total = 0;
            for (const diags of result.values())
                total += diags.length;
            vscode.window.showInformationMessage(`AI PR Review: Loaded ${total} suggestion(s) from ${vscode.workspace.asRelativePath(fileUri)}`);
        }
        catch (err) {
            vscode.window.showErrorMessage(`AI PR Review: Unexpected error — ${err instanceof Error ? err.message : String(err)}`);
        }
    });
    // Command: Clear Diagnostics
    const clearCmd = vscode.commands.registerCommand("ai-pr-review.clearDiagnostics", () => {
        if (diagnosticCollection) {
            diagnosticCollection.clear();
        }
        vscode.window.showInformationMessage("AI PR Review: Diagnostics cleared.");
    });
    context.subscriptions.push(loadCmd, clearCmd);
}
function deactivate() {
    if (diagnosticCollection) {
        diagnosticCollection.clear();
        diagnosticCollection.dispose();
    }
}
//# sourceMappingURL=extension.js.map