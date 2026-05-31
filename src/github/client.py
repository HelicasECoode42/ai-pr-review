from __future__ import annotations

import httpx
import logging
from typing import Optional
import base64

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
            raise GitHubApiError(f"GitHub API timeout: {path}") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise GitHubApiError("Authentication failed: token invalid or expired") from exc
            if status == 403:
                raise GitHubApiError("Access denied: rate limit exceeded or insufficient permissions") from exc
            if status == 404:
                raise GitHubApiError(f"Resource not found: {path}") from exc
            raise GitHubApiError(f"HTTP {status}: {path}") from exc
        except httpx.RequestError as exc:
            raise GitHubApiError(f"Failed to connect to GitHub API: {exc}") from exc

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        response = self._request(f"/repos/{repo}/pulls/{number}")
        data = response.json()
        head_info = data.get("head") or {}
        return PullRequest(
            repo=repo,
            number=number,
            title=data.get("title", ""),
            body=data.get("body"),
            author=(data.get("user") or {}).get("login"),
            base_ref=(data.get("base") or {}).get("ref"),
            head_ref=head_info.get("ref"),
            head_sha=head_info.get("sha"),
            html_url=data.get("html_url"),
        )

    def list_pull_requests(self, repo: str, count: int = 10) -> list[PullRequest]:
        """List recent PRs for a repository (updated desc)."""
        response = self._request(
            f"/repos/{repo}/pulls",
            params={"state": "all", "per_page": count, "sort": "updated", "direction": "desc"},
        )
        data = response.json()
        if not isinstance(data, list):
            return []
        results: list[PullRequest] = []
        for item in data[:count]:
            try:
                head_info = item.get("head") or {}
                results.append(PullRequest(
                    repo=repo,
                    number=item.get("number", 0),
                    title=item.get("title", ""),
                    body=item.get("body"),
                    author=(item.get("user") or {}).get("login"),
                    base_ref=(item.get("base") or {}).get("ref"),
                    head_ref=head_info.get("ref"),
                    head_sha=head_info.get("sha"),
                    html_url=item.get("html_url"),
                ))
            except Exception:
                continue
        return results

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

    def get_file_contents(self, repo: str, path: str, ref: str | None = None) -> str | None:
        """Fetch file contents from a repository at a given ref (branch or commit).

        Returns the decoded file content as text or None if not found.
        Raises GitHubApiError on network/auth errors.
        """
        params = {}
        if ref:
            params["ref"] = ref
        try:
            response = self._request(f"/repos/{repo}/contents/{path}", params=params)
            data = response.json()
            content = data.get("content")
            encoding = data.get("encoding")
            if not content:
                return None
            if encoding == "base64":
                try:
                    decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                    return decoded
                except Exception:
                    # Return the raw content as a fallback
                    return content
            return content
        except GitHubApiError:
            # Re-raise for caller to handle
            raise
        except Exception as e:
            raise GitHubApiError(f"Failed to fetch file contents for {repo}/{path}@{ref}: {e}") from e

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()