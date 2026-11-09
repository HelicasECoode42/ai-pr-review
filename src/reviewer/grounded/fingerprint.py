"""The source files whose content defines a grounded review's runtime identity."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2]


def runtime_sha256() -> str:
    """Hash every module that can change a review, so old checkpoints never resume."""
    reviewer = SOURCE_ROOT / "reviewer"
    paths = (
        reviewer / "grounded_review.py",
        *sorted(Path(__file__).parent.glob("*.py")),
        reviewer / "engine.py",
        reviewer / "discovery_lenses.py",
        reviewer / "criteria.py",
        reviewer / "critic.py",
        reviewer / "unit_review.py",
        reviewer / "provider.py",
        SOURCE_ROOT / "analyzer" / "repository_context.py",
        SOURCE_ROOT / "analyzer" / "symbol_index.py",
        SOURCE_ROOT / "application" / "review.py",
        SOURCE_ROOT / "application" / "strategy_policy.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(SOURCE_ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
