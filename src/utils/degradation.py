"""One place that decides how a degraded step is recorded.

A degradation must be visible in two places at once: the log, for operators,
and the caller's warning list, so it can reach the report the user reads.
Keeping that decision here stops each analyzer from inventing its own.
"""

from __future__ import annotations

import logging


def record_degradation(
    warnings: list[str] | None, message: str, *, logger: logging.Logger
) -> None:
    """Log the message and, when the caller supplied a list, keep it there too."""
    logger.warning(message)
    if warnings is not None:
        warnings.append(message)
