"""
简易演示脚本：无需 CLI，直接验证你的模块能扫描一个真实的 PR。
使用前请确保 .env 文件中已有 GITHUB_TOKEN。
"""

from src.analyzer.risk_rules import scan_risks
from src.analyzer.context_builder import build_review_context
from src.github.client import GitHubClient
from src.models import PullRequest
from src.utils.config import get_settings


def main():
    settings = get_settings()
    token = settings.github_token
    if not token:
        raise ValueError("请在 .env 中设置 GITHUB_TOKEN")

    REPO = "HelicasECoode42/ai-pr-review"
    PR_NUMBER = 1  # 改成真实存在的 PR 号

    print("[INFO] Fetching PR", REPO, "#", PR_NUMBER)

    with GitHubClient(token=token) as client:
        # 获取 PR 元信息
        pr: PullRequest = client.get_pull_request(REPO, PR_NUMBER)
        # 获取变更文件列表
        changed_files = client.get_changed_files(REPO, PR_NUMBER)

    print("[INFO] Running risk rules...")
    findings = scan_risks(changed_files)

    context = build_review_context(pr, changed_files, findings)

    print("\n" + "=" * 60)
    print("PR TITLE:", pr.title)
    print("AUTHOR:", pr.author)
    print("FILES CHANGED:", len(changed_files))
    print("RISK FINDINGS:", len(findings))
    for f in findings:
        print(f"  - [{f.severity.value}] {f.title} @ {f.file_path}:{f.line or '?'}")
    print("\nGENERATED CONTEXT (first 500 chars):")
    print(context[:500] + "..." if len(context) > 500 else context)
    print("=" * 60)


if __name__ == "__main__":
    main()
