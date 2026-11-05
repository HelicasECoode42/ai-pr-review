"""Regression contracts for the fixed offline complexity measurement."""

from scripts.evaluation.complexity_probe import digest, fixtures, rule_only, run_arm


def test_fixture_identity_is_fixed_and_scales_by_diff_size():
    first, second = fixtures(), fixtures()
    manifest = {name: (item.patch_sha, item.snapshot_sha) for name, item in first.items()}
    assert manifest == {name: (item.patch_sha, item.snapshot_sha) for name, item in second.items()}
    assert [len(first[n].files) for n in ("small", "medium", "large")] == [1, 8, 24]
    assert [first[n].additions for n in ("small", "medium", "large")] == [4, 96, 1560]
    assert len(digest(manifest)) == 64


def test_rule_only_baseline_uses_real_scan_and_report_builder():
    result = rule_only(fixtures()["small"])
    assert result["report_builder"] == "build_rule_only_report"
    assert result["scan"]["risk_and_ast_included"] and result["scan"]["cross_file_included"]
    assert result["findings"] == result["scan"]["signals"]
    assert result["provider_calls"] == 0


def test_grounded_evidence_budget_and_scripted_legacy_hotspot_routes():
    fs = fixtures()
    forced = run_arm(fs["medium"], "current_grounded", "forced_evidence")
    exhausted = run_arm(fs["medium"], "current_grounded", "budget_exhausted", evidence=1)
    legacy = run_arm(fs["large"], "legacy_two_stage", "legacy_hotspots")
    assert forced["calls_by_stage"] == {"discovery": 2, "verification": 1}
    assert forced["evidence_requests"][0]["executed"] is True
    assert exhausted["evidence_omission_reasons"] == ["evidence_budget_exhausted"]
    assert legacy["calls_by_stage"] == {"deep_review": 5, "triage": 1}


def test_critic_and_failed_routes_are_measured_or_reported_as_degraded():
    f = fixtures()["small"]
    critic = run_arm(f, "grounded_plus_critic")
    timeout = run_arm(f, "one_shot_control", "provider_failure")
    assert critic["composition"] == "measured"
    assert critic["configuration_status"] is None
    assert critic["calls_by_stage"] == {
        "critic": 1, "discovery": 1, "verification": 1,
    }
    assert critic["critic_requested"] is True
    assert critic["published"] == 1
    assert timeout["execution_status"] == "degraded"
    assert timeout["call_records"][0]["outcome"] == "scripted_timeout"
