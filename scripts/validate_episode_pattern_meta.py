"""Validate cross-group meta-pattern synthesis outputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")
ID_RE = re.compile(r"^M(?P<meta>\d{2})-P\d{2}$")
LIST_LOGIC_KEYS = {"inspected_object_types", "observation_forms", "judgment_forms", "request_roles"}
STRING_LOGIC_KEYS = {"evaluative_standard", "reasoning_template"}
REQUIRED_KEYS = {
    "pattern_id", "provisional_name", "logic", "source_pattern_ids",
    "inclusion_rule", "exclusion_rule", "merge_rationale",
    "representative_episode_ids", "boundary_episode_ids",
    "counterexample_episode_ids", "confusable_with", "confidence", "notes",
}
BANNED_NAMES = ("umbrella", "coverage", "claim-to-evidence", "evidence-chain sufficiency")


def validate_meta(directory: Path, meta_group: int) -> dict[str, Any]:
    manifest = json.loads((directory / "meta-manifest.json").read_text(encoding="utf-8"))
    row = next(item for item in manifest["meta_groups"] if item["meta_group"] == meta_group)
    source_patterns = row["source_patterns"]
    allowed_patterns = set(source_patterns)
    allowed_episodes = set(row["episode_ids"])
    output_path = directory / f"meta-patterns-{meta_group:02d}.json"
    report_path = directory / f"meta-report-{meta_group:02d}.md"
    errors: list[str] = []
    if not output_path.exists():
        return {"meta_group": meta_group, "error_count": 1, "errors": [f"missing {output_path}"]}
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 300:
        errors.append(f"missing or too-short report: {report_path}")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if payload.get("meta_group") != meta_group:
        errors.append("meta_group mismatch")
    patterns = payload.get("patterns", [])
    if not 8 <= len(patterns) <= 14:
        errors.append(f"pattern count must be 8-14: {len(patterns)}")
    pattern_ids = [pattern.get("pattern_id") for pattern in patterns]
    if len(pattern_ids) != len(set(pattern_ids)):
        errors.append("duplicate meta pattern IDs")
    mapped: set[str] = set()
    standards: set[str] = set()
    templates: set[str] = set()
    for index, pattern in enumerate(patterns, 1):
        prefix = f"pattern {index}"
        if missing := REQUIRED_KEYS - set(pattern):
            errors.append(f"{prefix}: missing keys {sorted(missing)}")
        pattern_id = pattern.get("pattern_id", "")
        match = ID_RE.match(pattern_id)
        if not match or int(match.group("meta")) != meta_group:
            errors.append(f"{prefix}: invalid pattern_id {pattern_id}")
        name = pattern.get("provisional_name", "")
        if not isinstance(name, str) or any(word in name.casefold() for word in BANNED_NAMES):
            errors.append(f"{prefix}: generic or invalid name {name}")
        logic = pattern.get("logic", {})
        for key in LIST_LOGIC_KEYS:
            values = logic.get(key)
            if not isinstance(values, list) or not values or not all(isinstance(value, str) and value.strip() for value in values):
                errors.append(f"{prefix}: logic.{key} must be a nonempty string list")
        for key, registry in (("evaluative_standard", standards), ("reasoning_template", templates)):
            value = logic.get(key)
            normalized = value.casefold().strip() if isinstance(value, str) else ""
            if len(normalized) < 30:
                errors.append(f"{prefix}: logic.{key} must be specific")
            elif normalized in registry:
                errors.append(f"{prefix}: duplicate logic.{key}")
            registry.add(normalized)
        sources = pattern.get("source_pattern_ids", [])
        source_set = set(sources)
        mapped.update(source_set)
        if len(sources) != len(source_set):
            errors.append(f"{prefix}: duplicate source_pattern_ids")
        if unknown := source_set - allowed_patterns:
            errors.append(f"{prefix}: unknown source patterns {sorted(unknown)}")
        source_groups = {source_patterns[item]["group"] for item in source_set if item in source_patterns}
        if len(source_set) < 2 or len(source_groups) < 2:
            errors.append(f"{prefix}: must merge >=2 patterns from >=2 groups")
        member_episodes = {
            episode_id
            for source_id in source_set
            if source_id in source_patterns
            for episode_id in source_patterns[source_id]["member_episode_ids"]
        }
        representatives = pattern.get("representative_episode_ids", [])
        if not 1 <= len(representatives) <= 5 or not set(representatives) <= member_episodes:
            errors.append(f"{prefix}: representatives must be 1-5 member episodes")
        boundaries = pattern.get("boundary_episode_ids", [])
        if len(boundaries) > 5 or not set(boundaries) <= member_episodes:
            errors.append(f"{prefix}: boundaries must be <=5 member episodes")
        counterexamples = pattern.get("counterexample_episode_ids", [])
        if len(counterexamples) > 5 or not set(counterexamples) <= allowed_episodes:
            errors.append(f"{prefix}: counterexamples must be <=5 source episodes")
        if unknown := set(pattern.get("confusable_with", [])) - set(pattern_ids):
            errors.append(f"{prefix}: unknown confusable IDs {sorted(unknown)}")
        if pattern.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"{prefix}: invalid confidence")
        if not isinstance(pattern.get("notes"), list):
            errors.append(f"{prefix}: notes must be a list")

    unmerged_raw = payload.get("unmerged_source_pattern_ids", [])
    unmerged = set(unmerged_raw)
    if len(unmerged_raw) != len(unmerged):
        errors.append("duplicate unmerged source pattern IDs")
    if unknown := unmerged - allowed_patterns:
        errors.append(f"unknown unmerged patterns: {sorted(unknown)}")
    if overlap := mapped & unmerged:
        errors.append(f"mapped/unmerged overlap: {sorted(overlap)}")
    if missing := allowed_patterns - mapped - unmerged:
        errors.append(f"uncovered source patterns: {sorted(missing)}")
    if len(unmerged) / len(allowed_patterns) > 0.15:
        errors.append(f"unmerged share exceeds 15%: {len(unmerged)}/{len(allowed_patterns)}")
    if not isinstance(payload.get("coverage_notes"), list):
        errors.append("coverage_notes must be a list")
    return {
        "meta_group": meta_group,
        "pattern_count": len(patterns),
        "source_pattern_count": len(allowed_patterns),
        "mapped_source_patterns": len(mapped),
        "unmerged_source_patterns": len(unmerged),
        "error_count": len(errors),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--meta-group", type=int, action="append")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "meta-manifest.json").read_text(encoding="utf-8"))
    groups = args.meta_group or list(range(1, manifest["meta_group_count"] + 1))
    results = [validate_meta(args.directory, group) for group in groups]
    output = {"meta_groups": results, "error_count": sum(row["error_count"] for row in results)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
