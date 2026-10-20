"""Requested features must not silently disappear on an incompatible route."""

import pytest

from src.models import ChangedFile, PullRequest
from src.reviewer.engine import review_with_ai


@pytest.mark.parametrize("feature", ["enable_critic", "verify_rule_signals"])
def test_unsupported_grounded_feature_stops_before_model_request(feature):
    class NoModelCalls:
        calls = 0

        def complete_json(self, system_prompt, user_prompt):
            self.calls += 1
            raise AssertionError("Unsupported configuration must not spend a model request")

    provider = NoModelCalls()
    pr = PullRequest(repo="fixture/repo", number=1, title="Change contract",
                     base_sha="a" * 40, head_sha="b" * 40)
    report = review_with_ai(
        pr, [ChangedFile(filename="src/value.py", patch="@@ -1 +1 @@\n-a\n+b")],
        [], provider, 10, follow_changes=True, repository_snapshot=object(),
        **{feature: True},
    )

    assert provider.calls == 0
    assert report.used_ai is False
    assert report.execution_status == "degraded"
    assert report.report_confidence == "partial"
    assert report.metrics["configuration_status"] == "unsupported"
    assert "Unsupported grounded review configuration" in report.ai_failure_reason
