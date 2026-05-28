from src.analyzer.diff_parser import changed_line_map, parse_file_hunks
from src.models import ChangedFile, FileStatus


def test_parse_file_hunks_maps_added_line_numbers() -> None:
    file = ChangedFile(
        filename="src/app.py",
        status=FileStatus.MODIFIED,
        patch="""@@ -10,3 +10,4 @@ def run():
 context
-old_call()
+new_call()
+another_call()
 tail""",
    )

    hunks = parse_file_hunks(file)

    assert len(hunks) == 1
    assert [line.line for line in hunks[0].added_lines] == [11, 12]
    assert changed_line_map([file]) == {"src/app.py": {11, 12}}
