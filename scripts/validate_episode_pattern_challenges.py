"""Validate one or all Deep-evidence Atlas pattern challenge outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-deep-63/pattern-challenges")
VERDICTS = {"core", "variant", "boundary_keep", "remove", "counterexample", "insufficient"}
STATUSES = {"stable", "revise", "split_candidate", "merge_candidate", "retire_candidate", "underdetermined"}
CONFIDENCES = {"low", "medium", "high"}
DEEP_FIELDS = {
    "focal_factors", "standards", "comparisons", "assumptions",
    "alternative_explanations", "counterfactuals", "inference_steps",
    "expected_information_gain", "repair_conditions", "missingness",
}


def _string(value: Any, minimum: int = 20) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def validate_pattern(directory: Path, pattern_id: str) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    row = next(item for item in manifest["patterns"] if item["pattern_id"] == pattern_id)
    expected = set(row["candidate_episode_ids"])
    all_patterns = {item["pattern_id"] for item in manifest["patterns"]}
    json_path = directory / f"pattern-challenge-{pattern_id}.json"
    report_path = directory / f"pattern-challenge-{pattern_id}-report.md"
    errors: list[str] = []
    if not json_path.exists():
        return {"pattern_id": pattern_id, "error_count": 1, "errors": [f"missing {json_path}"]}
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 500:
        errors.append(f"missing or too-short report: {report_path}")
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"pattern_id": pattern_id, "error_count": 1, "errors": [f"invalid JSON: {exc}"]}
    if not isinstance(payload, dict):
        return {
            "pattern_id": pattern_id,
            "error_count": 1,
            "errors": ["challenge JSON must be a top-level object"],
        }
    if set(payload) != {"pattern_id", "candidate_episode_ids", "episode_assessments", "pattern_assessment"}:
        errors.append(f"unexpected top-level keys: {sorted(set(payload))}")
    if payload.get("pattern_id") != pattern_id:
        errors.append("pattern_id mismatch")
    listed = payload.get("candidate_episode_ids", [])
    if len(listed) != len(set(listed)) or set(listed) != expected:
        errors.append("candidate_episode_ids differ from manifest")
    assessments = payload.get("episode_assessments", [])
    assessed_ids = [item.get("episode_id") for item in assessments]
    if len(assessed_ids) != len(set(assessed_ids)) or set(assessed_ids) != expected:
        errors.append("episode_assessments must cover every candidate exactly once")
    for index, item in enumerate(assessments, 1):
        prefix = f"episode assessment {index}:"
        if set(item) != {"episode_id", "membership_verdict", "reason", "boundary_with", "decisive_deep_fields", "confidence"}:
            errors.append(f"{prefix} unexpected keys")
        if item.get("membership_verdict") not in VERDICTS:
            errors.append(f"{prefix} invalid membership_verdict")
        if not _string(item.get("reason"), 40):
            errors.append(f"{prefix} reason is too short")
        boundary = item.get("boundary_with")
        if not isinstance(boundary, list) or set(boundary) - (all_patterns - {pattern_id}):
            errors.append(f"{prefix} invalid boundary_with")
        fields = item.get("decisive_deep_fields")
        if not isinstance(fields, list) or not fields or set(fields) - DEEP_FIELDS:
            errors.append(f"{prefix} invalid decisive_deep_fields")
        if item.get("confidence") not in CONFIDENCES:
            errors.append(f"{prefix} invalid confidence")

    assessment = payload.get("pattern_assessment", {})
    required = {
        "status", "recommended_name", "core_logic", "inclusion_rule",
        "exclusion_rule", "split_proposals", "merge_targets",
        "retained_counterexample_ids", "key_findings", "missingness_limits",
        "full_corpus_implication",
    }
    if set(assessment) != required:
        errors.append(f"pattern_assessment keys differ: {sorted(set(assessment))}")
    if assessment.get("status") not in STATUSES:
        errors.append("invalid pattern status")
    for key in ("recommended_name", "core_logic", "inclusion_rule", "exclusion_rule", "full_corpus_implication"):
        if not _string(assessment.get(key), 30):
            errors.append(f"pattern_assessment.{key} is too short")
    splits = assessment.get("split_proposals", [])
    if assessment.get("status") == "split_candidate" and len(splits) < 2:
        errors.append("split_candidate requires at least two split proposals")
    for index, split in enumerate(splits, 1):
        if set(split) != {"name", "logic", "episode_ids"}:
            errors.append(f"split {index}: unexpected keys")
        if not _string(split.get("name"), 8) or not _string(split.get("logic"), 30):
            errors.append(f"split {index}: name/logic too short")
        ids = split.get("episode_ids", [])
        if not isinstance(ids, list) or not ids or set(ids) - expected:
            errors.append(f"split {index}: invalid episode_ids")
    merges = assessment.get("merge_targets", [])
    if assessment.get("status") == "merge_candidate" and not merges:
        errors.append("merge_candidate requires merge_targets")
    for index, merge in enumerate(merges, 1):
        if set(merge) != {"pattern_id", "reason", "episode_ids"}:
            errors.append(f"merge {index}: unexpected keys")
        if merge.get("pattern_id") not in all_patterns - {pattern_id}:
            errors.append(f"merge {index}: invalid target")
        if not _string(merge.get("reason"), 30):
            errors.append(f"merge {index}: reason too short")
        ids = merge.get("episode_ids", [])
        if not isinstance(ids, list) or not ids or set(ids) - expected:
            errors.append(f"merge {index}: invalid episode_ids")
    counterexamples = assessment.get("retained_counterexample_ids", [])
    if not isinstance(counterexamples, list) or set(counterexamples) - expected:
        errors.append("invalid retained_counterexample_ids")
    for key in ("key_findings", "missingness_limits"):
        values = assessment.get(key)
        if not isinstance(values, list) or not all(_string(value, 15) for value in values):
            errors.append(f"pattern_assessment.{key} must be a string list")
    return {
        "pattern_id": pattern_id,
        "candidate_count": len(expected),
        "status": assessment.get("status"),
        "verdict_counts": {
            verdict: sum(item.get("membership_verdict") == verdict for item in assessments)
            for verdict in sorted(VERDICTS)
        },
        "error_count": len(errors),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--pattern", action="append")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    patterns = args.pattern or [row["pattern_id"] for row in manifest["patterns"]]
    results = [validate_pattern(args.directory, pattern_id) for pattern_id in patterns]
    output = {
        "patterns": results,
        "pattern_count": len(results),
        "candidate_memberships": sum(row.get("candidate_count", 0) for row in results),
        "error_count": sum(row["error_count"] for row in results),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
