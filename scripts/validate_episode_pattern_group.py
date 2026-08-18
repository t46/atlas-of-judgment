"""Validate one or all fresh-agent group pattern synthesis outputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")
PATTERN_ID_RE = re.compile(r"^G(?P<group>\d{2})-P\d{2}$")
REQUIRED_PATTERN_KEYS = {
    "pattern_id",
    "provisional_name",
    "logic",
    "inclusion_rule",
    "exclusion_rule",
    "member_episode_ids",
    "representative_episode_ids",
    "boundary_episode_ids",
    "counterexample_episode_ids",
    "confusable_with",
    "confidence",
    "notes",
}
REQUIRED_LOGIC_KEYS = {
    "inspected_object_types",
    "observation_forms",
    "evaluative_standard",
    "reasoning_template",
    "judgment_forms",
    "request_roles",
}
LIST_LOGIC_KEYS = {
    "inspected_object_types",
    "observation_forms",
    "judgment_forms",
    "request_roles",
}
BANNED_UMBRELLA_NAMES = (
    "umbrella",
    "coverage scaffold",
    "claim-to-evidence alignment",
    "claim-to-evidence calibration",
    "evidence-bounded evaluation of a claimed contribution",
    "conditional evidence-chain sufficiency",
)
MIN_PATTERNS = 6
MAX_PATTERNS = 12
MAX_PATTERN_SHARE = 0.65
MAX_OUTLIER_SHARE = 0.20
MAX_UNASSIGNED_SHARE = 0.10
MAX_PAIRWISE_JACCARD = 0.85


def validate_group(directory: Path, group: int) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    group_row = next(row for row in manifest["groups"] if row["group"] == group)
    allowed = set(group_row["episode_ids"])
    pattern_path = directory / f"group-patterns-{group:02d}.json"
    report_path = directory / f"group-report-{group:02d}.md"
    errors: list[str] = []
    if not pattern_path.exists():
        return {"group": group, "errors": [f"missing {pattern_path}"], "error_count": 1}
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 200:
        errors.append(f"missing or too-short report: {report_path}")

    payload = json.loads(pattern_path.read_text(encoding="utf-8"))
    for key in ("group", "patterns", "unassigned_episode_ids", "outlier_episode_ids", "coverage_notes"):
        if key not in payload:
            errors.append(f"missing top-level key: {key}")
    if payload.get("group") != group:
        errors.append(f"group mismatch: {payload.get('group')} != {group}")

    patterns = payload.get("patterns", [])
    if not MIN_PATTERNS <= len(patterns) <= MAX_PATTERNS:
        errors.append(
            f"pattern count must be {MIN_PATTERNS}-{MAX_PATTERNS}: {len(patterns)}"
        )
    pattern_ids = [pattern.get("pattern_id") for pattern in patterns]
    if len(pattern_ids) != len(set(pattern_ids)):
        errors.append("duplicate pattern IDs")
    member_union: set[str] = set()
    member_sets: dict[str, set[str]] = {}
    standards: dict[str, str] = {}
    reasoning_templates: dict[str, str] = {}
    review_by_episode = {
        episode["episode_id"]: episode["review_id"]
        for episode in _source_episodes(directory, group)
    }
    for index, pattern in enumerate(patterns, 1):
        prefix = f"pattern {index}"
        missing = REQUIRED_PATTERN_KEYS - set(pattern)
        if missing:
            errors.append(f"{prefix}: missing keys {sorted(missing)}")
        pattern_id = pattern.get("pattern_id", "")
        match = PATTERN_ID_RE.match(pattern_id)
        if not match or int(match.group("group")) != group:
            errors.append(f"{prefix}: invalid pattern_id {pattern_id}")
        logic = pattern.get("logic", {})
        logic_missing = REQUIRED_LOGIC_KEYS - set(logic)
        if logic_missing:
            errors.append(f"{prefix}: missing logic keys {sorted(logic_missing)}")
        for key in LIST_LOGIC_KEYS:
            values = logic.get(key)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                errors.append(f"{prefix}: logic.{key} must be a nonempty string list")
        for key, registry in (
            ("evaluative_standard", standards),
            ("reasoning_template", reasoning_templates),
        ):
            value = logic.get(key)
            if not isinstance(value, str) or len(value.strip()) < 30:
                errors.append(f"{prefix}: logic.{key} must be a specific string")
            elif value.casefold() in registry:
                errors.append(
                    f"{prefix}: logic.{key} duplicates {registry[value.casefold()]}"
                )
            else:
                registry[value.casefold()] = pattern_id
        name = pattern.get("provisional_name", "")
        if not isinstance(name, str) or len(name.strip()) < 8:
            errors.append(f"{prefix}: provisional_name is too short")
        elif any(phrase in name.casefold() for phrase in BANNED_UMBRELLA_NAMES):
            errors.append(f"{prefix}: umbrella/generic pattern name: {name}")
        raw_members = pattern.get("member_episode_ids", [])
        members = set(raw_members)
        if len(raw_members) != len(members):
            errors.append(f"{prefix}: duplicate member episode IDs")
        member_union.update(members)
        member_sets[pattern_id] = members
        if unknown := members - allowed:
            errors.append(f"{prefix}: unknown members {sorted(unknown)}")
        member_reviews = {review_by_episode[item] for item in members if item in review_by_episode}
        if len(members) < 3 or len(member_reviews) < 2:
            errors.append(f"{prefix}: recurring pattern needs >=3 episodes and >=2 reviews")
        if len(members) / len(allowed) > MAX_PATTERN_SHARE:
            errors.append(
                f"{prefix}: membership share exceeds {MAX_PATTERN_SHARE:.0%}: "
                f"{len(members)}/{len(allowed)}"
            )
        for key in ("representative_episode_ids", "boundary_episode_ids"):
            values = set(pattern.get(key, []))
            if not values <= members:
                errors.append(f"{prefix}: {key} must be a subset of members")
        representatives = pattern.get("representative_episode_ids", [])
        if not 1 <= len(representatives) <= 5:
            errors.append(f"{prefix}: representative count must be 1-5")
        if len(pattern.get("boundary_episode_ids", [])) > 5:
            errors.append(f"{prefix}: boundary count exceeds 5")
        counterexamples = set(pattern.get("counterexample_episode_ids", []))
        if unknown := counterexamples - allowed:
            errors.append(f"{prefix}: unknown counterexamples {sorted(unknown)}")
        if len(pattern.get("counterexample_episode_ids", [])) > 5:
            errors.append(f"{prefix}: counterexample count exceeds 5")
        if pattern.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"{prefix}: invalid confidence")
        if unknown := set(pattern.get("confusable_with", [])) - set(pattern_ids):
            errors.append(f"{prefix}: unknown confusable pattern IDs {sorted(unknown)}")
        notes = pattern.get("notes")
        if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
            errors.append(f"{prefix}: notes must be a string list")

    ordered_ids = sorted(member_sets)
    for left_index, left_id in enumerate(ordered_ids):
        for right_id in ordered_ids[left_index + 1 :]:
            left = member_sets[left_id]
            right = member_sets[right_id]
            union = left | right
            jaccard = len(left & right) / len(union) if union else 1.0
            if jaccard > MAX_PAIRWISE_JACCARD:
                errors.append(
                    f"patterns {left_id}/{right_id} membership Jaccard "
                    f"{jaccard:.3f} exceeds {MAX_PAIRWISE_JACCARD}"
                )

    unassigned = set(payload.get("unassigned_episode_ids", []))
    outliers = set(payload.get("outlier_episode_ids", []))
    for label, values in (("unassigned", unassigned), ("outliers", outliers)):
        if unknown := values - allowed:
            errors.append(f"unknown {label}: {sorted(unknown)}")
    if overlap := member_union & (unassigned | outliers):
        errors.append(f"member/unassigned-or-outlier overlap: {sorted(overlap)}")
    if overlap := unassigned & outliers:
        errors.append(f"unassigned/outlier overlap: {sorted(overlap)}")
    if len(outliers) / len(allowed) > MAX_OUTLIER_SHARE:
        errors.append(
            f"outlier share exceeds {MAX_OUTLIER_SHARE:.0%}: "
            f"{len(outliers)}/{len(allowed)}"
        )
    if len(unassigned) / len(allowed) > MAX_UNASSIGNED_SHARE:
        errors.append(
            f"unassigned share exceeds {MAX_UNASSIGNED_SHARE:.0%}: "
            f"{len(unassigned)}/{len(allowed)}"
        )
    coverage_notes = payload.get("coverage_notes")
    if not isinstance(coverage_notes, list) or not all(
        isinstance(note, str) for note in coverage_notes
    ):
        errors.append("coverage_notes must be a string list")
    covered = member_union | unassigned | outliers
    if missing := allowed - covered:
        errors.append(f"uncovered source episodes: {sorted(missing)}")

    return {
        "group": group,
        "pattern_count": len(patterns),
        "episode_count": len(allowed),
        "member_episodes": len(member_union),
        "unassigned_episodes": len(unassigned),
        "outlier_episodes": len(outliers),
        "error_count": len(errors),
        "errors": errors,
    }


def _source_episodes(directory: Path, group: int) -> list[dict[str, Any]]:
    source = (directory / f"group-source-{group:02d}.md").read_text(encoding="utf-8")
    block = source.split("```jsonl\n", 1)[1].split("\n```", 1)[0]
    return [json.loads(line) for line in block.splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--group", type=int, action="append")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    groups = args.group or list(range(1, manifest["group_count"] + 1))
    results = [validate_group(args.directory, group) for group in groups]
    output = {
        "groups": results,
        "group_count": len(results),
        "error_count": sum(result["error_count"] for result in results),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
