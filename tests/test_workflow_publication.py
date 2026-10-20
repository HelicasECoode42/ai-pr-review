import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from src.reviewer.publish import review_comment_body, review_publication_fingerprint

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required by github-script")
def test_reusable_workflow_inline_publication_dedupes_and_remembers_author_dismissal(
    tmp_path,
):
    workflow = (ROOT / ".github/workflows/ai-pr-review-reusable.yml").read_text()
    start = workflow.index("      - name: Post inline comments (opt-in)")
    script_start = workflow.index("          script: |\n", start) + len("          script: |\n")
    script_end = workflow.index("\n      - name: Upload review artifacts", script_start)
    script = textwrap.dedent(workflow[script_start:script_end])
    assert "createReviewComment" in script
    assert "already_published" in script

    head = "b" * 40
    suggestion = {
        "file_path": "src/example.py", "line": 4, "severity": "high",
        "confidence": 0.9, "title": "Broken contract", "reason": "The caller now fails.",
        "recommendation": "Keep the return shape stable.",
    }
    unavailable_suggestion = {
        "file_path": "src/other.py", "line": 9, "severity": "high",
        "confidence": 0.95, "title": "Missing fingerprint", "reason": "A second candidate.",
        "recommendation": "Keep its identity available.",
    }
    criteria = "grounded-v3"
    fingerprint = review_publication_fingerprint(
        repo="owner/repo", pr_number=3, head_sha=head, criteria_version=criteria,
        file_path=suggestion["file_path"], line=suggestion["line"],
        body=review_comment_body(type("Suggestion", (), suggestion)()),
    )
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "pr-review.json").write_text(json.dumps({
        "pr": {"repo": "owner/repo", "number": 3, "head_sha": head},
        "metrics": {
            "criteria_version": criteria,
            "publish_min_confidence": 0.8,
            "publish_fingerprints": json.dumps({"0": fingerprint}),
        },
        "suggestions": [suggestion, unavailable_suggestion],
    }))
    script_path = tmp_path / "inline.js"
    script_path.write_text(script)
    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent("""
        const fs = require("fs");
        const script = fs.readFileSync(process.argv[2], "utf8");
        const comments = [];
        let latestHead = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const context = {
          repo: { owner: "owner", repo: "repo" },
          payload: { pull_request: { number: 3, user: { login: "alice" } } },
        };
        const github = {
          paginate: async (fn, args) => (await fn(args)).data,
          rest: {
            pulls: {
              get: async () => ({ data: { head: { sha: latestHead }, user: { login: "alice" } } }),
              listReviewComments: async () => ({ data: comments }),
              createReviewComment: async args => {
                comments.push({ id: comments.length + 1, user: { login: "bot[bot]" },
                  path: args.path, line: args.line, body: args.body });
                return { data: comments[comments.length - 1] };
              },
            },
            users: { getAuthenticated: async () => ({ data: { login: "bot[bot]" } }) },
          },
        };
        const core = { warning() {}, info() {} };
        const fakeProcess = { env: { PR_NUMBER: "3" } };
        async function run() {
          const execute = new Function("github", "context", "core", "process", "require",
            `return (async () => {${script}\\n})()`);
          await execute(github, context, core, fakeProcess, require);
          return JSON.parse(fs.readFileSync("reports/publication.json", "utf8"));
        }
        (async () => {
          const first = await run();
          const second = await run();
          comments.push({ id: 9, in_reply_to_id: 1, user: { login: "alice" },
            body: "/ai-review dismiss" });
          const dismissed = await run();
          const reportPath = "reports/pr-review.json";
          const terminalReport = JSON.parse(fs.readFileSync(reportPath, "utf8"));
          terminalReport.suggestions = [terminalReport.suggestions[0]];
          fs.writeFileSync(reportPath, JSON.stringify(terminalReport));
          const terminal = await run();
          latestHead = "c".repeat(40);
          const stale = await run();
          console.log(JSON.stringify({ first, second, dismissed, terminal, stale,
            posted: comments.filter(c => c.user?.login === "bot[bot]").length }));
        })().catch(error => { console.error(error); process.exit(1); });
    """))
    completed = subprocess.run(
        [shutil.which("node"), str(harness), str(script_path)], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    result = json.loads(completed.stdout)
    assert result["first"]["candidates"][0]["status"] == "published"
    assert result["first"]["candidates"][1]["status"] == "unavailable_fingerprint"
    assert result["first"]["status"] == "partial"
    assert result["second"]["candidates"][0]["status"] == "already_published"
    assert result["dismissed"]["candidates"][0]["status"] == "dismissed_by_pr_author"
    assert result["terminal"]["candidates"][0]["status"] == "dismissed_by_pr_author"
    assert result["terminal"]["status"] == "complete"
    assert result["stale"]["status"] == "stale_head"
    assert result["posted"] == 1
