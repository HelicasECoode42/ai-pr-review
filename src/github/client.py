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


def _as_int(value: object) -> int:
    """GitHub counts are integers; a wrong type becomes 0 instead of a crash."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _changed_file(item: dict) -> ChangedFile:
    """Build one ChangedFile from an API entry by sanitizing its fields.

    A surprising field type must not remove a changed file from the review input:
    coverage accounting only sees the files it is handed, so a file dropped here
    would never show up as an omission anywhere downstream.
    """
    status_str = item.get("status")
    return ChangedFile(
        filename=item["filename"],
        status=(
            FileStatus(status_str)
            if isinstance(status_str, str) and status_str in FileStatus._value2member_map_
            else FileStatus.UNKNOWN
        ),
        additions=_as_int(item.get("additions")),
        deletions=_as_int(item.get("deletions")),
        changes=_as_int(item.get("changes")),
        patch=_as_text(item.get("patch")),
        previous_filename=_as_text(item.get("previous_filename")),
    )


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

    def _get(self, path: str, **kwargs: object) -> httpx.Response:
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
        response = self._get(f"/repos/{repo}/pulls/{number}")
        data = response.json()
        head_info = data.get("head") or {}
        return PullRequest(
            repo=repo,
            number=number,
            title=data.get("title", ""),
            body=data.get("body"),
            author=(data.get("user") or {}).get("login"),
            base_ref=(data.get("base") or {}).get("ref"),
            base_sha=(data.get("base") or {}).get("sha"),
            head_ref=head_info.get("ref"),
            head_sha=head_info.get("sha"),
            html_url=data.get("html_url"),
        )

    def list_pull_requests(self, repo: str, count: int = 10) -> list[PullRequest]:
        """List recent PRs for a repository (updated desc)."""
        response = self._get(
            f"/repos/{repo}/pulls",
            params={"state": "all", "per_page": count, "sort": "updated", "direction": "desc"},
        )
        data = response.json()
        if not isinstance(data, list):
            return []
        results: list[PullRequest] = []
        for index, item in enumerate(data[:count]):
            if not isinstance(item, dict):
                # This list is a convenience index, not review input, so a bad
                # entry is skipped — but never silently.
                logger.warning("Skipping malformed PR entry #%d from %s", index, repo)
                continue
            head_info = item.get("head") or {}
            results.append(PullRequest(
                repo=repo,
                number=_as_int(item.get("number")),
                title=_as_text(item.get("title")) or "",
                body=_as_text(item.get("body")),
                author=_as_text((item.get("user") or {}).get("login")),
                base_ref=_as_text((item.get("base") or {}).get("ref")),
                head_ref=_as_text(head_info.get("ref")),
                head_sha=_as_text(head_info.get("sha")),
                html_url=_as_text(item.get("html_url")),
            ))
        return results

    def get_compare(self, repo: str, base: str, head: str) -> list[ChangedFile]:
        """Get diff between two commits via the GitHub Compare API.

        Returns ChangedFile list like get_changed_files, for incremental review.
        base/head can be branch names or commit SHAs.
        """

        response = self._get(
            f"/repos/{repo}/compare/{base}...{head}",
            params={"per_page": 300},
        )
        data = response.json()
        raw_files = data.get("files") or []
        result: list[ChangedFile] = []
        for index, item in enumerate(raw_files):
            if not isinstance(item, dict) or not _as_text(item.get("filename")):
                # Loud, because skipping would silently shrink the reviewed change set.
                raise GitHubApiError(
                    f"Malformed file entry #{index} in compare {base}...{head}: missing filename"
                )
            result.append(_changed_file(item))
        return result

    def get_changed_files(self, repo: str, number: int) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        page = 1
        while True:
            response = self._get(
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            batch = response.json()
            if not batch:
                break
            for index, item in enumerate(batch):
                if not isinstance(item, dict) or not _as_text(item.get("filename")):
                    # Loud, because skipping would silently shrink the reviewed change set
                    # before any coverage accounting can record the omission.
                    raise GitHubApiError(
                        f"Malformed file entry #{index} on page {page} of {repo}#{number}:"
                        " missing filename"
                    )
                files.append(_changed_file(item))
            page += 1
        return files

    def get_review_comments(self, repo: str, number: int) -> list[dict]:
        """Fetch PR review comments (for fix tracking comparison).

        Returns a list of comment dicts with keys: id, path, line, body, user.login, created_at.
        """
        try:
            response = self._get(f"/repos/{repo}/pulls/{number}/comments")
            data = response.json()
            if not isinstance(data, list):
                return []
            return [
                {
                    "id": c.get("id"),
                    "path": c.get("path", ""),
                    "line": c.get("line"),
                    "body": c.get("body", ""),
                    "author": (c.get("user") or {}).get("login", ""),
                    "created_at": c.get("created_at", ""),
                }
                for c in data
            ]
        except Exception as exc:
            logger.warning("Failed to fetch review comments for %s#%d: %s", repo, number, exc)
            return []

    def get_file_contents(self, repo: str, path: str, ref: str | None = None) -> str | None:
        """Fetch file contents from a repository at a given ref (branch or commit).

        Returns the decoded file content as text or None if not found.
        Raises GitHubApiError on network/auth errors.
        """
        params = {}
        if ref:
            params["ref"] = ref
        try:
            response = self._get(f"/repos/{repo}/contents/{path}", params=params)
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

    def get_issue_comments(self, repo: str, issue_number: int) -> list[dict]:
        """Fetch issue comments for a PR (issue_number = PR number).

        Returns list of comment objects with keys: id, body, user.login, created_at.
        """
        response = self._get(
            f"/repos/{repo}/issues/{issue_number}/comments",
            params={"per_page": 50, "sort": "created", "direction": "desc"},
        )
        data = response.json()
        return data if isinstance(data, list) else []

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()