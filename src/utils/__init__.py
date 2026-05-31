def detect_programming_language(filename: str) -> str:
    """Detect programming language from file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    _LANG_MAP = {
        "py": "python",
        "pyw": "python",
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "mts": "typescript",
        "cts": "typescript",
    }
    return _LANG_MAP.get(ext, "other")
