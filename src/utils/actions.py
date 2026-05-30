from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def write_step_summary(text: str) -> None:
    """Write text to GitHub Actions step summary file if available.

    The runner exposes the path in the GITHUB_STEP_SUMMARY environment variable.
    If not running inside Actions, this is a no-op.
    """
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        logger.debug("GITHUB_STEP_SUMMARY not set; skipping step summary write")
        return
    try:
        # append mode: multiple steps may write
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(str(text))
    except Exception as e:
        logger.warning(f"Failed to write GitHub step summary to {path}: {e}")
