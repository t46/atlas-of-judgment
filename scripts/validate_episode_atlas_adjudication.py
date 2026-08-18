"""Validate the selected-Deep global Atlas adjudication."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-deep-63/pattern-challenges")
DISPOSITIONS = {"stable", "targeted_revision", "major_reclassification", "underdetermined"}
DECISIONS = {"retain", "revise", "split_pending", "merge_pending", "retire_pending", "underdetermined"}
CONFIDENCES = {"low", "medium", "high"}
DEEP_FIELDS = {
    "focal_factors", "standards", "comparisons", "assumptions",
    "alternative_explanations", "counterfactuals", "inference_steps",
    "expected_information_gain", "repair_conditions", "missingness",
}
GENERIC_BOUNDARY_PHRASES = (
    "applies its own operative standard to its focal inference",
    "applies a different operative standard",
    "similar request surface or result is not sufficient to merge them",
)
NAME_STOPWORDS = {"and", "by", "for", "of", "the", "to", "under", "with"}


def _text(value: Any, minimum: int = 30) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def _name_tokens(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in NAME_STOPWORDS and len(token) > 2
    }


def validate(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "atlas-adjudication-manifest.json").read_text(encoding="utf-8"))
    pattern_ids = set(manifest["pattern_ids"])
    candidates = {key: set(value) for key, value in manifest["candidate_episode_ids_by_pattern"].items()}
    challenge_names = {}
    for pattern_id in pattern_ids:
        challenge_path = directory / f"pattern-challenge-{pattern_id}.json"
        if challenge_path.exists():
            challenge = json.loads(challenge_path.read_text(encoding="utf-8"))
            challenge_names[pattern_id] = challenge.get("pattern_assessment", {}).get(
                "recommended_name", ""
            )
    json_path = directory / "atlas-adjudication.json"
    report_path = directory / "atlas-adjudication-report.md"
    errors: list[str] = []
    if not json_path.exists():
        return {"error_count": 1, "errors": [f"missing {json_path}"]}
    report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    if not report_path.exists() or len(report_text.strip()) < 1500:
        errors.append(f"missing or too-short report: {report_path}")
    if sum(line.startswith("# ") for line in report_text.splitlines()) != 1:
        errors.append("report must contain exactly one H1 title")
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"error_count": 1, "errors": [f"invalid JSON: {exc}"]}
    if not isinstance(payload, dict):
        return {
            "error_count": 1,
            "errors": ["adjudication JSON must be a top-level object"],
        }
    required = {"version", "atlas_disposition", "global_findings", "pattern_decisions", "cross_pattern_boundaries", "limits", "recommended_next_step"}
    if set(payload) != required:
        errors.append(f"top-level keys differ: {sorted(set(payload))}")
    if payload.get("version") != 1:
        errors.append("version must be 1")
    if payload.get("atlas_disposition") not in DISPOSITIONS:
        errors.append("invalid atlas_disposition")
    for key in ("global_findings", "limits"):
        values = payload.get(key)
        if not isinstance(values, list) or not values or not all(_text(value, 20) for value in values):
            errors.append(f"{key} must be a nonempty concrete string list")
    if not _text(payload.get("recommended_next_step"), 50):
        errors.append("recommended_next_step is too short")
    decisions = payload.get("pattern_decisions", [])
    decision_ids = [row.get("pattern_id") for row in decisions]
    if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != pattern_ids:
        errors.append("pattern_decisions must cover ten patterns exactly")
    rationales: dict[str, list[str]] = defaultdict(list)
    for row in decisions:
        pattern_id = row.get("pattern_id")
        prefix = f"{pattern_id}:"
        required_decision = {
            "pattern_id", "decision", "recommended_name", "clarified_core_logic",
            "inclusion_rule", "exclusion_rule", "accepted_candidate_episode_ids",
            "disputed_candidate_episode_ids", "removed_candidate_episode_ids",
            "strongest_supporting_episode_id", "decisive_boundary_episode_id",
            "merge_with", "split_axes", "rationale", "confidence", "full_corpus_action",
        }
        if set(row) != required_decision:
            errors.append(f"{prefix} keys differ")
        if row.get("decision") not in DECISIONS:
            errors.append(f"{prefix} invalid decision")
        if row.get("confidence") not in CONFIDENCES:
            errors.append(f"{prefix} invalid confidence")
        for key in ("recommended_name", "clarified_core_logic", "inclusion_rule", "exclusion_rule", "rationale", "full_corpus_action"):
            if not _text(row.get(key), 30):
                errors.append(f"{prefix} {key} too short")
        if pattern_id in challenge_names and not (
            _name_tokens(row.get("recommended_name"))
            & _name_tokens(challenge_names[pattern_id])
        ):
            errors.append(
                f"{prefix} recommended_name has drifted from its independent "
                "pattern challenge"
            )
        rationale = row.get("rationale", "")
        rationales[re.sub(r"\s+", " ", rationale).strip().casefold()].append(pattern_id)
        sets = []
        for key in ("accepted_candidate_episode_ids", "disputed_candidate_episode_ids", "removed_candidate_episode_ids"):
            values = row.get(key)
            if not isinstance(values, list) or len(values) != len(set(values)):
                errors.append(f"{prefix} invalid {key}")
                values = []
            sets.append(set(values))
        if pattern_id in candidates:
            if (sets[0] | sets[1] | sets[2]) != candidates[pattern_id]:
                errors.append(f"{prefix} candidate partition is incomplete or has extras")
            if (sets[0] & sets[1]) or (sets[0] & sets[2]) or (sets[1] & sets[2]):
                errors.append(f"{prefix} candidate partition overlaps")
        strongest = row.get("strongest_supporting_episode_id")
        if strongest not in sets[0]:
            errors.append(
                f"{prefix} strongest_supporting_episode_id must be accepted"
            )
        decisive_boundary = row.get("decisive_boundary_episode_id")
        if decisive_boundary not in (sets[1] | sets[2]):
            errors.append(
                f"{prefix} decisive_boundary_episode_id must be disputed or removed"
            )
        merge_with = row.get("merge_with")
        if not isinstance(merge_with, list) or set(merge_with) - (pattern_ids - {pattern_id}):
            errors.append(f"{prefix} invalid merge_with")
        split_axes = row.get("split_axes")
        if not isinstance(split_axes, list) or not all(_text(value, 25) for value in split_axes):
            errors.append(f"{prefix} invalid split_axes")
        if row.get("decision") == "split_pending" and len(split_axes) < 2:
            errors.append(f"{prefix} split_pending requires two split_axes")
        if row.get("decision") == "merge_pending" and not merge_with:
            errors.append(f"{prefix} merge_pending requires merge_with")

    for rationale, owners in rationales.items():
        if rationale and len(owners) >= 3:
            errors.append(f"generic rationale reused across patterns: {sorted(owners)}")

    boundaries = payload.get("cross_pattern_boundaries", [])
    if not 6 <= len(boundaries) <= 10:
        errors.append("cross_pattern_boundaries must contain 6–10 high-value pairs")
    distinctions: dict[str, list[str]] = defaultdict(list)
    distinction_words: list[tuple[str, list[str]]] = []
    for index, row in enumerate(boundaries, 1):
        prefix = f"boundary {index}:"
        if set(row) != {"left_pattern_id", "right_pattern_id", "distinction", "decisive_deep_fields", "episode_ids"}:
            errors.append(f"{prefix} keys differ")
        left, right = row.get("left_pattern_id"), row.get("right_pattern_id")
        if left not in pattern_ids or right not in pattern_ids or left == right:
            errors.append(f"{prefix} invalid pattern pair")
        if not _text(row.get("distinction"), 40):
            errors.append(f"{prefix} distinction too short")
        distinction = re.sub(r"\s+", " ", row.get("distinction", "")).strip().casefold()
        if len(distinction.split()) > 90:
            errors.append(f"{prefix} distinction exceeds 90 words")
        if any(phrase in distinction for phrase in GENERIC_BOUNDARY_PHRASES):
            errors.append(f"{prefix} distinction uses a generic comparison template")
        distinctions[distinction].append(f"{left}/{right}")
        distinction_words.append((f"{left}/{right}", distinction.split()))
        fields = row.get("decisive_deep_fields")
        if not isinstance(fields, list) or not fields or set(fields) - DEEP_FIELDS:
            errors.append(f"{prefix} invalid decisive_deep_fields")
        episode_ids = row.get("episode_ids")
        allowed = candidates.get(left, set()) | candidates.get(right, set())
        if not isinstance(episode_ids, list) or not episode_ids or set(episode_ids) - allowed:
            errors.append(f"{prefix} invalid episode_ids")
    for distinction, owners in distinctions.items():
        if distinction and len(owners) >= 3:
            errors.append(f"generic boundary distinction reused: {sorted(owners)}")
    ngram_owners: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for owner, words in distinction_words:
        for index in range(max(0, len(words) - 7)):
            ngram_owners[tuple(words[index : index + 8])].add(owner)
    repeated_frames = [
        (ngram, owners)
        for ngram, owners in ngram_owners.items()
        if len(owners) >= 4
    ]
    if repeated_frames:
        ngram, owners = max(repeated_frames, key=lambda item: len(item[1]))
        errors.append(
            "boundary distinctions reuse an eight-word sentence frame across "
            f"{len(owners)} pairs: {' '.join(ngram)!r}"
        )
    return {
        "atlas_disposition": payload.get("atlas_disposition"),
        "pattern_count": len(decisions),
        "decision_counts": {decision: sum(row.get("decision") == decision for row in decisions) for decision in sorted(DECISIONS)},
        "boundary_count": len(boundaries),
        "error_count": len(errors),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    result = validate(args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["error_count"] else 0)


if __name__ == "__main__":
    main()
