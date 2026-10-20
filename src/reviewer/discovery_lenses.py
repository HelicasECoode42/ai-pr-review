"""Deterministic facts and a checklist prompt for a third raw-discovery sample.

Across six dev cases, the gold findings raw discovery never found fell into a
few kinds the generic prompt does not steer toward: cross-module contracts,
test gaps, runtime-version compatibility, docs that disagree with code,
security of user-supplied input, consistency, and edge inputs. Several need a
fact the diff does not contain (the declared minimum runtime, whether any test
references a changed symbol, who calls it). This module computes those facts
from the frozen snapshot, cheaply and without a model, and renders them for a
"lens" sample whose findings join the same union, dedupe and verification.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath

from src.reviewer.suggestion_filter import DOCUMENTATION_SUFFIXES, parse_file_hunks

LENS_DISCOVERY_SYSTEM = """You are a senior code reviewer doing a focused second pass over a pull request.
A general review already ran; look specifically at the areas below and report defects introduced or
worsened by this PR in them. Return a JSON object only:
{"findings":[{"file":"relative/path","line":123,"title":"short title","reason":"the failure: what goes wrong and how it is triggered","evidence":["exact code line"],"doubts":"what you are unsure about, or empty"}]}
At most 20 findings. `reason` states the defect only; hedges go in `doubts`. Use the listed facts.
1. Runtime compatibility: syntax or APIs newer, or valid values rejected, versus the declared minimum
   runtime/platform versions listed below.
2. Contracts across modules: every caller and consumer of changed functions, fields and responses
   (listed below); error and nil/empty returns turned into success; values computed twice or from
   separate calls that can disagree; parameters or config added but not passed through.
3. Tests: changed behaviour, new limits, error paths and branches with no test (changed symbols with
   no test reference are listed); tests or fixtures that no longer exercise what they claim.
4. Docs: README, changelog, docstrings, comments and the PR description versus what the code does.
5. Security: user-supplied paths, patterns and input used without validation; internals leaked in errors.
6. Consistency: duplicated logic, the same value with different types or units on different paths,
   results mutated in place, unstable keys or identifiers.
7. Edge inputs: zero, negative, partial, missing or one-of-two values; empty and huge inputs.
8. Cost and resources: expensive queries on hot paths, repeated work, threads or handles not released.
A finding must name a changed file and a positive line number in the new version.
"""

_DEFINITION = re.compile(
    r"^\+\s*(?:export\s+)?(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?(?:static\s+)?"
    r"(?:def|func|fun|function|class|interface|struct)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w{2,})"
    r"|^\+\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w{2,})\s*=\s*(?:async\s*)?\(",
    re.MULTILINE,
)
# Manifest lines that declare a minimum runtime or platform, by file name.
_RUNTIME_PATTERNS = (
    (re.compile(r"\.gemspec$"), re.compile(r"required_ruby_version")),
    (re.compile(r"^package\.json$"), re.compile(r'"(node|npm|engines)"')),
    (re.compile(r"^pyproject\.toml$"), re.compile(r"requires-python|python_requires")),
    (re.compile(r"^setup\.(py|cfg)$"), re.compile(r"python_requires")),
    (re.compile(r"^go\.mod$"), re.compile(r"^go\s+\d")),
    (re.compile(r"^Cargo\.toml$"), re.compile(r"rust-version")),
    (re.compile(r"\.gradle(\.kts)?$"), re.compile(r"jvmTarget|minSdk|(source|target)Compatibility")),
    (re.compile(r"^\.(nvmrc|ruby-version|python-version|node-version)$"), re.compile(r".")),
)
MAX_SYMBOLS = 12
MAX_CALLERS_PER_SYMBOL = 3


def _is_test(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return ("test" in PurePosixPath(lowered).parts or "tests" in PurePosixPath(lowered).parts
            or "spec" in PurePosixPath(lowered).parts or "__tests__" in lowered
            or name.startswith("test") or ".test." in name or ".spec." in name
            or name.endswith(("_test.go", "_test.py", "_spec.rb", "test.kt", "test.java", "test.rb")))


def _line(snapshot, path: str, number: int) -> str:
    text = (snapshot.read(path, "head") or "").splitlines()
    return text[number - 1].strip()[:160] if 0 < number <= len(text) else ""


def _top_level_files(snapshot) -> list[str]:
    root, head = getattr(snapshot, "root", None), getattr(snapshot, "head", None)
    if root is None or head is None:
        return []
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "--name-only", head],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [name for name in output.splitlines() if name][:500]


def runtime_constraints(snapshot) -> list[str]:
    """Minimum runtime/platform declarations from top-level manifests.

    Manifests are read directly: repository search skips JSON and build files.
    """
    facts: list[str] = []
    for name in _top_level_files(snapshot):
        for file_pattern, line_pattern in _RUNTIME_PATTERNS:
            if not file_pattern.search(name):
                continue
            text = snapshot.read(name, "head") or ""
            for number, line in enumerate(text.splitlines(), 1):
                if line_pattern.search(line):
                    facts.append(f"{name}:{number}: {line.strip()[:160]}")
            break
    return list(dict.fromkeys(facts))[:10]


def changed_symbols(files) -> list[tuple[str, str]]:
    symbols: list[tuple[str, str]] = []
    for file in files:
        if not file.patch or PurePosixPath(file.filename).suffix.lower() in DOCUMENTATION_SUFFIXES:
            continue
        if _is_test(file.filename):
            continue
        for match in _DEFINITION.finditer(file.patch):
            symbols.append((file.filename, match.group(1) or match.group(2)))
        for hunk in parse_file_hunks(file):
            header = hunk.header.split("@@")[-1]
            found = re.findall(r"(?:def|func|fun|function|class)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w{2,})",
                               header)
            symbols.extend((file.filename, name) for name in found)
    unique = list(dict.fromkeys(symbols))
    return unique[:MAX_SYMBOLS]


def lens_facts(snapshot, files) -> str:
    """Render the facts block appended to the lens sample's prompt."""
    lines = ["## Facts for the focused pass (computed from the frozen snapshot)"]
    runtime = runtime_constraints(snapshot)
    lines.append("### Declared runtime/platform versions")
    lines.extend(f"- {fact}" for fact in runtime) if runtime else lines.append("- none found")
    changed = {file.filename for file in files}
    untested, callers = [], []
    for path, symbol in changed_symbols(files):
        try:
            hits = snapshot.search(symbol, side="head", limit=12)
        except Exception:  # a search backend must not break the review
            continue
        if not any(_is_test(hit_path) for hit_path, _ in hits):
            untested.append(f"{symbol} ({path})")
        outside = [(p, n) for p, n in hits if p != path and not _is_test(p)]
        for hit_path, number in outside[:MAX_CALLERS_PER_SYMBOL]:
            marker = " [changed in this PR]" if hit_path in changed else ""
            callers.append(f"{symbol}: {hit_path}:{number}{marker}: {_line(snapshot, hit_path, number)}")
    lines.append("### Changed symbols with no test reference in the repository")
    lines.extend(f"- {item}" for item in untested) if untested else lines.append("- none")
    lines.append("### Other references to changed symbols (callers and consumers)")
    lines.extend(f"- {item}" for item in callers) if callers else lines.append("- none found")
    docs = [file.filename for file in files
            if PurePosixPath(file.filename).suffix.lower() in DOCUMENTATION_SUFFIXES
            or PurePosixPath(file.filename).name.upper().startswith(("README", "CHANGELOG"))]
    lines.append("### Changed documentation files")
    lines.extend(f"- {path}" for path in docs) if docs else lines.append("- none")
    return "\n".join(lines)
