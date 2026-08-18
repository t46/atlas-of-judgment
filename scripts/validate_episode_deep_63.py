"""Validate selective Episode Deep outputs against schema, source, and Lite spine."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


DEFAULT_DIR = Path("data/analysis/iclr/episode-deep-63")
DEFAULT_SCHEMA = Path("schemas/evaluation-episode-v0.2.json")
DEEP_CLAIM_FIELDS = (
    "focal_factors",
    "standards",
    "comparisons",
    "assumptions",
    "alternative_explanations",
    "counterfactuals",
    "inference_steps",
    "expected_information_gain",
    "repair_conditions",
)
PRESERVED_FIELDS = ("source", "chain", "signatures", "quality")
GENERIC_PHRASES = (
    "the concrete issue described",
    "the relevant evidence",
    "the stated concern",
    "address the issue",
    "the paper should provide more evidence",
    "this would help clarify",
    "a stronger evaluation would resolve the concern",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def source_episodes(path: Path) -> dict[str, dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    try:
        block = source.split("```jsonl\n", 1)[1].split("\n```", 1)[0]
    except IndexError as exc:
        raise ValueError(f"missing source JSONL block: {path}") from exc
    episodes = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        episode = row["episode"]
        episodes[episode["episode_id"]] = episode
    return episodes


def iter_claims(episode: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    deep = episode["deep"]
    for field in DEEP_CLAIM_FIELDS:
        for claim in deep[field]:
            yield field, claim


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def validate_unit(
    directory: Path,
    schema_path: Path,
    unit: int,
) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    row = next(item for item in manifest["reviews"] if item["unit"] == unit)
    expected_ids = set(row["focal_episode_ids"])
    source_path = directory / f"source-review-{unit:02d}.md"
    output_path = directory / f"deep-review-{unit:02d}.jsonl"
    report_path = directory / f"deep-review-{unit:02d}-report.md"
    errors: list[str] = []
    if not output_path.exists():
        return {"unit": unit, "review_id": row["review_id"], "error_count": 1, "errors": [f"missing {output_path}"]}
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 300:
        errors.append(f"missing or too-short report: {report_path}")
    source_text = source_path.read_text(encoding="utf-8")
    lite_by_id = source_episodes(source_path)
    try:
        outputs = load_jsonl(output_path)
    except (json.JSONDecodeError, OSError) as exc:
        return {"unit": unit, "review_id": row["review_id"], "error_count": 1, "errors": [f"invalid JSONL: {exc}"]}
    output_ids = [episode.get("episode_id") for episode in outputs]
    if len(output_ids) != len(set(output_ids)):
        errors.append("duplicate output episode IDs")
    if set(output_ids) != expected_ids:
        errors.append(
            f"output IDs differ: expected={sorted(expected_ids)} actual={sorted(set(output_ids))}"
        )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    claim_count = 0
    empty_fields = 0
    for episode in outputs:
        episode_id = episode.get("episode_id", "<missing>")
        prefix = f"{episode_id}:"
        for error in sorted(validator.iter_errors(episode), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.path)
            errors.append(f"{prefix} schema {location}: {error.message}")
        if episode.get("enrichment_level") != "deep":
            errors.append(f"{prefix} enrichment_level must be deep")
        source = episode.get("source", {})
        expected_episode_prefix = (
            f"E-{source.get('paper_id')}-{source.get('review_id')}-"
        )
        if (
            not isinstance(episode_id, str)
            or not episode_id.startswith(expected_episode_prefix)
            or re.fullmatch(r"\d{2}", episode_id[len(expected_episode_prefix) :])
            is None
        ):
            errors.append(
                f"{prefix} noncanonical episode_id; expected "
                f"{expected_episode_prefix}NN with a two-digit suffix"
            )
        lite = lite_by_id.get(episode_id)
        if lite is None:
            continue
        if episode.get("schema_version") != lite["schema_version"]:
            errors.append(f"{prefix} schema_version changed")
        for field in PRESERVED_FIELDS:
            if episode.get(field) != lite[field]:
                errors.append(f"{prefix} Lite field changed: {field}")
        evidence = episode.get("evidence", {})
        for key, value in lite["evidence"].items():
            if evidence.get(key) != value:
                errors.append(f"{prefix} existing evidence changed: {key}")
        existing_evidence_keys = set(lite["evidence"])
        for key, value in evidence.items():
            ref = value.get("ref", "") if isinstance(value, dict) else ""
            local_line = ref.rsplit(":L", 1)[-1] if ":L" in ref else ""
            source_has_locator = (
                f"[{ref}]" in source_text
                or (value.get("provenance_level") == "primary" and f"[L{local_line}]" in source_text)
            )
            if key not in existing_evidence_keys and not source_has_locator:
                errors.append(f"{prefix} evidence locator is absent from source packet: {key}={ref}")

        deep = episode.get("deep")
        if not isinstance(deep, dict):
            continue
        for field in DEEP_CLAIM_FIELDS:
            if field not in deep:
                errors.append(f"{prefix} missing deep field: {field}")
                continue
            claims = deep[field]
            if not claims:
                empty_fields += 1
            for claim in claims:
                claim_count += 1
                text = claim.get("text", "")
                if len(text.strip()) < 20:
                    errors.append(f"{prefix} {field} claim is too short: {text!r}")
                if any(phrase in text.casefold() for phrase in GENERIC_PHRASES):
                    errors.append(f"{prefix} {field} contains generic prose: {text!r}")
                refs = claim.get("evidence_refs", [])
                unknown = set(refs) - set(evidence)
                if unknown:
                    errors.append(f"{prefix} {field} has unknown evidence keys: {sorted(unknown)}")
                if claim.get("status") == "reviewer_explicit" and not any(
                    evidence.get(key, {}).get("provenance_level") == "primary"
                    for key in refs
                ):
                    errors.append(f"{prefix} reviewer_explicit {field} lacks primary evidence")
        if not lite["chain"]["requested_tests_or_changes"] and deep.get("expected_information_gain"):
            errors.append(f"{prefix} expected_information_gain must be empty without a Lite request")
        if deep.get("trajectory_links") != []:
            errors.append(f"{prefix} trajectory_links must be empty for initial-review packet")
        intervention = deep.get("intervention_spec")
        if intervention is not None:
            if set(intervention) != {"held_fixed", "varied", "contrasts"}:
                errors.append(f"{prefix} intervention_spec must contain held_fixed/varied/contrasts")
            if not intervention.get("varied") or not intervention.get("contrasts"):
                errors.append(f"{prefix} intervention_spec needs nonempty varied and contrasts")
            if not lite["chain"]["requested_tests_or_changes"]:
                errors.append(f"{prefix} intervention_spec requires a Lite requested test/change")

    return {
        "unit": unit,
        "review_id": row["review_id"],
        "episode_count": len(outputs),
        "claim_count": claim_count,
        "empty_deep_fields": empty_fields,
        "error_count": len(errors),
        "errors": errors,
    }


def duplicate_claim_errors(directory: Path, units: list[int]) -> list[str]:
    occurrences: dict[str, set[str]] = defaultdict(set)
    original: dict[str, str] = {}
    for unit in units:
        path = directory / f"deep-review-{unit:02d}.jsonl"
        if not path.exists():
            continue
        try:
            episodes = load_jsonl(path)
        except (json.JSONDecodeError, OSError):
            # validate_unit reports the malformed unit; cross-review reuse must
            # not crash the whole validation report before it can be emitted.
            continue
        for episode in episodes:
            review_id = episode["source"]["review_id"]
            for _, claim in iter_claims(episode):
                normalized = _normalize(claim["text"])
                occurrences[normalized].add(review_id)
                original.setdefault(normalized, claim["text"])
    return [
        f"exact Deep claim reused across {len(review_ids)} reviews: {original[text]!r}"
        for text, review_ids in occurrences.items()
        if len(review_ids) >= 3
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--unit", type=int, action="append")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    units = args.unit or [row["unit"] for row in manifest["reviews"]]
    results = [validate_unit(args.directory, args.schema, unit) for unit in units]
    cross_errors = duplicate_claim_errors(args.directory, units) if not args.unit else []
    output = {
        "units": results,
        "unit_count": len(results),
        "episode_count": sum(row.get("episode_count", 0) for row in results),
        "claim_count": sum(row.get("claim_count", 0) for row in results),
        "cross_review_error_count": len(cross_errors),
        "cross_review_errors": cross_errors,
        "error_count": sum(row["error_count"] for row in results) + len(cross_errors),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
