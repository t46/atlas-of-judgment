"""Validate one skeptical new-card challenge audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


VERDICTS = {"retain_distinct", "absorb_existing", "revise_boundary", "source_insufficient"}
CONFIDENCES = {"low", "medium", "high"}


def text(value: Any, minimum: int) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(directory: Path, card_id: str, auditor: str) -> list[str]:
    source = load(directory / f"challenge-{card_id}.jsonl")
    output_path = directory / f"audit-{card_id}-{auditor}.jsonl"
    report_path = directory / f"audit-{card_id}-{auditor}-report.md"
    if not output_path.exists():
        return [f"missing {output_path}"]
    errors: list[str] = []
    rows = load(output_path)
    if [row.get("episode_id") for row in rows] != [row["episode_id"] for row in source]:
        errors.append("episode IDs/order differ from challenge packet")
    required = {
        "episode_id", "candidate_card_id", "verdict", "strongest_existing_rival",
        "gate_components", "decisive_endpoint", "reason", "confidence",
        "proposed_boundary_change",
    }
    for row in rows:
        prefix = f"{row.get('episode_id', 'unknown')}:"
        if set(row) != required:
            errors.append(f"{prefix} top-level keys differ")
        if row.get("candidate_card_id") != card_id:
            errors.append(f"{prefix} candidate card differs")
        if row.get("verdict") not in VERDICTS:
            errors.append(f"{prefix} invalid verdict")
        rival = row.get("strongest_existing_rival")
        if rival is not None and (not isinstance(rival, str) or not rival.startswith("A-P")):
            errors.append(f"{prefix} invalid strongest_existing_rival")
        components = row.get("gate_components")
        if not isinstance(components, list) or not components:
            errors.append(f"{prefix} missing gate_components")
        else:
            for component in components:
                if not isinstance(component, dict) or set(component) != {"component", "present", "evidence"}:
                    errors.append(f"{prefix} malformed gate component")
                    continue
                if not text(component.get("component"), 4) or not isinstance(component.get("present"), bool) or not text(component.get("evidence"), 15):
                    errors.append(f"{prefix} weak gate component")
        if not text(row.get("decisive_endpoint"), 25) or not text(row.get("reason"), 60):
            errors.append(f"{prefix} endpoint/reason too short")
        if row.get("confidence") not in CONFIDENCES:
            errors.append(f"{prefix} invalid confidence")
        boundary = row.get("proposed_boundary_change")
        if boundary is not None and not text(boundary, 30):
            errors.append(f"{prefix} weak proposed_boundary_change")
        if row.get("verdict") == "revise_boundary" and boundary is None:
            errors.append(f"{prefix} revise_boundary requires proposed_boundary_change")
    if not report_path.exists() or len(report_path.read_text(encoding="utf-8").strip()) < 500:
        errors.append("missing or too-short report")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--card", required=True, choices=("N-P01", "N-P02", "N-P03"))
    parser.add_argument("--auditor", required=True)
    args = parser.parse_args()
    errors = validate(args.directory, args.card, args.auditor)
    print(json.dumps({"card": args.card, "auditor": args.auditor, "error_count": len(errors), "errors": errors}, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
