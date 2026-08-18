"""Validate regional Atlas-external logic synthesis."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-reclassification-3135/unmapped-discovery")
REGIONAL_ID = re.compile(r"^R-(?P<region>\d{2})-P\d{2}$")
ATLAS_IDS = {f"A-P{i:02d}" for i in range(1, 11)}
CHAIN_KEYS = {"inspected_object", "observation", "evaluative_standard", "reasoning_bridge", "judgment", "repair_role"}


def validate_region(directory: Path, region: int) -> dict:
    manifest = json.loads((directory / "regional-manifest.json").read_text())
    source = next(row for row in manifest["regions"] if row["region"] == region)
    expected = source["episode_ids"]
    expected_local = source["local_pattern_ids"]
    path = directory / f"regional-patterns-{region:02d}.json"
    report = directory / f"regional-report-{region:02d}.md"
    errors = []
    if not path.exists():
        return {"region": region, "error_count": 1, "errors": ["missing output"]}
    if not report.exists() or len(report.read_text().strip()) < 300:
        errors.append("missing or short report")
    data = json.loads(path.read_text())
    if data.get("region") != region:
        errors.append("region mismatch")
    patterns = data.get("regional_patterns", [])
    ids = [row.get("regional_pattern_id") for row in patterns]
    if len(ids) != len(set(ids)):
        errors.append("duplicate regional IDs")
    members = {}
    for row in patterns:
        pattern_id = row.get("regional_pattern_id", "")
        match = REGIONAL_ID.match(pattern_id)
        if not match or int(match.group("region")) != region:
            errors.append(f"invalid regional ID: {pattern_id}")
        if set(row.get("chain_template", {})) != CHAIN_KEYS:
            errors.append(f"{pattern_id}: invalid chain template")
        for key, value in row.get("chain_template", {}).items():
            if not isinstance(value, str) or len(value.strip()) < 20:
                errors.append(f"{pattern_id}: chain_template.{key} too short")
            if value.strip().casefold() in {
                "the operative evaluation standard",
                "the inference from observation to judgment",
                "the resulting judgment",
            }:
                errors.append(f"{pattern_id}: generic chain_template.{key}")
        episode_ids = set(row.get("member_episode_ids", []))
        members[pattern_id] = episode_ids
        if len(episode_ids) < 3:
            errors.append(f"{pattern_id}: fewer than 3 episodes")
        reviews = {source["review_by_episode"].get(x) for x in episode_ids}
        if len(reviews - {None}) < 2:
            errors.append(f"{pattern_id}: fewer than 2 reviews")
        if not set(row.get("representative_episode_ids", [])) <= episode_ids:
            errors.append(f"{pattern_id}: representative outside members")
        if not set(row.get("supporting_local_pattern_ids", [])) <= set(expected_local):
            errors.append(f"{pattern_id}: unknown local support")
        for contrast in row.get("nearest_atlas_patterns", []):
            if contrast.get("pattern_id") not in ATLAS_IDS:
                errors.append(f"{pattern_id}: invalid Atlas contrast")
    assignments = data.get("episode_assignments", [])
    if [row.get("episode_id") for row in assignments] != expected:
        errors.append("episode assignment order/coverage mismatch")
    assigned = {pattern_id: set() for pattern_id in ids}
    for row in assignments:
        disposition = row.get("disposition")
        pattern_ids = row.get("regional_pattern_ids", [])
        if disposition not in {"regional_pattern", "atlas_boundary", "singleton_new_logic"}:
            errors.append("invalid episode disposition")
        if disposition == "regional_pattern" and not pattern_ids:
            errors.append("regional assignment missing IDs")
        if disposition != "regional_pattern" and pattern_ids:
            errors.append("nonregional assignment has IDs")
        for pattern_id in pattern_ids:
            if pattern_id not in assigned:
                errors.append(f"unknown regional assignment {pattern_id}")
            else:
                assigned[pattern_id].add(row["episode_id"])
        if not set(row.get("nearest_atlas_pattern_ids", [])) <= ATLAS_IDS:
            errors.append("invalid nearest Atlas ID")
    for pattern_id in ids:
        if assigned[pattern_id] != members[pattern_id]:
            errors.append(f"{pattern_id}: member mismatch")
    decisions = data.get("local_pattern_decisions", [])
    if [row.get("local_pattern_id") for row in decisions] != expected_local:
        errors.append("local pattern decision order/coverage mismatch")
    for row in decisions:
        if row.get("decision") not in {"merge", "atlas_boundary", "retire"}:
            errors.append("invalid local decision")
        regional_ids = row.get("regional_pattern_ids", [])
        if row.get("decision") == "merge" and not regional_ids:
            errors.append("merge decision missing regional IDs")
        if row.get("decision") != "merge" and regional_ids:
            errors.append("nonmerge local decision has regional IDs")
        if not set(regional_ids) <= set(ids):
            errors.append("local decision has unknown regional ID")
    return {"region": region, "episode_count": len(expected), "pattern_count": len(patterns), "error_count": len(errors), "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--region", type=int, action="append")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "regional-manifest.json").read_text())
    regions = args.region or [row["region"] for row in manifest["regions"]]
    results = [validate_region(args.directory, region) for region in regions]
    output = {"regions": results, "error_count": sum(row["error_count"] for row in results)}
    print(json.dumps(output, indent=2))
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
