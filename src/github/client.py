from __future__ import annotations

import httpx
import logging
from typing import Optional

from src.models import ChangedFile, FileStatus, PullRequest

logger = logging.getLogger(__name__)


class GitHubApiError(Exception):
    """Raised when GitHub API request fails (network, auth, HTTP error)."""
    pass


class GitHubClient:
    def __init__(self, token: str | None, timeout: float = 45.0) -> None:
        self.token = token
        self.timeout = timeout
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-pr-review-assistant",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            logger.warning("GitHub token not provided, API requests may be limited")
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, path: str, **kwargs: object) -> httpx.Response:
        """Make a GET request; raise GitHubApiError on failure."""
        try:
            response = self._client.get(path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise GitHubApiError(f"GitHub API timeout: {path}")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise GitHubApiError("Authentication failed: token invalid or expired")
            if status == 403:
                raise GitHubApiError("Access denied: rate limit exceeded or insufficient permissions")
            if status == 404:
                raise GitHubApiError(f"Resource not found: {path}")
            raise GitHubApiError(f"HTTP {status}: {path}")
        except httpx.RequestError as exc:
            raise GitHubApiError(f"Failed to connect to GitHub API: {exc}")

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        response = self._request(f"/repos/{repo}/pulls/{number}")
        data = response.json()
        return PullRequest(
            repo=repo,
            number=number,
            title=data.get("title", ""),
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
                try:
                    status_str = item.get("status", "unknown")
                    status = FileStatus(status_str) if status_str in FileStatus._value2member_map_ else FileStatus.UNKNOWN
                    files.append(
                        ChangedFile(
                            filename=item.get("filename", ""),
                            status=status,
                            additions=item.get("additions", 0),
                            deletions=item.get("deletions", 0),
                            changes=item.get("changes", 0),
                            patch=item.get("patch"),
                            previous_filename=item.get("previous_filename"),
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse file entry: {e}, skipping")
            page += 1
        return files

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()