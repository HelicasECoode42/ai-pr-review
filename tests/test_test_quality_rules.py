"""Tests for the deterministic test-quality rules."""

from __future__ import annotations

from src.analyzer.test_quality_rules import scan_test_file

FILE = "aafactory/src/tests/test_chat/test_mock_chat.py"


def test_local_import_binding_and_mocked_call_are_reported():
    text = """
from aafactory.chat.interface import _load_avatar_infos_for_chat

def test_avatar(mocker):
    mocker.patch('aafactory.chat.interface._load_avatar_infos_for_chat', return_value=("en", "x"))
    info = _load_avatar_infos_for_chat()
    assert info[0] == "en"
"""
    findings = scan_test_file(FILE, text)
    rules = {item.rule_id for item in findings}
    assert "TQ001" in rules
    assert "TQ002" in rules
    tq001 = next(item for item in findings if item.rule_id == "TQ001")
    assert "_load_avatar_infos_for_chat" in tq001.reason


def test_patched_function_called_directly_is_reported_per_call():
    text = """
def test_chat(mocker):
    mocker.patch('aafactory.chat.interface.send_request_to_open_ai', new_callable=mocker.AsyncMock)
    result = chat_interface.send_request_to_open_ai("hi")
    assert result == "ok"
"""
    calls = [item for item in scan_test_file(FILE, text) if item.rule_id == "TQ002"]
    assert calls
    assert "send_request_to_open_ai" in calls[0].reason


def test_global_clear_without_teardown_is_reported_but_fixture_teardown_is_not():
    without = """
from aafactory.chat.interface import CHAT_HISTORY

def test_chat():
    CHAT_HISTORY.clear()
    CHAT_HISTORY.append("x")
"""
    assert any(item.rule_id == "TQ003" for item in scan_test_file(FILE, without))

    with_teardown = """
import pytest
from aafactory.chat.interface import CHAT_HISTORY

@pytest.fixture
def clean_history():
    CHAT_HISTORY.clear()
    yield
    CHAT_HISTORY.clear()

def test_chat(clean_history):
    CHAT_HISTORY.append("x")
"""
    assert not any(item.rule_id == "TQ003" for item in scan_test_file(FILE, with_teardown))


def test_missing_pytest_dependencies_are_reported_only_when_used():
    text = """
def test_chat(mocker):
    mocker.patch("x.y")
"""
    findings = scan_test_file(FILE, text, project_text="[tool.poetry]\nname='x'\n")
    assert any(item.rule_id == "TQ004" for item in findings)

    declared = scan_test_file(
        FILE, text, project_text="[tool.poetry.group.dev.dependencies]\npytest-mock='*'\n"
    )
    assert not any(item.rule_id == "TQ004" for item in declared)

    unused = scan_test_file(FILE, "def test_x():\n    assert 1 == 1\n", project_text="")
    assert not any(item.rule_id == "TQ004" for item in unused)


def test_syntax_error_returns_no_findings():
    assert scan_test_file(FILE, "def broken(:\n") == []
