from tools.generate_functions_index import extract_functions, generate_index


def test_sync_and_async_functions_share_metadata_extraction(tmp_path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    filepath = src_dir / "sample.py"
    filepath.write_text(
        '''\
def sync_call(value: int) -> str:
    """Return the shared result."""
    return str(value)

async def async_call(value: int) -> str:
    """Return the shared result."""
    return str(value)

class Marker:
    """Keep class indexing intact."""
''',
        encoding="utf-8",
    )

    entries = extract_functions(str(filepath))
    sync_entry = next(entry for entry in entries if entry["name"] == "sync_call")
    async_entry = next(entry for entry in entries if entry["name"] == "async_call")

    shared_fields = ("file_path", "args", "returns", "docstring")
    assert tuple(sync_entry[field] for field in shared_fields) == tuple(
        async_entry[field] for field in shared_fields
    )
    assert sync_entry["kind"] == "function"
    assert async_entry["kind"] == "async_function"
    assert sync_entry["line"] != async_entry["line"]

    markdown = generate_index(str(src_dir))
    assert "- **def** `sync_call(value)` → `str`" in markdown
    assert "- **async def** `async_call(value)` → `str`" in markdown
    assert "- **class** `Marker`" in markdown
