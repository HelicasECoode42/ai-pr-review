"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { eligibleSuggestions, validateRunReport } = require("../scripts/publication_gate.js");

const identity = { repo: "owner/repo", prNumber: 7, headSha: "abc" };
function fixture() {
  return {
    pr: { repo: "owner/repo", number: 7, head_sha: "abc" },
    execution_status: "success", report_confidence: "normal",
    suggestions: [
      { line: 4, source: "ai_behavior_verified" },
      { line: 8, source: "rule_unverified" },
    ],
    metrics: { candidate_ledger_run_id: "run-1" },
    candidate_lifecycle: {
      schema_version: 1, inventory_status: "complete_within_scope",
      run_id: "run-1", candidate_count: 2,
      suggestion_candidate_ids: { "0": 1, "1": 2 },
      candidates: [
        { candidate_id: 1, lifecycle_status: "ready_for_publication", suggestion_mapping: "exact" },
        { candidate_id: 2, lifecycle_status: "ready_for_publication", suggestion_mapping: "exact" },
      ],
    },
  };
}

test("only ready mapped suggestions with actual eligible source pass", () => {
  assert.deepEqual(eligibleSuggestions(fixture(), identity), [
    { index: 0, candidateId: "1", source: "ai_behavior_verified" },
  ]);
});

test("the production deterministic test-quality source passes when its candidate is ready and exact", () => {
  const report = fixture();
  report.suggestions.push({
    file_path: "tests/test_quality.py", line: 17, severity: "low", confidence: 0.7,
    title: "[TQ007] Structural rule fact", reason: "Verified deterministic rule fact.",
    recommendation: "Keep the test aligned with the contract.", source: "rule_test_quality",
  });
  report.candidate_lifecycle.candidate_count = 3;
  report.candidate_lifecycle.suggestion_candidate_ids["2"] = 4;
  report.candidate_lifecycle.candidates.push({
    candidate_id: 4,
    candidate_key_sha256: "c011ace7680f9ea3365cfdc19658e71a179c43bb03dee8da711866d7ec89852d",
    file_path: "tests/test_quality.py", line: 17,
    candidate_status: "confirmed", decision: "confirmed",
    lifecycle_status: "ready_for_publication", suggestion_mapping: "exact",
  });
  assert.deepEqual(eligibleSuggestions(report, identity).at(-1), {
    index: 2, candidateId: "4", source: "rule_test_quality",
  });
});

test("unverified, unknown, and fabricated source labels fail closed", () => {
  for (const source of ["ai_critic_unverified", "fake_model_source", "model", "ai"]) {
    const report = fixture();
    report.suggestions[0].source = source;
    assert.deepEqual(eligibleSuggestions(report, identity), [], source);
  }
});

test("missing mappings, wrong identity, or failed lifecycle reject the report", () => {
  const report = fixture();
  delete report.candidate_lifecycle.suggestion_candidate_ids["0"];
  assert.deepEqual(eligibleSuggestions(report, identity), []);
  assert.equal(validateRunReport(fixture(), { ...identity, headSha: "other" }), false);
  const failed = fixture();
  failed.execution_status = "failed";
  assert.equal(validateRunReport(failed, identity), false);
  const staleRun = fixture();
  staleRun.candidate_lifecycle.run_id = "old-run";
  assert.equal(validateRunReport(staleRun, identity), false);
  const missingLedgerRun = fixture();
  delete missingLedgerRun.metrics.candidate_ledger_run_id;
  assert.equal(validateRunReport(missingLedgerRun, identity), false);
  const noSchema = fixture();
  delete noSchema.candidate_lifecycle.schema_version;
  assert.equal(validateRunReport(noSchema, identity), false);
  const duplicateMapping = fixture();
  duplicateMapping.candidate_lifecycle.suggestion_candidate_ids["1"] = 1;
  assert.equal(validateRunReport(duplicateMapping, identity), false);
  assert.deepEqual(eligibleSuggestions(duplicateMapping, identity), []);
});
