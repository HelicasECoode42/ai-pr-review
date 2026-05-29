import os
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from dotenv import load_dotenv

load_dotenv()

class GitHubPRClient:
    def __init__(self, repo: str, pr_number: int):
        self.repo = repo
        self.pr_number = pr_number
        self.token = os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("请在 .env 文件中设置 GITHUB_TOKEN")
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def _request(self, endpoint: str) -> dict:
        url = f"https://api.github.com/repos/{self.repo}/{endpoint}"
        resp = requests.get(url, headers=self.headers, verify=False)
        resp.raise_for_status()
        return resp.json()

    def get_pr_info(self) -> dict:
        return self._request(f"pulls/{self.pr_number}")

    def get_pr_files(self) -> list:
        return self._request(f"pulls/{self.pr_number}/files")

    def get_diff_text(self) -> str:
        url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}"
        headers = self.headers.copy()
        headers["Accept"] = "application/vnd.github.v3.diff"
        resp = requests.get(url, headers=headers, verify=False)
        resp.raise_for_status()
        return resp.text
GitHubClient = GitHubPRClient