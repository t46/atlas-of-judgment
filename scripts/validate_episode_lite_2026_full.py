"""Validate resumable full-corpus ICLR 2026 Episode Lite shard outputs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

try:
    from .validate_episode_lite_1000 import (
        PRIMARY_REF_RE,
        SOURCE_PRIMARY_REF_RE,
        WRAPPER_REF_RE,
        find_cross_review_reuse,
        is_generic_claim_text,
        is_generic_signature,
        iter_claims,
        iter_labeled_texts,
        load_episodes,
    )
except ImportError:  # Direct script execution.
    from validate_episode_lite_1000 import (
        PRIMARY_REF_RE,
        SOURCE_PRIMARY_REF_RE,
        WRAPPER_REF_RE,
        find_cross_review_reuse,
        is_generic_claim_text,
        is_generic_signature,
        iter_claims,
        iter_labeled_texts,
        load_episodes,
    )


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-lite-2026-full")
DEFAULT_SCHEMA = Path("schemas/evaluation-episode-v0.2.json")


def load_metadata(directory: Path, shard: int) -> dict[str, Any]:
    return json.loads(
        (directory / f"source-shard-{shard:05d}.json").read_text(encoding="utf-8")
    )


def validate_shard(
    directory: Path,
    schema_validator: Draft202012Validator,
    connection: sqlite3.Connection,
    shard: int,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    metadata = load_metadata(directory, shard)
    if metadata.get("shard") != shard:
        errors.append("source metadata shard mismatch")
    reviews = metadata.get("reviews", [])
    review_ids = [row.get("review_id") for row in reviews]
    if len(review_ids) != len(set(review_ids)):
        errors.append("duplicate review ID in source metadata")
    selected = {row["review_id"]: row for row in reviews}

    episode_path = directory / f"episodes-shard-{shard:05d}.jsonl"
    coverage_path = directory / f"coverage-shard-{shard:05d}.json"
    for path in (episode_path, coverage_path):
        if not path.exists():
            errors.append(f"missing output: {path}")
    if errors:
        return {
            "shard": shard,
            "selected_reviews": len(reviews),
            "covered_reviews": 0,
            "episodes": 0,
            "warning_count": len(warnings),
            "error_count": len(errors),
            "warnings": warnings,
            "errors": errors,
        }

    valid_primary: dict[str, set[str]] = {}
    valid_wrapper: dict[str, dict[str, str]] = {}
    for review_id, item in selected.items():
        row = connection.execute(
            """
            SELECT j.paper_id, j.user_prompt, m.memo
            FROM jobs AS j JOIN memos AS m USING(job_id)
            WHERE j.job_id=? AND j.stage='initial_blind' AND j.status='complete'
            """,
            (f"initial:{review_id}",),
        ).fetchone()
        if row is None:
            errors.append(f"source job missing for {review_id}")
            valid_primary[review_id] = set()
            valid_wrapper[review_id] = {}
            continue
        paper_id, user_prompt, memo = row
        if paper_id != item["paper_id"]:
            errors.append(f"source paper mismatch for {review_id}")
        valid_primary[review_id] = set(SOURCE_PRIMARY_REF_RE.findall(user_prompt))
        valid_wrapper[review_id] = {
            f"I-{review_id}:L{index:03d}": line
            for index, line in enumerate(memo.splitlines(), 1)
        }

    episodes = load_episodes(episode_path)
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    if coverage.get("shard") != shard:
        errors.append("coverage shard mismatch")
    coverage_rows: dict[str, dict[str, Any]] = {}
    status_counts = Counter()
    for row in coverage.get("reviews", []):
        review_id = row.get("review_id")
        if review_id in coverage_rows:
            errors.append(f"duplicate coverage review: {review_id}")
        coverage_rows[review_id] = row
        status_counts[row.get("status", "missing")] += 1
        required = {
            "review_id", "episode_count", "status", "review_is_substantive",
            "zero_episode_reason", "provenance_failure", "notes",
        }
        missing = required - set(row)
        if missing:
            errors.append(f"coverage row {review_id} missing {sorted(missing)}")
        if review_id not in selected:
            errors.append(f"coverage review not in source: {review_id}")
        if row.get("status") not in {"complete", "zero", "error"}:
            errors.append(f"coverage row {review_id} has invalid status")
        if not isinstance(row.get("review_is_substantive"), bool):
            errors.append(f"coverage row {review_id} review_is_substantive must be boolean")
        if not isinstance(row.get("notes"), list):
            errors.append(f"coverage row {review_id} notes must be an array")

    episode_ids: set[str] = set()
    episode_counts = Counter()
    provenance = Counter()
    text_instances: list[tuple[int, str, str, str]] = []
    for index, episode in enumerate(episodes, 1):
        prefix = f"{episode_path}:record {index}"
        for error in schema_validator.iter_errors(episode):
            location = "/".join(map(str, error.absolute_path))
            errors.append(f"{prefix}:{location}: {error.message}")
        episode_id = episode.get("episode_id")
        if episode_id in episode_ids:
            errors.append(f"{prefix}: duplicate episode_id {episode_id}")
        episode_ids.add(episode_id)
        if episode.get("enrichment_level") != "lite":
            errors.append(f"{prefix}: enrichment_level must be lite")
        source = episode.get("source", {})
        review_id = source.get("review_id")
        paper_id = source.get("paper_id")
        if review_id not in selected:
            errors.append(f"{prefix}: review not in source metadata: {review_id}")
            continue
        if paper_id != selected[review_id]["paper_id"]:
            errors.append(f"{prefix}: paper_id mismatch for {review_id}")
        expected_prefix = f"E-{paper_id}-{review_id}-"
        suffix = episode_id[len(expected_prefix):] if isinstance(episode_id, str) and episode_id.startswith(expected_prefix) else ""
        if not isinstance(episode_id, str) or not episode_id.startswith(expected_prefix) or re.fullmatch(r"\d{2}", suffix) is None:
            errors.append(f"{prefix}: noncanonical episode_id {episode_id!r}")
        episode_counts[review_id] += 1

        evidence = episode.get("evidence", {})
        levels = {key: value.get("provenance_level") for key, value in evidence.items()}
        for claim in iter_claims(episode):
            claim_text = claim.get("text", "")
            if is_generic_claim_text(claim_text):
                warnings.append(f"{prefix}: generic placeholder claim: {claim_text}")
            for key in claim.get("evidence_refs", []):
                if key not in evidence:
                    errors.append(f"{prefix}: dangling evidence key {key}")
            if claim.get("status") == "reviewer_explicit" and claim.get("evidence_refs") and not any(levels.get(key) == "primary" for key in claim["evidence_refs"]):
                warnings.append(f"{prefix}: reviewer_explicit claim lacks primary evidence")
        for key, ref_row in evidence.items():
            level, ref = ref_row.get("provenance_level"), ref_row.get("ref", "")
            evidence_text = ref_row.get("text")
            provenance[level] += 1
            pattern = PRIMARY_REF_RE if level == "primary" else WRAPPER_REF_RE
            if level in {"primary", "analytic_wrapper"}:
                match = pattern.match(ref)
                if not match or match.group("review") != review_id:
                    warnings.append(f"{prefix}: noncanonical {level} ref {key}={ref}")
                elif level == "primary" and ref not in valid_primary[review_id]:
                    errors.append(f"{prefix}: primary ref does not exist: {ref}")
                elif level == "analytic_wrapper" and ref not in valid_wrapper[review_id]:
                    errors.append(f"{prefix}: wrapper ref does not exist: {ref}")
                if not isinstance(evidence_text, str) or not evidence_text.strip():
                    errors.append(f"{prefix}: {level} evidence {key} requires nonempty text")
                elif level == "analytic_wrapper" and ref in valid_wrapper[review_id] and evidence_text.strip() != valid_wrapper[review_id][ref].strip():
                    errors.append(f"{prefix}: wrapper evidence text differs from source line: {ref}")
        notes = episode.get("quality", {}).get("notes", [])
        if not notes or not notes[0].startswith("Boundary rationale:"):
            warnings.append(f"{prefix}: missing boundary rationale")
        if not any(level == "primary" for level in levels.values()) and "primary_provenance" not in episode.get("quality", {}).get("missing_links", []):
            warnings.append(f"{prefix}: no primary evidence without declared missing link")
        for name in ("concrete", "abstract"):
            signature = episode.get("signatures", {}).get(name, "")
            if is_generic_signature(signature):
                warnings.append(f"{prefix}: generic {name} signature: {signature}")
        for field, value in iter_labeled_texts(episode):
            text_instances.append((shard, field, value, review_id))

    warnings.extend(find_cross_review_reuse(text_instances))
    if len(selected) >= 5 and len(episodes) <= len(selected):
        warnings.append(
            "suspicious one-or-fewer episode yield per review across the entire shard"
        )
    for review_id in selected:
        row = coverage_rows.get(review_id)
        if row is None:
            errors.append(f"missing coverage row for {review_id}")
            continue
        actual = episode_counts[review_id]
        if row.get("episode_count") != actual:
            errors.append(f"coverage count mismatch for {review_id}: declared={row.get('episode_count')} actual={actual}")
        expected_status = "zero" if actual == 0 else "complete"
        if row.get("status") not in {expected_status, "error"}:
            errors.append(f"coverage status mismatch for {review_id}: {row.get('status')}")

    return {
        "shard": shard,
        "selected_reviews": len(selected),
        "covered_reviews": len(coverage_rows),
        "episodes": len(episodes),
        "episode_ids": sorted(episode_ids),
        "coverage_status": dict(status_counts),
        "provenance": dict(provenance),
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": warnings,
        "errors": errors,
    }


def validate(directory: Path, schema_path: Path, shards: list[int]) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    valid_shards = {row["shard"]: row for row in manifest["shards"]}
    schema_validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    connection = sqlite3.connect(
        f"file:{Path(manifest['database']).resolve()}?mode=ro&immutable=1", uri=True
    )
    try:
        results = []
        for shard in shards:
            if shard not in valid_shards:
                results.append({"shard": shard, "selected_reviews": 0, "covered_reviews": 0, "episodes": 0, "episode_ids": [], "warning_count": 0, "error_count": 1, "warnings": [], "errors": ["shard absent from manifest"]})
            else:
                results.append(validate_shard(directory, schema_validator, connection, shard))
    finally:
        connection.close()
    episode_ids = [episode_id for row in results for episode_id in row.pop("episode_ids", [])]
    global_errors = []
    if len(episode_ids) != len(set(episode_ids)):
        global_errors.append("duplicate episode ID across validated shards")
    return {
        "shard_count": len(results),
        "selected_reviews": sum(row["selected_reviews"] for row in results),
        "covered_reviews": sum(row["covered_reviews"] for row in results),
        "episodes": sum(row["episodes"] for row in results),
        "global_errors": global_errors,
        "warning_count": sum(row["warning_count"] for row in results),
        "error_count": len(global_errors) + sum(row["error_count"] for row in results),
        "shards": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--only-shard", type=int, action="append")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    shards = args.only_shard or [row["shard"] for row in manifest["shards"]]
    result = validate(args.directory, args.schema, shards)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(1 if result["error_count"] or result["warning_count"] else 0)


if __name__ == "__main__":
    main()
