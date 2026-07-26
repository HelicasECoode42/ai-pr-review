from __future__ import annotations

from src.analyzer.cross_file import analyze_cross_file_impact
from src.models import ChangedFile, FileStatus


def test_same_function_repeated_in_hunks_is_not_cross_file_refactor() -> None:
    file = ChangedFile(
        filename="src/context_builder.py",
        status=FileStatus.MODIFIED,
        patch="""@@ -1,3 +1,4 @@
def __ast_dummy__(value):
    return value

@@ -20,3 +21,4 @@
def __ast_dummy__(value):
    return value
""",
    )

    findings = analyze_cross_file_impact([file])

    assert not any(f.rule_id == "cross-file-refactor" for f in findings)


def test_many_function_finding_has_a_concrete_file_path() -> None:
    files = [
        ChangedFile(
            filename=f"src/module_{index}.py",
            status=FileStatus.MODIFIED,
            patch=f"""@@ -1,1 +1,2 @@
def function_{index}(value):
    return value
""",
        )
        for index in range(3)
    ]

    findings = analyze_cross_file_impact(files)

    many = [f for f in findings if f.rule_id == "cross-file-many-funcs"]
    assert many
    assert many[0].file_path
