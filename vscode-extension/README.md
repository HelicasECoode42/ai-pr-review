# AI PR Review — VS Code Extension

Auto-load AI PR Review results from GitHub Actions into VS Code Problems panel and Review Panel.

## How it works

1. Push a PR → GitHub Actions runs `ai-pr-review` → posts review comments
2. Open VS Code on the PR branch → extension auto-detects the PR
3. Status bar shows issue count, Problems panel gets diagnostics, Review Panel shows full report

## Requirements

- **`gh` CLI** installed and authenticated (`gh auth login`)
- **Git** repository with an `origin` remote on GitHub
- VS Code `^1.85.0`

## Install

### From VSIX

Download the latest `ai-pr-review-*.vsix` from [GitHub Releases](https://github.com/HelicasECoode42/ai-pr-review/releases), then:

```
Extensions panel → ... → Install from VSIX... → select the .vsix file
```

### From source

```bash
cd vscode-extension
npm install
npm run compile
# Then F5 in VS Code to launch Extension Development Host
```

## Usage

- Open any project with an open PR
- Status bar (bottom-left) shows `AI Review: N issues / M high`
- Problems panel (`Cmd+Shift+M`) lists suggestions linked to source code — click to jump
- **`AI PR Review: Open Review Panel`** opens a side panel with full PR summary, metadata, and suggestions
- **`AI PR Review: Refresh`** re-fetches the latest review results

### Commands

| Command | Description |
|---------|-------------|
| `AI PR Review: Refresh` | Re-fetch review from GitHub |
| `AI PR Review: Open Review Panel` | Open detailed review panel |
| `AI PR Review: Load Report File` | Load a local `pr-review.json` |
| `AI PR Review: Clear Diagnostics` | Clear all review diagnostics |

## Package

```bash
npm run package
# → ai-pr-review-0.2.0.vsix
```
