"use strict";

// These values are assigned only by production verification paths. Unknown,
// legacy, and model-claimed source labels fail closed at the publication edge.
const PUBLISHABLE_SOURCES = new Set([
  "ai_behavior_verified",
  "ai_critic_confirmed",
  "rule_confirmed",
  "rule_test_quality",
]);

function validateRunReport(report, { repo, prNumber, headSha }) {
  if (!report || typeof report !== "object") return false;
  if (!report.pr || typeof report.pr !== "object") return false;
  if (String(report.pr.repo || "").toLowerCase() !== repo.toLowerCase()) return false;
  if (Number(report.pr.number) !== Number(prNumber) || report.pr.head_sha !== headSha) return false;
  if (!["success", "degraded"].includes(report.execution_status)) return false;
  if (!["normal", "partial", "fallback"].includes(report.report_confidence)) return false;
  if (!report.metrics || typeof report.metrics !== "object"
      || typeof report.metrics.candidate_ledger_run_id !== "string"
      || report.metrics.candidate_ledger_run_id.length === 0) return false;
  const lifecycle = report.candidate_lifecycle;
  if (!(lifecycle?.schema_version === 1
    && typeof lifecycle.run_id === "string" && lifecycle.run_id.length > 0
    && lifecycle.run_id === report.metrics.candidate_ledger_run_id
    && ["complete_within_scope", "partial", "not_applicable"].includes(lifecycle.inventory_status)
    && lifecycle.suggestion_candidate_ids && typeof lifecycle.suggestion_candidate_ids === "object"
    && !Array.isArray(lifecycle.suggestion_candidate_ids)
    && Array.isArray(lifecycle.candidates)
    && lifecycle.candidate_count === lifecycle.candidates.length
    && Array.isArray(report.suggestions))) return false;
  const candidateIds = lifecycle.candidates.map(candidate =>
    candidate && typeof candidate === "object" ? String(candidate.candidate_id) : "");
  const mappedIds = Object.values(lifecycle.suggestion_candidate_ids).map(String);
  return candidateIds.every(Boolean) && new Set(candidateIds).size === candidateIds.length
    && new Set(mappedIds).size === mappedIds.length;
}

function eligibleSuggestions(report, identity) {
  if (!validateRunReport(report, identity)) return [];
  const lifecycle = report.candidate_lifecycle;
  const candidates = new Map(lifecycle.candidates
    .filter(candidate => candidate && typeof candidate === "object")
    .map(candidate => [String(candidate.candidate_id), candidate]));
  const eligible = [];
  for (const [indexText, candidateId] of Object.entries(lifecycle.suggestion_candidate_ids)) {
    if (!/^\d+$/.test(indexText)) continue;
    const index = Number(indexText);
    const suggestion = report.suggestions[index];
    const candidate = candidates.get(String(candidateId));
    if (!suggestion || !candidate || candidate.lifecycle_status !== "ready_for_publication"
        || candidate.suggestion_mapping !== "exact"
        || !PUBLISHABLE_SOURCES.has(suggestion.source) || suggestion.line == null) continue;
    eligible.push({ index, candidateId: String(candidateId), source: suggestion.source });
  }
  return eligible;
}

module.exports = { validateRunReport, eligibleSuggestions };
