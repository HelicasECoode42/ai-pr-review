from __future__ import annotations

import httpx

from src.models import ChangedFile, FileStatus, PullRequest


class GitHubApiError(Exception):
    """Raised when the GitHub API returns an error or is unreachable."""


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

    def _request(self, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.get(path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise GitHubApiError("GitHub API 请求超时，请检查网络或稍后重试。") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise GitHubApiError(
                    "GitHub API 认证失败：token 无效或已过期。"
                ) from exc
            if status == 403:
                raise GitHubApiError(
                    "GitHub API 访问被拒绝：可能是 rate limit 超限或权限不足。"
                ) from exc
            if status == 404:
                raise GitHubApiError(
                    "仓库或 PR 不存在，请检查 owner/repo 和 PR 编号是否正确。"
                ) from exc
            raise GitHubApiError(
                f"GitHub API 返回错误 (HTTP {status})。"
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubApiError(
                f"无法连接 GitHub API：{exc}"
            ) from exc

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        response = self._request(f"/repos/{repo}/pulls/{number}")
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
            response = self._request(
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
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
