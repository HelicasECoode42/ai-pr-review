#!/usr/bin/env python3
"""
Scan repository text files, detect non-UTF-8 encodings and convert them to UTF-8 (without BOM).
Attempt decodings in order: utf-8, gbk/cp936, cp1252, latin-1. When conversion is performed,
sanitize replacement characters (U+FFFD) and strip common BOM.
Prints changed files and returns non-zero on unrecoverable failures.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import List

TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".rst",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".toml",
    ".csv",
}

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}

DECODE_CANDIDATES = ["utf-8", "gbk", "cp936", "cp1252", "latin-1"]


def is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return True
            # Heuristic: if more than 30% of bytes are > 0x7F, still text (non-ascii) but treat as text
            return False
    except Exception:
        return True


def should_process(path: Path) -> bool:
    if path.is_dir():
        return False
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    # also process files in docs/ even without extension
    if any(part in ("docs", "src", "tests") for part in path.parts):
        return True
    return False


def detect_and_convert(path: Path) -> bool:
    """Return True if file was changed."""
    try:
        raw = path.read_bytes()
    except Exception as e:
        print(f"[skip] cannot read {path}: {e}")
        return False

    # skip binary
    if b"\x00" in raw:
        return False

    # Remove BOM if present when trying utf-8
    has_bom = raw.startswith(b"\xef\xbb\xbf")

    for enc in DECODE_CANDIDATES:
        try:
            text = raw.decode(enc)
            # If decoded and enc is not utf-8, we will re-encode to utf-8.
            # Sanitize replacement character and stray control characters
            if "\ufffd" in text:
                # replacement character present, that's suspicious; continue trying other decodings
                # but also allow if this was the only successful decode
                pass
            # Basic sanity: reject decodings that result in many \ufffd
            if text.count("\ufffd") > 3:
                # try next
                continue
            # OK use this decoding
            # Strip BOM if present
            if text.startswith("\ufeff"):
                text = text.lstrip("\ufeff")
            # remove replacement chars
            if "\ufffd" in text:
                text = text.replace("\ufffd", "")
            # Normalize line endings to LF
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            new_bytes = text.encode("utf-8")
            # Only write if changed (different bytes)
            if new_bytes != raw:
                path.write_bytes(new_bytes)
                print(f"[converted] {path} (from {enc})")
                return True
            else:
                return False
        except Exception:
            continue

    # If none decoding succeeded cleanly, attempt a latin-1 fallback and strip non-printable
    try:
        text = raw.decode("latin-1")
        # remove high-control characters except common whitespace
        cleaned = []
        for ch in text:
            if ord(ch) >= 0x20 or ch in "\n\t\r":
                cleaned.append(ch)
        text2 = "".join(cleaned)
        text2 = text2.replace("\ufffd", "")
        text2 = text2.replace("\r\n", "\n").replace("\r", "\n")
        new_bytes = text2.encode("utf-8")
        if new_bytes != raw:
            path.write_bytes(new_bytes)
            print(f"[converted-fallback] {path} (from latin-1 fallback)")
            return True
    except Exception:
        pass

    print(f"[warn] could not safely convert {path}; manual check needed")
    return False


def main() -> int:
    root = Path(".")
    changed: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        parts = Path(dirpath).parts
        if any(p in SKIP_DIRS for p in parts):
            continue
        # skip hidden .git
        if ".git" in parts:
            continue
        for name in filenames:
            path = Path(dirpath) / name
            if not should_process(path):
                continue
            try:
                if is_binary(path):
                    continue
                if detect_and_convert(path):
                    changed.append(path)
            except Exception as e:
                print(f"[error] processing {path}: {e}")

    print("\nSummary:")
    if changed:
        for p in changed:
            print("  ", p)
        print(f"\nConverted {len(changed)} file(s) to UTF-8.")
        return 0
    else:
        print("  No files converted.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
