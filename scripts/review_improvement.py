"""Local offline tools for the constrained strategy-candidate lifecycle.

All commands except ``replay-live`` are offline and do not load provider settings.
``replay-live`` requires the explicit ``--allow-model-requests`` flag and reads the
configured provider settings only to construct the existing production provider.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.application.strategy_policy import load_active_policy
from src.evaluation.improvement import (
    build_input_manifest,
    evaluate_gate,
    generate_candidate,
    promote_candidate,
    replay_candidate,
    rollback_policy,
)


class FixtureProvider:
    def __init__(self, response: dict, *, request_budget, max_output_tokens):
        self.response = response
        self.usage_records: list[dict] = []
        self._request_budget = request_budget
        self._max_output_tokens = max_output_tokens
        self.http_attempts = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self._request_budget.reserve(system_prompt, user_prompt)
        self.http_attempts = self._request_budget.http_attempts
        self.usage_records.append(
            {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        )
        return json.dumps(self.response)

    def close(self) -> None:
        pass


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("prepared", type=Path)
    manifest.add_argument("output", type=Path)
    manifest.add_argument("--split", choices=("dev", "holdout", "test"), required=True)
    manifest.add_argument("--case-id", action="append")
    candidate = sub.add_parser("candidate")
    candidate.add_argument("--store", type=Path, required=True)
    candidate.add_argument("--manifest", type=Path, required=True)
    candidate.add_argument("--run", type=Path, required=True)
    candidate.add_argument("--diagnoses", type=Path, required=True)
    replay = sub.add_parser("replay-fixture")
    replay.add_argument("--store", type=Path, required=True)
    replay.add_argument("--candidate-id", required=True)
    replay.add_argument("--manifest", type=Path, required=True)
    replay.add_argument("--response", type=Path, required=True)
    replay.add_argument("--protocol", type=Path, required=True)
    replay.add_argument("--holdout-attestation", type=Path)
    live = sub.add_parser("replay-live")
    live.add_argument("--store", type=Path, required=True)
    live.add_argument("--candidate-id", required=True)
    live.add_argument("--manifest", type=Path, required=True)
    live.add_argument("--protocol", type=Path, required=True)
    live.add_argument("--holdout-attestation", type=Path)
    live.add_argument("--allow-model-requests", action="store_true", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--store", type=Path, required=True)
    gate.add_argument("--evaluation-id", required=True)
    gate.add_argument("--adjudication", type=Path, required=True)
    gate.add_argument("--quality", type=Path, required=True)
    gate.add_argument("--coverage", type=Path, required=True)
    gate.add_argument("--costs", type=Path, required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("--store", type=Path, required=True)
    promote.add_argument("--candidate-id", required=True)
    promote.add_argument("--evaluation-id", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--store", type=Path, required=True)
    rollback.add_argument("--version-id", required=True)
    show = sub.add_parser("show")
    show.add_argument("--store", type=Path)
    args = parser.parse_args()

    if args.command == "manifest":
        value = build_input_manifest(args.prepared, split=args.split, case_ids=args.case_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"input_set_sha256": value["input_set_sha256"], "case_count": value["case_count"]}
            )
        )
    elif args.command == "candidate":
        value = generate_candidate(
            args.store,
            manifest_path=args.manifest,
            run_path=args.run,
            diagnoses_path=args.diagnoses,
        )
        print(json.dumps({"candidate_id": value["candidate_id"], "status": value["status"]}))
    elif args.command == "replay-fixture":
        response, protocol = _json(args.response), _json(args.protocol)
        attestation = _json(args.holdout_attestation) if args.holdout_attestation else None
        value = replay_candidate(
            args.store,
            candidate_id=args.candidate_id,
            manifest_path=args.manifest,
            provider_factory=lambda _arm, _case, _policy, **budget: FixtureProvider(
                response, **budget
            ),
            protocol=protocol,
            provider_mode="fixture",
            holdout_attestation=attestation,
        )
        print(
            json.dumps(
                {
                    "evaluation_id": value["evaluation_id"],
                    "status": value["status"],
                    "quality_status": value["quality_status"],
                }
            )
        )
    elif args.command == "replay-live":
        # This is the only CLI path that may construct the existing network provider.
        # It requires an explicit flag and exact config/protocol identity; this branch
        # is intentionally not used by tests or any default command.
        from src.reviewer.provider import OpenAICompatibleProvider
        from src.utils.config import get_settings

        protocol = _json(args.protocol)
        attestation = _json(args.holdout_attestation) if args.holdout_attestation else None
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("Configured model credential is unavailable")
        if settings.review_model != protocol.get("model"):
            raise ValueError("Protocol model does not match configured review model")

        def provider_factory(_arm, _case, _policy, *, request_budget, max_output_tokens):
            return OpenAICompatibleProvider(
                settings.openai_api_key,
                protocol["model"],
                settings.openai_base_url,
                timeout=float(protocol["timeout_seconds_per_arm"]),
                total_timeout=float(protocol["timeout_seconds_per_arm"]),
                max_output_tokens=max_output_tokens,
                request_budget=request_budget,
            )

        value = replay_candidate(
            args.store,
            candidate_id=args.candidate_id,
            manifest_path=args.manifest,
            provider_factory=provider_factory,
            protocol=protocol,
            provider_mode="live",
            holdout_attestation=attestation,
        )
        print(
            json.dumps(
                {
                    "evaluation_id": value["evaluation_id"],
                    "status": value["status"],
                    "quality_status": value["quality_status"],
                }
            )
        )
    elif args.command == "gate":
        value = evaluate_gate(
            args.store,
            evaluation_id=args.evaluation_id,
            adjudication=_json(args.adjudication),
            quality=_json(args.quality),
            coverage=_json(args.coverage),
            costs=_json(args.costs),
        )
        print(json.dumps({"decision": value["decision"], "reasons": value["reasons"]}))
    elif args.command == "promote":
        print(promote_candidate(args.store, args.candidate_id, args.evaluation_id))
    elif args.command == "rollback":
        loaded = rollback_policy(args.store, args.version_id)
        print(json.dumps({"policy_id": loaded.policy_id, "status": loaded.status}))
    else:
        loaded = load_active_policy(args.store)
        print(
            json.dumps(
                {"policy_id": loaded.policy_id, "status": loaded.status, "reason": loaded.reason}
            )
        )


if __name__ == "__main__":
    main()
