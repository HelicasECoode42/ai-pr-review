from src.reviewer.candidate_dedupe import dedupe_candidates

F = "tests/test_chat.py"


def item(line, title, rule_id=None, file=F):
    found = {"file": file, "line": line, "title": title}
    if rule_id:
        found["rule_id"] = rule_id
    return found


def test_restated_model_candidates_merge_anywhere_in_the_file():
    kept, merges = dedupe_candidates([
        item(9, "Test mocks the function under test, so it never exercises real code"),
        item(33, "Test mocks the function under test, so it verifies nothing"),
    ])
    assert [found["line"] for found in kept] == [9]
    assert merges[0]["kept_index"] == 0 and merges[0]["merged"]["line"] == 33


def test_distinct_issues_on_the_same_line_are_kept():
    kept, _ = dedupe_candidates([
        item(9, "Test mocks the function under test, so it never exercises real code"),
        item(9, "Mocked video-generation function is never called"),
    ])
    assert len(kept) == 2


def test_different_rules_on_one_line_are_never_merged():
    kept, _ = dedupe_candidates([
        item(14, "[TQ008] Mock side_effect mutates module-global state", "TQ008"),
        item(14, "[TQ006] Mock side_effect return shape does not match", "TQ006"),
    ])
    assert len(kept) == 2


def test_same_rule_on_nearby_lines_merges():
    kept, _ = dedupe_candidates([
        item(36, "[TQ007] magic index", "TQ007"),
        item(37, "[TQ007] magic index", "TQ007"),
        item(60, "[TQ007] magic index", "TQ007"),
    ])
    assert [found["line"] for found in kept] == [36, 60]


def test_rule_merges_with_a_nearby_model_candidate_using_its_vocabulary():
    kept, merges = dedupe_candidates([
        item(6, "Global CHAT_HISTORY cleared only at start, leaking state"),
        item(8, "[TQ003] Module-level global is cleared without teardown", "TQ003"),
        # "mock" alone is not TQ002 vocabulary: an import-binding title stays apart.
        item(28, "Mocked loader is bypassed by direct import"),
        item(33, "[TQ002] Test calls the function it mocked", "TQ002"),
    ])
    assert [found["line"] for found in kept] == [8, 28, 33]
    assert kept[0]["rule_id"] == "TQ003"  # the rule fact represents the group
    assert merges[0]["merged"]["line"] == 6


def test_other_files_never_merge():
    kept, _ = dedupe_candidates([
        item(1, "Same title here", file="a.py"),
        item(1, "Same title here", file="b.py"),
    ])
    assert len(kept) == 2
