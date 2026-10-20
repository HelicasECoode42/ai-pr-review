from src.analyzer.test_quality_lane import collect_test_quality_candidates, is_python_test
from src.models import ChangedFile


class _Snapshot:
    def __init__(self, files):
        self.files = files

    def read(self, path, side="head"):
        return self.files.get(path)


TEST = '''import pytest
from pkg.chat.interface import load_info

@pytest.mark.asyncio
async def test_chat(mocker):
    mocker.patch("pkg.chat.interface.send", return_value="x")
    mocker.patch("pkg.chat.interface.load_info", return_value=(1,))
    info = load_info()
    from pkg.chat import interface
    await interface.send(info, 2, 3)
'''


def test_changed_python_tests_become_candidates_with_resolved_sources():
    snapshot = _Snapshot({
        "app/src/tests/test_chat.py": TEST,
        "app/pyproject.toml": "[tool.poetry]\nname = 'x'\n",
        "app/src/pkg/chat/interface.py": (
            "from pkg.fetch import send\n\ndef load_info():\n    return ()\n"
        ),
        "app/src/pkg/fetch.py": "def send(messages):\n    return ''\n",
    })
    files = [ChangedFile(filename="app/src/tests/test_chat.py", patch="@@ -0,0 +1 @@\n+x\n")]
    found = collect_test_quality_candidates(snapshot, files)
    rules = {item["rule_id"] for item in found}
    assert {"TQ001", "TQ004"} <= rules
    # `send` is re-exported by the patched module; one import hop resolves its
    # real one-argument signature, so the three-argument call is reported.
    assert "TQ005" in rules
    by_rule = {item["rule_id"]: item for item in found}
    # The real function behind the patched or mis-called name is located, so
    # review can show what an un-mocked call would actually do.
    assert by_rule["TQ001"]["target"] == ("app/src/pkg/chat/interface.py", 3)
    assert by_rule["TQ005"]["target"] == ("app/src/pkg/fetch.py", 1)
    for item in found:
        assert item["title"].startswith(f"[{item['rule_id']}]")
        assert item["line"] >= 1


def test_only_python_test_files_are_scanned():
    assert is_python_test("a/tests/helpers.py")
    assert is_python_test("a/test_x.py")
    assert not is_python_test("a/src/x.py")
    assert not is_python_test("a/tests/x.ts")
    snapshot = _Snapshot({"a/src/x.py": TEST})
    assert collect_test_quality_candidates(snapshot, [ChangedFile(filename="a/src/x.py")]) == []
