"""Validate the final Evaluation Logic Atlas pattern map."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")
ID_RE = re.compile(r"^A-P\d{2}$")
LIST_LOGIC_KEYS = {"inspected_object_types", "observation_forms", "judgment_forms", "request_roles"}
BANNED_NAMES = ("umbrella", "coverage", "claim-to-evidence", "evidence-chain sufficiency")


def validate(directory: Path) -> dict:
    manifest = json.loads((directory / "final-manifest.json").read_text(encoding="utf-8"))
    allowed_meta = set(manifest["meta_patterns"])
    allowed_episodes = set(manifest["episode_ids"])
    map_path = directory / "final-pattern-map.json"
    report_path = directory / "atlas-report.md"
    errors: list[str] = []
    if not map_path.exists():
        return {"error_count": 1, "errors": [f"missing {map_path}"]}
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 2000:
        errors.append("atlas report missing or too short")
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    patterns = payload.get("patterns", [])
    if not 10 <= len(patterns) <= 18:
        errors.append(f"Atlas pattern count must be 10-18: {len(patterns)}")
    pattern_ids = [pattern.get("pattern_id") for pattern in patterns]
    if len(pattern_ids) != len(set(pattern_ids)):
        errors.append("duplicate Atlas pattern IDs")
    mapped: set[str] = set()
    standards: set[str] = set()
    templates: set[str] = set()
    for index, pattern in enumerate(patterns, 1):
        prefix = f"pattern {index}"
        pattern_id = pattern.get("pattern_id", "")
        if not ID_RE.match(pattern_id):
            errors.append(f"{prefix}: invalid pattern_id {pattern_id}")
        name = pattern.get("name", "")
        if not isinstance(name, str) or any(word in name.casefold() for word in BANNED_NAMES):
            errors.append(f"{prefix}: generic or invalid name {name}")
        logic = pattern.get("core_logic", {})
        for key in LIST_LOGIC_KEYS:
            values = logic.get(key)
            if not isinstance(values, list) or not values or not all(isinstance(value, str) and value.strip() for value in values):
                errors.append(f"{prefix}: core_logic.{key} must be a nonempty string list")
        for key, registry in (("evaluative_standard", standards), ("reasoning_template", templates)):
            value = logic.get(key)
            normalized = value.casefold().strip() if isinstance(value, str) else ""
            if len(normalized) < 40:
                errors.append(f"{prefix}: core_logic.{key} must be specific")
            elif normalized in registry:
                errors.append(f"{prefix}: duplicate core_logic.{key}")
            registry.add(normalized)
        source_ids = pattern.get("source_meta_pattern_ids", [])
        source_set = set(source_ids)
        mapped.update(source_set)
        if len(source_ids) != len(source_set):
            errors.append(f"{prefix}: duplicate source_meta_pattern_ids")
        if unknown := source_set - allowed_meta:
            errors.append(f"{prefix}: unknown source meta patterns {sorted(unknown)}")
        meta_groups = {manifest["meta_patterns"][item]["meta_group"] for item in source_set if item in allowed_meta}
        if len(source_set) < 2 or len(meta_groups) < 2:
            errors.append(f"{prefix}: must merge >=2 meta patterns from >=2 meta groups")
        members = {
            episode_id
            for source_id in source_set
            if source_id in allowed_meta
            for episode_id in manifest["meta_patterns"][source_id]["member_episode_ids"]
        }
        representatives = pattern.get("representative_episode_ids", [])
        if not 2 <= len(representatives) <= 6 or not set(representatives) <= members:
            errors.append(f"{prefix}: representatives must be 2-6 member episodes")
        boundaries = pattern.get("boundary_episode_ids", [])
        if len(boundaries) > 6 or not set(boundaries) <= members:
            errors.append(f"{prefix}: boundaries must be <=6 member episodes")
        counterexamples = pattern.get("counterexample_episode_ids", [])
        if len(counterexamples) > 6 or not set(counterexamples) <= allowed_episodes:
            errors.append(f"{prefix}: counterexamples must be <=6 source episodes")
        variants = pattern.get("variants")
        if not isinstance(variants, list):
            errors.append(f"{prefix}: variants must be a list")
        else:
            for variant in variants:
                if not {"name", "distinction", "source_meta_pattern_ids"} <= set(variant):
                    errors.append(f"{prefix}: malformed variant")
                elif not set(variant["source_meta_pattern_ids"]) <= source_set:
                    errors.append(f"{prefix}: variant source IDs must belong to pattern")
        if unknown := set(pattern.get("confusable_with", [])) - set(pattern_ids):
            errors.append(f"{prefix}: unknown confusable IDs {sorted(unknown)}")
        if pattern.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"{prefix}: invalid confidence")
        for key in ("inclusion_rule", "exclusion_rule", "merge_rationale"):
            if not isinstance(pattern.get(key), str) or len(pattern[key].strip()) < 30:
                errors.append(f"{prefix}: {key} must be specific")
        if not isinstance(pattern.get("notes"), list):
            errors.append(f"{prefix}: notes must be a list")

    unmerged_raw = payload.get("unmerged_meta_pattern_ids", [])
    unmerged = set(unmerged_raw)
    if len(unmerged_raw) != len(unmerged):
        errors.append("duplicate unmerged meta IDs")
    if unknown := unmerged - allowed_meta:
        errors.append(f"unknown unmerged meta patterns: {sorted(unknown)}")
    if overlap := mapped & unmerged:
        errors.append(f"mapped/unmerged overlap: {sorted(overlap)}")
    if missing := allowed_meta - mapped - unmerged:
        errors.append(f"uncovered meta patterns: {sorted(missing)}")
    if len(unmerged) / len(allowed_meta) > 0.15:
        errors.append(f"unmerged share exceeds 15%: {len(unmerged)}/{len(allowed_meta)}")
    contrasts = payload.get("global_contrasts")
    if not isinstance(contrasts, list) or not contrasts:
        errors.append("global_contrasts must be a nonempty list")
    else:
        for index, contrast in enumerate(contrasts, 1):
            if not isinstance(contrast, dict):
                errors.append(f"global contrast {index} must be an object")
                continue
            text = contrast.get("contrast")
            episode_ids = contrast.get("episode_ids")
            if not isinstance(text, str) or len(text.strip()) < 40:
                errors.append(f"global contrast {index} text is too short")
            if (
                not isinstance(episode_ids, list)
                or len(episode_ids) < 2
                or not set(episode_ids) <= allowed_episodes
            ):
                errors.append(
                    f"global contrast {index} needs >=2 valid source episode IDs"
                )
    limitations = payload.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or not all(isinstance(item, str) and item.strip() for item in limitations)
    ):
        errors.append("limitations must be a nonempty list")
    return {
        "pattern_count": len(patterns),
        "meta_pattern_count": len(allowed_meta),
        "mapped_meta_patterns": len(mapped),
        "unmerged_meta_patterns": len(unmerged),
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
