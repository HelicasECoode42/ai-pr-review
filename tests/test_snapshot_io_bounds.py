from __future__ import annotations

import base64
import json
import subprocess
import time

import httpx
import pytest

from src.analyzer.git_loader import GitPlanError, load_frozen_plan_inputs
from src.analyzer.repository_context import (
    GIT_SEARCH_MAX_BYTES,
    SOURCE_MAX_BYTES,
    EvidenceBundle,
    GitHubSnapshot,
    GitSnapshot,
    _run_bounded_command,
)
from src.github.client import GitHubClient, GitHubResponseTimeout, GitHubResponseTooLarge


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def init_repo(root, files):
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


def test_git_snapshot_read_stops_at_source_byte_limit(tmp_path):
    head = init_repo(tmp_path, {"large.py": "value = '" + ("x" * 900_000) + "'\n"})
    snapshot = GitSnapshot(tmp_path, head, head)

    raw, limited = _run_bounded_command(
        ["git", "-C", str(tmp_path), "show", f"{head}:large.py"],
        max_bytes=4096,
        timeout=5,
    )
    assert len(raw) == 4096
    assert limited is True

    assert snapshot.read("large.py") is None
    assert snapshot.audit()["errors"] == [{
        "reason": "source_too_large", "path": "large.py", "side": "head",
    }]
    assert SOURCE_MAX_BYTES == 500_000


def test_git_search_caps_large_grep_output_and_evidence_is_unavailable(tmp_path):
    head = init_repo(tmp_path, {"huge.py": "needle " + ("x" * 1_200_000) + "\n"})
    snapshot = GitSnapshot(tmp_path, head, head)

    result = snapshot.search("needle", limit=4)

    assert result == [("huge.py", 1)]
    audit = snapshot.audit()
    assert audit["search_limited"] is True
    assert any(item["reason"] == "search_output_limited" for item in audit["errors"])
    assert GIT_SEARCH_MAX_BYTES == 1_000_000

    bundle = EvidenceBundle(snapshot)
    outcome = bundle.execute([{"tool": "search", "symbol": "needle"}])[0]
    assert outcome["status"] == "search_unavailable"
    assert outcome["omission_reason"] == "search_output_limited"
    assert bundle.omitted[-1]["reason"] == "search_output_limited"


class CountingStream(httpx.SyncByteStream):
    def __init__(self, body: bytes, chunk_size: int = 8192):
        self.body = body
        self.chunk_size = chunk_size
        self.consumed = 0

    def __iter__(self):
        for offset in range(0, len(self.body), self.chunk_size):
            chunk = self.body[offset:offset + self.chunk_size]
            self.consumed += len(chunk)
            yield chunk


class SlowCountingStream(CountingStream):
    def __iter__(self):
        for offset in range(0, len(self.body), self.chunk_size):
            if offset:
                time.sleep(0.03)
            chunk = self.body[offset:offset + self.chunk_size]
            self.consumed += len(chunk)
            yield chunk


def test_github_contents_stream_stops_before_reading_full_response():
    body = json.dumps({
        "encoding": "base64",
        "content": base64.b64encode(b"x" * 1_500_000).decode("ascii"),
    }).encode("utf-8")
    stream = CountingStream(body)

    def handler(request):
        return httpx.Response(200, request=request, stream=stream)

    client = GitHubClient(None, timeout=2.0)
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(GitHubResponseTooLarge):
            client.get_file_contents("owner/repo", "large.py", "a" * 40)
    finally:
        client.close()

    assert stream.consumed < len(body)
    assert stream.consumed <= 800_000 + 8192


def test_github_tree_stream_stops_at_total_body_deadline():
    body = b'{"tree":[' + (b" " * 40_000) + b"]}"
    stream = SlowCountingStream(body, chunk_size=8192)

    def handler(request):
        return httpx.Response(200, request=request, stream=stream)

    client = GitHubClient(None, timeout=2.0)
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(GitHubResponseTimeout):
            client._get_json_limited(
                "/repos/owner/repo/git/trees/head",
                max_response_bytes=2_000_000,
                deadline_seconds=0.02,
            )
    finally:
        client.close()

    assert stream.consumed < len(body)


def test_github_deadline_checks_small_drips_without_waiting_for_a_full_chunk(monkeypatch):
    from src.github import client as client_module

    clock = [0.0]

    class DripStream(httpx.SyncByteStream):
        consumed = 0

        def __iter__(self):
            for byte in b'{"ok":true}':
                clock[0] += 0.01
                self.consumed += 1
                yield bytes([byte])

    stream = DripStream()
    client = GitHubClient(None, timeout=2.0)
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)),
    )
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    try:
        with pytest.raises(GitHubResponseTimeout):
            client._get_json_limited("/fixture", max_response_bytes=100, deadline_seconds=0.02)
    finally:
        client.close()

    assert stream.consumed < len(b'{"ok":true}')
    assert clock[0] < 0.05


def test_github_snapshot_keeps_base_and_head_tree_and_read_caches_separate():
    class FrozenClient:
        def __init__(self):
            self.trees = []
            self.reads = []

        def get_json_limited(self, path, *, params, max_response_bytes):
            self.trees.append(path)
            name = (
                "base_only.py"
                if path.endswith("/" + "a" * 40)
                else "head_only.py"
            )
            return {"tree": [{"type": "blob", "path": name}], "truncated": False}

        def get_file_contents(self, repo, path, ref):
            self.reads.append((path, ref))
            return f"def find_me_{'base' if ref == 'a' * 40 else 'head'}():\n    pass\n"

    client = FrozenClient()
    snapshot = GitHubSnapshot(client, "owner/repo", "a" * 40, "b" * 40, [])

    assert snapshot.search("find_me_base", side="base") == [("base_only.py", 1)]
    assert snapshot.search("find_me_head", side="head") == [("head_only.py", 1)]
    assert snapshot.read("shared.py", side="base") != snapshot.read("shared.py", side="head")
    assert client.trees == [
        f"/repos/owner/repo/git/trees/{'a' * 40}",
        f"/repos/owner/repo/git/trees/{'b' * 40}",
    ]
    assert ("shared.py", "a" * 40) in client.reads
    assert ("shared.py", "b" * 40) in client.reads


def test_git_loader_fails_closed_on_oversized_changed_patch(tmp_path):
    base = init_repo(tmp_path, {"large.py": "before = 1\n"})
    (tmp_path / "large.py").write_text("value = '" + ("x" * 2_100_000) + "'\n")
    git(tmp_path, "add", "large.py")
    git(tmp_path, "commit", "-qm", "large change")
    head = git(tmp_path, "rev-parse", "HEAD")

    with pytest.raises(GitPlanError, match="output byte limit"):
        load_frozen_plan_inputs(tmp_path, base, head)


def test_git_loader_passes_remaining_total_budget_to_each_patch(tmp_path, monkeypatch):
    from src.analyzer import git_loader

    base = init_repo(tmp_path, {f"file_{index}.py": "before = 1\n" for index in range(3)})
    for index in range(3):
        (tmp_path / f"file_{index}.py").write_text("value = '" + "x" * 300 + "'\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "several changes")
    head = git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(git_loader, "_PLAN_PATCH_MAX_BYTES", 1000)
    monkeypatch.setattr(git_loader, "_PLAN_PATCH_TOTAL_MAX_BYTES", 700)
    original = git_loader._run_bounded_command
    patch_bytes = []

    def counted(command, **limits):
        result = original(command, **limits)
        if "--no-ext-diff" in command:
            patch_bytes.append(len(result[0]))
        return result

    monkeypatch.setattr(git_loader, "_run_bounded_command", counted)
    with pytest.raises(GitPlanError):
        load_frozen_plan_inputs(tmp_path, base, head)

    assert len(patch_bytes) > 1
    assert sum(patch_bytes) <= 700


@pytest.mark.parametrize("response", [{"truncated": False}, {"tree": None}, {"tree": {}}])
def test_malformed_tree_is_unavailable_not_a_successful_empty_search(response):
    class Client:
        def get_json_limited(self, *args, **kwargs):
            return response

    snapshot = GitHubSnapshot(Client(), "owner/repo", "a" * 40, "b" * 40, [])
    bundle = EvidenceBundle(snapshot)
    outcome = bundle.execute([{"tool": "search", "symbol": "caller"}])[0]
    assert snapshot.audit()["search_limited"] is True
    assert snapshot.audit()["errors"][0]["reason"] == "tree_unavailable"
    assert outcome["status"] == "search_unavailable"
