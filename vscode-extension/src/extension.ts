import * as vscode from "vscode";
import {
  parseAndCreateDiagnostics,
  applyDiagnostics,
  getCollectionName,
} from "./diagnostics";

let diagnosticCollection: vscode.DiagnosticCollection | undefined;

export function activate(context: vscode.ExtensionContext): void {
  // Create the diagnostic collection
  diagnosticCollection = vscode.languages.createDiagnosticCollection(
    getCollectionName(),
  );
  context.subscriptions.push(diagnosticCollection);

  // Command: Load Report
  const loadCmd = vscode.commands.registerCommand(
    "ai-pr-review.loadReport",
    async () => {
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

        if (!uris || uris.length === 0) return;

        const fileUri = uris[0];
        const raw = await vscode.workspace.fs.readFile(fileUri);
        const json = Buffer.from(raw).toString("utf-8");

        const result = parseAndCreateDiagnostics(json);
        if (result instanceof Error) {
          vscode.window.showErrorMessage(
            `AI PR Review: ${result.message}`,
          );
          return;
        }

        if (!diagnosticCollection) return;
        applyDiagnostics(diagnosticCollection, result);

        // Count total diagnostics
        let total = 0;
        for (const diags of result.values()) total += diags.length;

        vscode.window.showInformationMessage(
          `AI PR Review: Loaded ${total} suggestion(s) from ${vscode.workspace.asRelativePath(fileUri)}`,
        );
      } catch (err: unknown) {
        vscode.window.showErrorMessage(
          `AI PR Review: Unexpected error — ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
  );

  // Command: Clear Diagnostics
  const clearCmd = vscode.commands.registerCommand(
    "ai-pr-review.clearDiagnostics",
    () => {
      if (diagnosticCollection) {
        diagnosticCollection.clear();
      }
      vscode.window.showInformationMessage(
        "AI PR Review: Diagnostics cleared.",
      );
    },
  );

  context.subscriptions.push(loadCmd, clearCmd);
}

export function deactivate(): void {
  if (diagnosticCollection) {
    diagnosticCollection.clear();
    diagnosticCollection.dispose();
  }
}
