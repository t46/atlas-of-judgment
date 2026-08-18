"""Validate candidate-card refinement v2 shard outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-reclassification-3135/new-card-refinement-v2")
CARDS = {"N-P01", "N-P02", "N-P03"}
VERDICTS = {"confirmed", "excluded", "uncertain"}
FITS = {"core", "variant", "boundary"}
CONFIDENCES = {"low", "medium", "high"}


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def text(value: Any, minimum: int) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    shards = manifest.get("shards", [])
    shard_ids = [row.get("shard") for row in shards]
    if len(shard_ids) != len(set(shard_ids)):
        errors.append("duplicate shard id in manifest")
    if manifest.get("shard_count") != len(shards):
        errors.append("manifest shard_count differs from shards length")
    keys = [tuple(key) for row in shards for key in row.get("keys", [])]
    if len(keys) != len(set(keys)):
        errors.append("duplicate episode/card key across manifest shards")
    if manifest.get("pair_count") != len(keys):
        errors.append("manifest pair_count differs from key count")
    for row in shards:
        if row.get("pair_count") != len(row.get("keys", [])):
            errors.append(f"shard {row.get('shard')} pair_count differs from key count")
    return errors


def validate(directory: Path, shard: int) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest_row = next((row for row in manifest["shards"] if row["shard"] == shard), None)
    if manifest_row is None:
        return {"shard": shard, "error_count": 1, "errors": ["missing manifest shard"]}
    output = directory / f"refined-shard-{shard:03d}.jsonl"
    report = directory / f"refined-shard-{shard:03d}-report.md"
    if not output.exists():
        return {"shard": shard, "error_count": 1, "errors": [f"missing {output}"]}
    rows = load(output)
    errors: list[str] = []
    if [[row.get("episode_id"), row.get("card_id")] for row in rows] != manifest_row["keys"]:
        errors.append("episode/card keys or order differ")
    required = {
        "episode_id", "card_id", "verdict", "fit", "gate_evidence", "reason",
        "strongest_existing_rival", "n_p02_route", "missing_links", "confidence",
        "needs_source_audit",
    }
    counts = {verdict: 0 for verdict in sorted(VERDICTS)}
    card_counts = {card: 0 for card in sorted(CARDS)}
    for row in rows:
        prefix = f"{row.get('episode_id')}:{row.get('card_id')}:"
        if set(row) != required:
            errors.append(f"{prefix} top-level keys differ")
        card, verdict = row.get("card_id"), row.get("verdict")
        if card not in CARDS or verdict not in VERDICTS:
            errors.append(f"{prefix} invalid card/verdict")
            continue
        route = row.get("n_p02_route")
        if card == "N-P02":
            if route not in {"task_design", "concept_boundary_operation", "none"}:
                errors.append(f"{prefix} invalid N-P02 route")
            if verdict == "confirmed" and route == "none":
                errors.append(f"{prefix} confirmed N-P02 requires a positive route")
            if verdict == "excluded":
                reason = (row.get("reason") or "").casefold()
                if route != "none" or not any(term in reason for term in ("task", "problem", "motivation")) or not any(term in reason for term in ("concept", "category", "boundary")):
                    errors.append(f"{prefix} excluded N-P02 must reject both routes")
        elif route is not None:
            errors.append(f"{prefix} non-N-P02 route must be null")
        counts[verdict] += 1
        if verdict == "confirmed":
            card_counts[card] += 1
            if row.get("fit") not in FITS or not text(row.get("gate_evidence"), 100):
                errors.append(f"{prefix} confirmed requires fit and detailed gate_evidence")
            gate = (row.get("gate_evidence") or "").casefold()
            if card == "N-P01" and not all(any(term in gate for term in group) for group in (
                ("credible", "observed", "established", "accepted", "result", "failure", "phenomenon"),
                ("mechanism", "process", "producing", "why-account", "competing explanation"),
                ("test", "intervention", "remedy", "repair", "distinguish", "discriminat"),
            )):
                errors.append(f"{prefix} N-P01 v2 gate incomplete")
            if card == "N-P02":
                route_a = any(term in gate for term in ("task", "problem", "motivation")) and any(
                    term in gate for term in ("design", "operation", "representation", "machinery")
                )
                route_b = any(term in gate for term in ("concept", "category", "boundary")) and any(
                    term in gate for term in ("design", "operation", "representation", "compatib", "consistent")
                )
                common = all(any(term in gate for term in group) for group in (
                    ("complete reporting", "fully reported", "reporting", "remains", "survive"),
                    ("independent", "not reducible", "separate", "after resolving"),
                ))
                if not common or not (route_a or route_b):
                    errors.append(f"{prefix} N-P02 v2 gate incomplete")
            if card == "N-P03" and not all(any(term in gate for term in group) for group in (
                ("surface", "copyedit", "typograph", "presentation", "legibility", "organization", "spacing", "typo"),
                ("claim", "method", "proof", "evidence", "science"),
                ("reconstruct", "inspectable", "intact", "independent"),
                ("reader", "communication", "publication"),
            )):
                errors.append(f"{prefix} N-P03 v2 gate incomplete")
        else:
            if row.get("fit") is not None or row.get("gate_evidence") is not None:
                errors.append(f"{prefix} non-confirmed must have null fit/gate_evidence")
        if not text(row.get("reason"), 60):
            errors.append(f"{prefix} reason too short")
        rival = row.get("strongest_existing_rival")
        if rival is not None and (not isinstance(rival, str) or not rival.startswith("A-P")):
            errors.append(f"{prefix} invalid rival")
        missing = row.get("missing_links")
        if not isinstance(missing, list):
            errors.append(f"{prefix} missing_links must be list")
        if verdict == "uncertain":
            if not missing or row.get("needs_source_audit") is not True or row.get("confidence") == "high":
                errors.append(f"{prefix} uncertain requires links, audit, and non-high confidence")
        elif missing:
            errors.append(f"{prefix} non-uncertain missing_links must be empty")
        if row.get("confidence") not in CONFIDENCES or not isinstance(row.get("needs_source_audit"), bool):
            errors.append(f"{prefix} invalid confidence/audit")
    if not report.exists() or len(report.read_text(encoding="utf-8").strip()) < 400:
        errors.append("missing or too-short report")
    return {"shard": shard, "pair_count": len(rows), "verdict_counts": counts, "confirmed_card_counts": card_counts, "error_count": len(errors), "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--shard", type=int, action="append")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    shards = args.shard or [row["shard"] for row in manifest["shards"]]
    results = [validate(args.directory, shard) for shard in shards]
    manifest_errors = validate_manifest(manifest)
    output = {
        "shard_count": len(results),
        "pair_count": sum(row.get("pair_count", 0) for row in results),
        "manifest_errors": manifest_errors,
        "error_count": len(manifest_errors) + sum(row["error_count"] for row in results),
        "shards": results,
    }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
