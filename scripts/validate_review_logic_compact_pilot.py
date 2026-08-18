"""Validate compact review-logic prompt-tuning scenarios."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


DEFAULT_DIR = Path("data/analysis/iclr/review-logic-compact-pilot")
SCORE_LEAKAGE = re.compile(
    r"\b(?:review(?:er)?|overall|numerical|soundness|contribution|presentation)\s+"
    r"(?:rating|score)\b|"
    r"\b(?:rating|score)\s+of\s+\d+(?:\.\d+)?\b|"
    r"\b(?:accept|accepted|reject|rejected)\b(?=\s+(?:the\s+)?paper\b)|"
    r"\brecommend(?:s|ed|ation)?\s+(?:for\s+)?(?:acceptance|rejection)\b",
    re.I,
)
PRIMARY_REF = re.compile(r"^R-([^:]+):L\d{3,}$")
WRAPPER_REF = re.compile(r"^I-([^:]+):L\d{3,}$")
SOURCE_REF = re.compile(r"\[(R-[^:\]]+:L\d{3,})\]")
GENERIC = (
    "the paper-specific method",
    "the reviewer identifies an issue",
    "this observation matters for the paper",
    "the claim is not fully established",
    "provide clarification or additional evidence",
    "apply the clarification, comparison, or validation requested",
    "apply the requested clarification",
    "the memo identifies a concrete evaluation issue",
    "the reviewer connects the observation to a specific standard",
    "the local evaluation is recorded without inferring",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(directory: Path, scenario: str) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    item = next((row for row in manifest["scenarios"] if row["scenario"] == scenario), None)
    if item is None:
        return {"scenario": scenario, "error_count": 1, "warning_count": 0, "errors": ["scenario absent from manifest"], "warnings": []}
    schema = json.loads(Path(manifest["schema"]).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    metadata = json.loads((directory / item["source_metadata"]).read_text(encoding="utf-8"))
    expected = metadata["reviews"]
    expected_ids = [row["review_id"] for row in expected]
    expected_papers = {row["review_id"]: row["paper_id"] for row in expected}
    output = directory / f"compact-{scenario}.jsonl"
    report = directory / f"compact-{scenario}-report.md"
    errors: list[str] = []
    warnings: list[str] = []
    if not output.exists():
        return {"scenario": scenario, "error_count": 1, "warning_count": 0, "errors": ["missing output"], "warnings": []}
    rows = load_jsonl(output)
    if [row.get("review_id") for row in rows] != expected_ids:
        errors.append("review coverage or source order differs")

    source_manifest = json.loads((Path(manifest["source"]) / "manifest.json").read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{Path(source_manifest['database']).resolve()}?mode=ro&immutable=1", uri=True)
    valid_primary: dict[str, set[str]] = {}
    try:
        for review_id in expected_ids:
            row = connection.execute("SELECT user_prompt FROM jobs WHERE job_id=?", (f"initial:{review_id}",)).fetchone()
            valid_primary[review_id] = set(SOURCE_REF.findall(row[0])) if row else set()
    finally:
        connection.close()
    source_text = (directory / item["source_markdown"]).read_text(encoding="utf-8")
    valid_wrapper = set(re.findall(r"\[(I-[^:\]]+:L\d{3,})\]", source_text))

    field_texts: dict[tuple[str, str], set[str]] = defaultdict(set)
    total_units = 0
    unit_counts_by_review: dict[str, int] = {}
    primary_refs = wrapper_refs = 0
    for row_index, row in enumerate(rows, 1):
        prefix = f"row {row_index}:{row.get('review_id')}"
        for error in validator.iter_errors(row):
            errors.append(f"{prefix}:{'/'.join(map(str, error.absolute_path))}: {error.message}")
        review_id = row.get("review_id")
        if review_id not in expected_papers:
            continue
        if row.get("paper_id") != expected_papers[review_id]:
            errors.append(f"{prefix}: paper_id mismatch")
        summary = row.get("review_logic_summary", "")
        if SCORE_LEAKAGE.search(summary):
            errors.append(f"{prefix}: review summary leaks score/decision")
        units = row.get("logic_units", [])
        total_units += len(units)
        unit_counts_by_review[review_id] = len(units)
        expected_unit_ids = [f"U-{review_id}-{index:02d}" for index in range(1, len(units) + 1)]
        if [unit.get("unit_id") for unit in units] != expected_unit_ids:
            errors.append(f"{prefix}: unit IDs/order differ")
        for unit in units:
            for field in ("inspected_object", "observation", "reasoning", "judgment"):
                text = unit.get(field, "")
                normalized = " ".join(text.casefold().split()) if isinstance(text, str) else ""
                if any(phrase in normalized for phrase in GENERIC):
                    warnings.append(f"{prefix}:{unit.get('unit_id')}: generic {field}")
                if normalized:
                    field_texts[(field, normalized)].add(review_id)
            refs = unit.get("evidence_refs", [])
            if not refs:
                errors.append(f"{prefix}:{unit.get('unit_id')}: no evidence refs")
            for ref in refs:
                primary = PRIMARY_REF.fullmatch(ref)
                wrapper = WRAPPER_REF.fullmatch(ref)
                if primary:
                    primary_refs += 1
                    if primary.group(1) != review_id or ref not in valid_primary[review_id]:
                        errors.append(f"{prefix}:{unit.get('unit_id')}: invalid primary ref {ref}")
                elif wrapper:
                    wrapper_refs += 1
                    if wrapper.group(1) != review_id or ref not in valid_wrapper:
                        errors.append(f"{prefix}:{unit.get('unit_id')}: invalid wrapper ref {ref}")
                else:
                    errors.append(f"{prefix}:{unit.get('unit_id')}: malformed ref {ref}")
            if unit.get("support_status") == "reviewer_explicit" and not any(PRIMARY_REF.fullmatch(ref) for ref in refs):
                errors.append(f"{prefix}:{unit.get('unit_id')}: reviewer_explicit lacks primary ref")
            suggestion = unit.get("suggested_improvement")
            if isinstance(suggestion, str) and not suggestion.strip():
                errors.append(f"{prefix}:{unit.get('unit_id')}: empty suggestion must be null")
    for (field, text), review_ids in field_texts.items():
        if len(review_ids) > 2:
            warnings.append(f"exact {field} reused across {len(review_ids)} reviews: {text[:100]}")
    reference = item["reference_episode_count"]
    reference_path = Path(manifest["source"]) / f"episodes-shard-{item['source_shard']:05d}.jsonl"
    reference_by_review: Counter[str] = Counter()
    for episode in load_jsonl(reference_path):
        source = episode.get("source", {})
        if isinstance(source, dict) and isinstance(source.get("review_id"), str):
            reference_by_review[source["review_id"]] += 1
    for review_id, reference_units in reference_by_review.items():
        compact_units = unit_counts_by_review.get(review_id, 0)
        if reference_units >= 2 and compact_units / reference_units < 0.7:
            warnings.append(
                f"review-level unit yield below 70% of independent detailed reference: "
                f"{review_id} {compact_units}/{reference_units}"
            )
    recall_ratio = total_units / reference if reference else None
    if recall_ratio is not None and recall_ratio < 0.7:
        warnings.append(f"unit yield below 70% of independent detailed reference: {total_units}/{reference}")
    required_report_terms = ("要件達成", "不明瞭点", "裁量補完", "再試行")
    report_text = report.read_text(encoding="utf-8") if report.exists() else ""
    if not report.exists() or not all(term in report_text for term in required_report_terms):
        errors.append("missing empirical self-report sections")
    if re.search(r"\[critical\].*(?:部分的|×)", report_text):
        errors.append("self-report declares a critical requirement partial or failed")
    return {
        "scenario": scenario,
        "review_count": len(rows),
        "unit_count": total_units,
        "reference_episode_count": reference,
        "unit_reference_ratio": round(recall_ratio, 3) if recall_ratio is not None else None,
        "primary_ref_count": primary_refs,
        "wrapper_ref_count": wrapper_refs,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    scenarios = args.scenario or [row["scenario"] for row in manifest["scenarios"]]
    results = [validate(args.directory, scenario) for scenario in scenarios]
    output = {
        "scenario_count": len(results),
        "error_count": sum(row["error_count"] for row in results),
        "warning_count": sum(row["warning_count"] for row in results),
        "scenarios": results,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(1 if output["error_count"] or output["warning_count"] else 0)


if __name__ == "__main__":
    main()
