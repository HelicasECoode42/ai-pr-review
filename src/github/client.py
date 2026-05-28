from __future__ import annotations

import httpx

from src.models import ChangedFile, FileStatus, PullRequest


class GitHubClient:
    def __init__(self, token: str | None, timeout: float = 45.0) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-pr-review-assistant",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        response = self._client.get(f"/repos/{repo}/pulls/{number}")
        response.raise_for_status()
        data = response.json()
        return PullRequest(
            repo=repo,
            number=number,
            title=data["title"],
            body=data.get("body"),
            author=(data.get("user") or {}).get("login"),
            base_ref=(data.get("base") or {}).get("ref"),
            head_ref=(data.get("head") or {}).get("ref"),
            html_url=data.get("html_url"),
        )

    def get_changed_files(self, repo: str, number: int) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        page = 1
        while True:
            response = self._client.get(
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            for item in batch:
                files.append(
                    ChangedFile(
                        filename=item["filename"],
                        status=FileStatus(item.get("status", "unknown"))
                        if item.get("status") in FileStatus._value2member_map_
                        else FileStatus.UNKNOWN,
                        additions=item.get("additions", 0),
                        deletions=item.get("deletions", 0),
                        changes=item.get("changes", 0),
                        patch=item.get("patch"),
                        previous_filename=item.get("previous_filename"),
                    )
                )
            page += 1
        return files

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
