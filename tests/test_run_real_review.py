from types import SimpleNamespace

from src.models import PullRequest, ReviewReport, Severity


def test_real_review_runner_uses_application_boundary_and_preserves_shared_config(
    monkeypatch, tmp_path
):
    from scripts import run_real_review

    snapshot = SimpleNamespace(base="a" * 40, head="b" * 40)
    pr_files = []
    report = ReviewReport(
        pr=PullRequest(repo="fixture/repo", number=0, title="test"),
        files=[], summary="ok", risk_level=Severity.LOW,
    )
    observed = {}

    class FakeApplication:
        def __init__(self, settings, *, provider_factory):
            observed["factory"] = provider_factory

        def review(self, pr, files, findings, **options):
            observed["review"] = (pr, files, findings, options)
            return report

    monkeypatch.setattr(run_real_review, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run_real_review, "get_settings", lambda: object())
    monkeypatch.setattr(
        run_real_review, "load_frozen_plan_inputs",
        lambda *args: (snapshot, pr_files, []),
    )
    monkeypatch.setattr(
        run_real_review, "scan_signals",
        lambda files: SimpleNamespace(findings=[], warnings=[]),
    )
    monkeypatch.setattr(run_real_review, "ReviewApplication", FakeApplication)

    assert run_real_review.main([
        "--base", "a" * 40, "--head", "b" * 40, "--out", str(tmp_path / "out.json")
    ]) == 0

    assert callable(observed["factory"])
    _, files, findings, options = observed["review"]
    assert files is pr_files and findings == []
    assert options["follow_changes"] is True
    assert options["repository_snapshot"] is snapshot
    assert options["unit_review_max_units"] == 0
