from __future__ import annotations

import httpx
from typing import Optional

from src.models import ChangedFile, FileStatus, PullRequest


class GitHubApiError(Exception):
    """Raised when the GitHub API returns an error or is unreachable."""
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
            print("[WARN] GitHub token 未提供，API 请求可能会因权限不足而失败")
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, path: str, **kwargs: object) -> Optional[httpx.Response]:
        """发送请求，异常时返回 None 并打印错误，不抛出异常"""
        try:
            response = self._client.get(path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            print(f"[ERROR] GitHub API 请求超时: {path}")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                print("[ERROR] GitHub API 认证失败：token 无效或已过期")
            elif status == 403:
                print("[ERROR] GitHub API 访问被拒绝：可能是 rate limit 超限或权限不足")
            elif status == 404:
                print(f"[ERROR] 资源不存在: {path}")
            else:
                print(f"[ERROR] GitHub API 返回错误 (HTTP {status}): {path}")
        except httpx.RequestError as exc:
            print(f"[ERROR] 无法连接 GitHub API: {exc}")
        except Exception as e:
            print(f"[ERROR] 未知错误: {e}")
        return None

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        """获取 PR 信息，失败时返回一个空的 PullRequest 对象"""
        response = self._request(f"/repos/{repo}/pulls/{number}")
        if response is None:
            # 降级返回空 PR
            return PullRequest(
                repo=repo,
                number=number,
                title="",
                body=None,
                author=None,
                base_ref=None,
                head_ref=None,
                html_url=None,
            )
        try:
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
        except Exception as e:
            print(f"[ERROR] 解析 PR 信息失败: {e}")
            return PullRequest(
                repo=repo,
                number=number,
                title="",
                body=None,
                author=None,
                base_ref=None,
                head_ref=None,
                html_url=None,
            )

    def get_changed_files(self, repo: str, number: int) -> list[ChangedFile]:
        """获取变更文件列表，失败时返回空列表"""
        files: list[ChangedFile] = []
        page = 1
        while True:
            response = self._request(
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            if response is None:
                # 单页请求失败，停止翻页并返回已获取的文件
                break
            try:
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
                        print(f"[WARN] 解析文件条目失败: {e}")
                        continue
                page += 1
            except Exception as e:
                print(f"[ERROR] 解析文件列表响应失败: {e}")
                break
        return files

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()