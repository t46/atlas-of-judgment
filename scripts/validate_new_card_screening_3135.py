"""Validate blind screening outputs for the three proposed Atlas cards."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path(
    "data/analysis/iclr/episode-reclassification-3135/new-card-screening"
)
CARD_IDS = {"N-P01", "N-P02", "N-P03"}
FITS = {"core", "variant", "boundary"}
CONFIDENCES = {"low", "medium", "high"}
CHAIN_FIELDS = {
    "inspected_objects", "observations", "reasoning_bridge", "judgments",
    "requested_tests_or_changes", "signatures", "missingness",
}


def _text(value: Any, minimum: int) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: row must be an object")
        rows.append(row)
    return rows


def _source_episodes(path: Path) -> dict[str, dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    try:
        block = source.split("```jsonl\n", 1)[1].split("\n```", 1)[0]
    except IndexError as exc:
        raise ValueError(f"missing source JSONL block: {path}") from exc
    rows = [json.loads(line) for line in block.splitlines() if line.strip()]
    return {row["episode_id"]: row for row in rows}


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _validate_gate(card_id: str, evidence: str) -> str | None:
    """Reject generic evidence that omits a card's decisive three-part gate."""
    gate = evidence.casefold()
    if card_id == "N-P01":
        groups = (
            ("observed", "established", "credible", "result", "failure", "behavior", "outcome"),
            ("mechanism", "why", "process", "producing", "explanation", "account"),
            ("remedy", "intervention", "next test", "next experiment", "repair", "distinguish", "discriminat", "what to try"),
        )
        label = "credible observation + missing why-account + discriminating remedy"
    elif card_id == "N-P02":
        groups = (
            ("even if", "fully reported", "complete reporting", "reporting were complete", "reconstruct", "remains"),
            ("problem", "task", "motivation", "concept", "boundary", "promise"),
            ("design", "method", "machinery", "rationale", "principled response", "operation"),
        )
        label = "complete-reporting counterfactual + task/problem side + design/rationale side"
    else:
        groups = (
            ("underlying", "science", "claim", "technical", "reconstruct", "inspectable", "intact"),
            ("figure", "notation", "exposition", "organization", "copyedit", "wording", "terminology", "presentation", "caption"),
            ("reader", "communication", "publication", "professional", "navigate", "comprehension", "readiness"),
        )
        label = "intact science/reconstruction + concrete presentation defect + communication endpoint"
    if not all(_contains(gate, terms) for terms in groups):
        return f"{card_id} gate_evidence must state {label}"
    return None


def validate_shard(directory: Path, shard: int) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest_row = next((row for row in manifest["shards"] if row["shard"] == shard), None)
    if manifest_row is None:
        return {"shard": shard, "error_count": 1, "errors": ["shard absent from manifest"]}
    expected = manifest_row["episode_ids"]
    source_by_id = _source_episodes(directory / f"source-shard-{shard:03d}.md")
    output_path = directory / f"screen-shard-{shard:03d}.jsonl"
    report_path = directory / f"screen-shard-{shard:03d}-report.md"
    if not output_path.exists():
        return {"shard": shard, "error_count": 1, "errors": [f"missing {output_path}"]}
    errors: list[str] = []
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 400:
        errors.append(f"missing or too-short report: {report_path}")
    try:
        rows = _load_jsonl(output_path)
    except (ValueError, OSError) as exc:
        return {"shard": shard, "error_count": 1, "errors": [str(exc)]}
    if [row.get("episode_id") for row in rows] != expected:
        errors.append("output episode IDs must match manifest exactly and in source order")

    reason_owners: dict[str, set[str]] = defaultdict(set)
    with_membership = with_uncertainty = audit_count = 0
    card_counts = {card_id: 0 for card_id in sorted(CARD_IDS)}
    required = {
        "episode_id", "new_memberships", "uncertain_new_cards",
        "closest_excluded_new_cards", "screen_confidence", "needs_source_audit",
    }
    for index, row in enumerate(rows, 1):
        episode_id = row.get("episode_id", f"record-{index}")
        prefix = f"{episode_id}:"
        if set(row) != required:
            errors.append(f"{prefix} top-level keys differ")
        memberships = row.get("new_memberships", [])
        uncertain = row.get("uncertain_new_cards", [])
        excluded = row.get("closest_excluded_new_cards", [])
        if not all(isinstance(value, list) for value in (memberships, uncertain, excluded)):
            errors.append(f"{prefix} membership/uncertainty/exclusion values must be lists")
            continue
        if len(excluded) > 3:
            errors.append(f"{prefix} closest_excluded_new_cards exceeds three")
        collections = (
            ("new_memberships", memberships),
            ("uncertain_new_cards", uncertain),
            ("closest_excluded_new_cards", excluded),
        )
        id_sets: list[set[str]] = []
        for label, values in collections:
            ids = [item.get("card_id") for item in values if isinstance(item, dict)]
            if len(ids) != len(values) or len(ids) != len(set(ids)) or set(ids) - CARD_IDS:
                errors.append(f"{prefix} invalid or duplicate {label} card IDs")
            id_sets.append(set(ids))
        if any(id_sets[a] & id_sets[b] for a, b in ((0, 1), (0, 2), (1, 2))):
            errors.append(f"{prefix} membership, uncertainty, and exclusion sets overlap")

        for item in memberships:
            if not isinstance(item, dict) or set(item) != {
                "card_id", "fit", "gate_evidence", "reason",
                "decisive_chain_fields", "confidence",
            }:
                errors.append(f"{prefix} malformed membership")
                continue
            card_id = item.get("card_id")
            if item.get("fit") not in FITS or item.get("confidence") not in CONFIDENCES:
                errors.append(f"{prefix} invalid membership fit/confidence")
            if not _text(item.get("reason"), 50):
                errors.append(f"{prefix} membership reason too short")
            if not _text(item.get("gate_evidence"), 60):
                errors.append(f"{prefix} membership gate_evidence too short")
            elif card_id in CARD_IDS and (gate_error := _validate_gate(card_id, item["gate_evidence"])):
                errors.append(f"{prefix} {gate_error}")
            fields = item.get("decisive_chain_fields")
            if not isinstance(fields, list) or not fields or set(fields) - CHAIN_FIELDS:
                errors.append(f"{prefix} invalid decisive_chain_fields")
            normalized = re.sub(r"\s+", " ", item.get("reason", "")).strip().casefold()
            reason_owners[normalized].add(episode_id)
            if card_id in card_counts:
                card_counts[card_id] += 1
        for item in uncertain:
            if not isinstance(item, dict) or set(item) != {"card_id", "reason", "missing_links"}:
                errors.append(f"{prefix} malformed uncertain card")
                continue
            if not _text(item.get("reason"), 40):
                errors.append(f"{prefix} uncertainty reason too short")
            missing = item.get("missing_links")
            if not isinstance(missing, list) or not missing or not all(_text(value, 5) for value in missing):
                errors.append(f"{prefix} uncertainty needs concrete missing_links")
        for item in excluded:
            if not isinstance(item, dict) or set(item) != {"card_id", "reason"}:
                errors.append(f"{prefix} malformed closest excluded card")
            elif not _text(item.get("reason"), 40):
                errors.append(f"{prefix} closest-excluded reason too short")

        if row.get("screen_confidence") not in CONFIDENCES:
            errors.append(f"{prefix} invalid screen_confidence")
        if uncertain and row.get("screen_confidence") == "high":
            errors.append(f"{prefix} uncertainty is incompatible with high confidence")
        if uncertain and row.get("needs_source_audit") is not True:
            errors.append(f"{prefix} uncertain card requires source audit")
        if not isinstance(row.get("needs_source_audit"), bool):
            errors.append(f"{prefix} needs_source_audit must be boolean")
        source_episode = source_by_id.get(episode_id, {})
        has_primary = any(
            value.get("provenance_level") == "primary"
            for value in source_episode.get("evidence", {}).values()
            if isinstance(value, dict)
        )
        missing_links = source_episode.get("quality", {}).get("missing_links", [])
        if (not has_primary or missing_links) and row.get("needs_source_audit") is not True:
            errors.append(f"{prefix} provenance-limited source requires needs_source_audit")
        if missing_links and row.get("screen_confidence") == "high":
            errors.append(f"{prefix} missing source links are incompatible with high confidence")
        with_membership += bool(memberships)
        with_uncertainty += bool(uncertain)
        audit_count += row.get("needs_source_audit") is True

    for reason, owners in reason_owners.items():
        if reason and len(owners) >= 3:
            errors.append(f"exact membership reason reused across {len(owners)} episodes: {reason!r}")
    return {
        "shard": shard, "episode_count": len(rows),
        "episodes_with_membership": with_membership,
        "episodes_with_uncertainty": with_uncertainty,
        "episodes_needing_source_audit": audit_count,
        "card_membership_counts": card_counts,
        "error_count": len(errors), "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--shard", type=int, action="append")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    shards = args.shard or [row["shard"] for row in manifest["shards"]]
    results = [validate_shard(args.directory, shard) for shard in shards]
    output = {
        "shards": results, "shard_count": len(results),
        "episode_count": sum(row.get("episode_count", 0) for row in results),
        "error_count": sum(row["error_count"] for row in results),
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(1 if output["error_count"] else 0)


if __name__ == "__main__":
    main()
