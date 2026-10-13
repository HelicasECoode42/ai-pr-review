"""Executable checks support labels for the tiny synthetic bootstrap dataset."""

import json
from pathlib import Path

import pytest


def test_synthetic_labels_match_execution():
    cases = json.loads(Path("datasets/golden/synthetic-dev.json").read_text())
    for case in cases:
        lines = case["diff"].splitlines()
        definition = next(line[1:] for line in lines if line.startswith(" def "))
        old = next(line[1:] for line in lines if line.startswith("-    "))
        new = next(line[1:] for line in lines if line.startswith("+    "))
        before, after = {}, {}
        exec(definition + "\n" + old, before)
        exec(definition + "\n" + new, after)
        if "division" in case["case_id"]:
            assert before["average"](3, 0) == 0
            if case["findings"]:
                with pytest.raises(ZeroDivisionError):
                    after["average"](3, 0)
            else:
                for total, count in ((3, 0), (10, 2), (-2, 3)):
                    assert before["average"](total, count) == after["average"](total, count)
        else:
            assert before["lookup"]([10], -1) is None
            if case["findings"]:
                assert after["lookup"]([10], -1) == 10
            else:
                for index in (-2, -1, 0, 1, 2):
                    assert before["lookup"]([10], index) == after["lookup"]([10], index)
