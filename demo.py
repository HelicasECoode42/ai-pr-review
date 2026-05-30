"""
简易演示脚本：无需 CLI，直接验证你的模块能扫描一个真实的 PR。
使用前请确保 .env 文件中已有 GITHUB_TOKEN。
"""

import os
import requests
from dotenv import load_dotenv
from src.analyzer.diff_parser import parse_file_hunks
from src.analyzer.risk_rules import scan_risks
from src.analyzer.context_builder import build_review_context
from src.models import ChangedFile, PullRequest

load_dotenv()
TOKEN = os.getenv("GITHUB_TOKEN")
if not TOKEN:
    raise ValueError("请在 .env 中设置 GITHUB_TOKEN")

def main():
    REPO = "HelicasECoode42/ai-pr-review"
    PR_NUMBER = 1   # 改成真实存在的 PR 号

    print("[INFO] Fetching PR", REPO, "#", PR_NUMBER)

    # 获取 PR 元信息
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}"
    headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    pr_data = resp.json()

    # 获取 diff 文本（用于解析 hunks，实际上我们后面会直接从 files 里取 patch）
    # 这里为了完整，也获取一次
    diff_url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}"
    headers_diff = headers.copy()
    headers_diff["Accept"] = "application/vnd.github.v3.diff"
    diff_text = requests.get(diff_url, headers=headers_diff).text

    # 获取文件列表
    files_url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}/files"
    files_resp = requests.get(files_url, headers=headers)
    files_data = files_resp.json()

    changed_files = []
    for item in files_data:
        changed_files.append(ChangedFile(
            filename=item["filename"],
            status=item.get("status", "modified"),
            additions=item.get("additions", 0),
            deletions=item.get("deletions", 0),
            patch=item.get("patch", "")
        ))

    print("[INFO] Running risk rules...")
    findings = scan_risks(changed_files)

    pr = PullRequest(
        repo=REPO,
        number=PR_NUMBER,
        title=pr_data["title"],
        body=pr_data.get("body"),
        author=pr_data.get("user", {}).get("login"),
        base_ref=pr_data.get("base", {}).get("ref"),
        head_ref=pr_data.get("head", {}).get("ref")
    )
    context = build_review_context(pr, changed_files, findings)

    print("\n" + "="*60)
    print("PR TITLE:", pr.title)
    print("AUTHOR:", pr.author)
    print("FILES CHANGED:", len(changed_files))
    print("RISK FINDINGS:", len(findings))
    for f in findings:
        print(f"  - [{f.severity.value}] {f.title} @ {f.file_path}:{f.line or '?'}")
    print("\nGENERATED CONTEXT (first 500 chars):")
    print(context[:500] + "..." if len(context) > 500 else context)
    print("="*60)

if __name__ == "__main__":
    main()