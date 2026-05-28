from __future__ import annotations

from src.models import ReviewReport


def render_json(report: ReviewReport) -> str:
    return report.model_dump_json(indent=2)
