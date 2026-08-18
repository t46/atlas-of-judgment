"""Validate local unmapped-logic discovery outputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_DIRECTORY = Path(
    "data/analysis/iclr/episode-reclassification-3135/unmapped-discovery"
)
ID_RE = re.compile(r"^U-(?P<group>\d{2})-P\d{2}$")
DISPOSITIONS = {
    "candidate_pattern",
    "atlas_boundary",
    "source_insufficient",
    "singleton_new_logic",
}
PATTERN_KEYS = {
    "candidate_pattern_id", "provisional_name", "chain_template",
    "inclusion_rule", "exclusion_rule", "member_episode_ids",
    "representative_episode_ids", "boundary_episode_ids",
    "counterexample_episode_ids", "nearest_atlas_patterns", "variants",
    "confidence", "notes",
}
CHAIN_KEYS = {
    "inspected_object", "observation", "evaluative_standard",
    "reasoning_bridge", "judgment", "repair_role",
}
ASSIGNMENT_KEYS = {
    "episode_id", "disposition", "candidate_pattern_ids",
    "nearest_atlas_pattern_ids", "reason", "confidence",
}
ATLAS_IDS = {f"A-P{i:02d}" for i in range(1, 11)}


def validate_group(directory: Path, group: int) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text())
    group_row = next(row for row in manifest["groups"] if row["group"] == group)
    expected = group_row["episode_ids"]
    allowed = set(expected)
    output_path = directory / f"local-patterns-{group:02d}.json"
    report_path = directory / f"local-report-{group:02d}.md"
    errors: list[str] = []
    if not output_path.exists():
        return {"group": group, "error_count": 1, "errors": ["missing output"]}
    if not report_path.exists() or len(report_path.read_text().strip()) < 300:
        errors.append("missing or too-short report")
    payload = json.loads(output_path.read_text())
    for key in ("group", "candidate_patterns", "assignments", "coverage_notes"):
        if key not in payload:
            errors.append(f"missing top-level key: {key}")
    if payload.get("group") != group:
        errors.append("group mismatch")
    patterns = payload.get("candidate_patterns", [])
    ids = [row.get("candidate_pattern_id") for row in patterns]
    if len(ids) != len(set(ids)):
        errors.append("duplicate candidate pattern IDs")
    pattern_members: dict[str, set[str]] = {}
    review_by_episode = _review_by_episode(directory, group)
    for index, row in enumerate(patterns, 1):
        prefix = f"pattern {index}"
        if missing := PATTERN_KEYS - set(row):
            errors.append(f"{prefix}: missing keys {sorted(missing)}")
        pattern_id = row.get("candidate_pattern_id", "")
        match = ID_RE.match(pattern_id)
        if not match or int(match.group("group")) != group:
            errors.append(f"{prefix}: invalid ID {pattern_id}")
        chain = row.get("chain_template", {})
        if set(chain) != CHAIN_KEYS:
            errors.append(f"{prefix}: chain_template must have exactly {sorted(CHAIN_KEYS)}")
        for key in CHAIN_KEYS:
            if not isinstance(chain.get(key), str) or len(chain.get(key, "").strip()) < 12:
                errors.append(f"{prefix}: chain_template.{key} too short")
        members_raw = row.get("member_episode_ids", [])
        members = set(members_raw)
        pattern_members[pattern_id] = members
        if len(members_raw) != len(members):
            errors.append(f"{prefix}: duplicate members")
        if unknown := members - allowed:
            errors.append(f"{prefix}: unknown members {sorted(unknown)}")
        if len(members) < 3 or len({review_by_episode[x] for x in members if x in review_by_episode}) < 2:
            errors.append(f"{prefix}: recurring candidate needs >=3 episodes and >=2 reviews")
        for key in ("representative_episode_ids", "boundary_episode_ids"):
            if not set(row.get(key, [])) <= members:
                errors.append(f"{prefix}: {key} must be member subset")
        if not 1 <= len(row.get("representative_episode_ids", [])) <= 5:
            errors.append(f"{prefix}: representative count must be 1-5")
        if unknown := set(row.get("counterexample_episode_ids", [])) - allowed:
            errors.append(f"{prefix}: unknown counterexamples {sorted(unknown)}")
        nearest = row.get("nearest_atlas_patterns", [])
        for contrast in nearest:
            if contrast.get("pattern_id") not in ATLAS_IDS:
                errors.append(f"{prefix}: invalid nearest Atlas pattern")
            if len(contrast.get("decisive_difference", "").strip()) < 20:
                errors.append(f"{prefix}: nearest Atlas difference too short")
        if row.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"{prefix}: invalid confidence")

    assignments = payload.get("assignments", [])
    assignment_ids = [row.get("episode_id") for row in assignments]
    if assignment_ids != expected:
        errors.append("assignments must exactly match source order and coverage")
    for index, row in enumerate(assignments, 1):
        prefix = f"assignment {index}"
        if set(row) != ASSIGNMENT_KEYS:
            errors.append(f"{prefix}: keys must be exactly {sorted(ASSIGNMENT_KEYS)}")
        disposition = row.get("disposition")
        if disposition not in DISPOSITIONS:
            errors.append(f"{prefix}: invalid disposition")
        candidate_ids = row.get("candidate_pattern_ids", [])
        if unknown := set(candidate_ids) - set(ids):
            errors.append(f"{prefix}: unknown candidate IDs {sorted(unknown)}")
        if disposition == "candidate_pattern" and not candidate_ids:
            errors.append(f"{prefix}: candidate_pattern needs candidate IDs")
        if disposition != "candidate_pattern" and candidate_ids:
            errors.append(f"{prefix}: noncandidate disposition has candidate IDs")
        nearest = set(row.get("nearest_atlas_pattern_ids", []))
        if unknown := nearest - ATLAS_IDS:
            errors.append(f"{prefix}: invalid nearest Atlas IDs {sorted(unknown)}")
        if len(row.get("reason", "").strip()) < 30:
            errors.append(f"{prefix}: reason too short")
        if row.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"{prefix}: invalid confidence")
    assigned_members: dict[str, set[str]] = {pattern_id: set() for pattern_id in ids}
    for row in assignments:
        for pattern_id in row.get("candidate_pattern_ids", []):
            assigned_members[pattern_id].add(row["episode_id"])
    for pattern_id in ids:
        if assigned_members[pattern_id] != pattern_members.get(pattern_id, set()):
            errors.append(f"{pattern_id}: assignment/member mismatch")
    notes = payload.get("coverage_notes")
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        errors.append("coverage_notes must be string list")
    return {
        "group": group,
        "episode_count": len(expected),
        "candidate_pattern_count": len(patterns),
        "disposition_counts": dict(
            sorted(
                __import__("collections").Counter(
                    row.get("disposition") for row in assignments
                ).items()
            )
        ),
        "error_count": len(errors),
        "errors": errors,
    }


def _review_by_episode(directory: Path, group: int) -> dict[str, str]:
    source = (directory / f"source-group-{group:02d}.md").read_text()
    block = source.split("```jsonl\n", 1)[1].split("\n```", 1)[0]
    return {
        row["episode_id"]: row["review_id"]
        for row in (json.loads(line) for line in block.splitlines() if line.strip())
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--group", type=int, action="append")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text())
    groups = args.group or [row["group"] for row in manifest["groups"]]
    results = [validate_group(args.directory, group) for group in groups]
    output = {"groups": results, "error_count": sum(row["error_count"] for row in results)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
