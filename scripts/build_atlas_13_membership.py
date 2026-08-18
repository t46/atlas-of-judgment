"""Join the adjudicated ten-card Atlas with the three-card exhaustive screen."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-reclassification-3135")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 0.0


def build(directory: Path) -> dict[str, Any]:
    old_rows = load_jsonl(directory / "reclassified-membership.jsonl")
    screening = directory / "new-card-screening"
    manifest = json.loads((screening / "manifest.json").read_text(encoding="utf-8"))
    screen_rows: list[dict[str, Any]] = []
    for shard in [row["shard"] for row in manifest["shards"]]:
        screen_rows.extend(load_jsonl(screening / f"screen-shard-{shard:03d}.jsonl"))
    old_ids = [row["episode_id"] for row in old_rows]
    screen_ids = [row["episode_id"] for row in screen_rows]
    if old_ids != screen_ids:
        raise ValueError("existing and new-card episode IDs/order differ")
    if len(old_ids) != 3135 or len(set(old_ids)) != 3135:
        raise ValueError("expected 3,135 unique episodes")

    combined: list[dict[str, Any]] = []
    pattern_episodes: dict[str, set[str]] = defaultdict(set)
    pattern_reviews: dict[str, set[str]] = defaultdict(set)
    pattern_papers: dict[str, set[str]] = defaultdict(set)
    fit_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    new_fit_counts: Counter[str] = Counter()
    new_confidence_counts: Counter[str] = Counter()
    episodes_with_existing = episodes_with_new = episodes_with_any = 0
    new_only_count = old_unmapped_covered = old_unmapped_total = 0
    old_no_confirmed_covered = 0
    new_multi_count = all_multi_count = 0
    uncertain_new_count = source_audit_count = 0

    for old, screen in zip(old_rows, screen_rows, strict=True):
        existing = old["memberships"]
        new = screen["new_memberships"]
        memberships = existing + [
            {
                "pattern_id": item["card_id"],
                **{key: value for key, value in item.items() if key != "card_id"},
            }
            for item in new
        ]
        pattern_ids = [item["pattern_id"] for item in memberships]
        if len(pattern_ids) != len(set(pattern_ids)):
            raise ValueError(f"duplicate card membership for {old['episode_id']}")
        for membership in memberships:
            pattern_id = membership["pattern_id"]
            pattern_episodes[pattern_id].add(old["episode_id"])
            pattern_reviews[pattern_id].add(old["review_id"])
            pattern_papers[pattern_id].add(old["paper_id"])
            fit_counts[membership["fit"]] += 1
            confidence_counts[membership["confidence"]] += 1
        for membership in new:
            new_fit_counts[membership["fit"]] += 1
            new_confidence_counts[membership["confidence"]] += 1
        existing_flag, new_flag = bool(existing), bool(new)
        episodes_with_existing += existing_flag
        episodes_with_new += new_flag
        episodes_with_any += existing_flag or new_flag
        new_only_count += new_flag and not existing_flag
        new_multi_count += len(new) > 1
        all_multi_count += len(memberships) > 1
        uncertain_new_count += bool(screen["uncertain_new_cards"])
        audit = old["needs_source_audit"] or screen["needs_source_audit"]
        source_audit_count += audit
        if old["unmapped_logic"] is not None:
            old_unmapped_total += 1
            old_unmapped_covered += new_flag
        old_no_confirmed_covered += new_flag and not existing_flag
        combined.append({
            "episode_id": old["episode_id"],
            "paper_id": old["paper_id"],
            "review_id": old["review_id"],
            "pattern_ids": pattern_ids,
            "memberships": memberships,
            "existing_pattern_ids": [item["pattern_id"] for item in existing],
            "new_pattern_ids": [item["card_id"] for item in new],
            "uncertain_existing_patterns": old["uncertain_patterns"],
            "uncertain_new_cards": screen["uncertain_new_cards"],
            "closest_excluded_existing_patterns": old["closest_excluded_patterns"],
            "closest_excluded_new_cards": screen["closest_excluded_new_cards"],
            "prior_unmapped_logic": old["unmapped_logic"],
            "existing_classification_confidence": old["classification_confidence"],
            "new_screen_confidence": screen["screen_confidence"],
            "needs_source_audit": audit,
        })

    output = directory / "atlas-13-membership.jsonl"
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined),
        encoding="utf-8",
    )
    all_ids = [f"A-P{i:02d}" for i in range(1, 11)] + [f"N-P{i:02d}" for i in range(1, 4)]
    patterns = {
        pattern_id: {
            "episode_count": len(pattern_episodes[pattern_id]),
            "review_count": len(pattern_reviews[pattern_id]),
            "paper_count": len(pattern_papers[pattern_id]),
        }
        for pattern_id in all_ids
    }
    overlaps = []
    for left, right in combinations(all_ids, 2):
        intersection = pattern_episodes[left] & pattern_episodes[right]
        if not intersection:
            continue
        overlaps.append({
            "left_pattern_id": left,
            "right_pattern_id": right,
            "episode_count": len(intersection),
            "jaccard": jaccard(pattern_episodes[left], pattern_episodes[right]),
        })
    new_old_overlaps = [
        row for row in overlaps
        if row["left_pattern_id"].startswith("A-") and row["right_pattern_id"].startswith("N-")
    ]
    summary = {
        "scope": "ICLR 2026 1,000-review discovery sample",
        "population_prevalence_permitted": False,
        "atlas_status": "candidate 13-card extension pending challenge audit",
        "episode_count": len(combined),
        "review_count": len({row["review_id"] for row in combined}),
        "paper_count": len({row["paper_id"] for row in combined}),
        "confirmed_membership_count": sum(len(row["memberships"]) for row in combined),
        "episodes_with_existing_membership": episodes_with_existing,
        "episodes_with_new_membership": episodes_with_new,
        "episodes_with_any_membership": episodes_with_any,
        "episodes_newly_covered_by_new_cards": new_only_count,
        "old_unmapped_episode_count": old_unmapped_total,
        "old_unmapped_covered_by_new_cards": old_unmapped_covered,
        "new_multi_label_episode_count": new_multi_count,
        "all_multi_label_episode_count": all_multi_count,
        "uncertain_new_card_episode_count": uncertain_new_count,
        "source_audit_episode_count": source_audit_count,
        "membership_fit_counts": dict(sorted(fit_counts.items())),
        "membership_confidence_counts": dict(sorted(confidence_counts.items())),
        "new_membership_fit_counts": dict(sorted(new_fit_counts.items())),
        "new_membership_confidence_counts": dict(sorted(new_confidence_counts.items())),
        "patterns": patterns,
        "pairwise_overlaps": overlaps,
        "new_existing_overlaps": sorted(
            new_old_overlaps, key=lambda row: (-row["episode_count"], row["left_pattern_id"], row["right_pattern_id"])
        ),
    }
    (directory / "atlas-13-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    top_overlaps = summary["new_existing_overlaps"][:12]
    report = [
        "# Candidate 13-card Atlas membership summary", "",
        "> This is a 1,000-review discovery sample. Population prevalence is not permitted.", "",
        f"- Episodes: {summary['episode_count']:,}",
        f"- Reviews: {summary['review_count']:,}",
        f"- Confirmed memberships: {summary['confirmed_membership_count']:,}",
        f"- Episodes with any of 13 cards: {summary['episodes_with_any_membership']:,}",
        f"- Episodes with a new card: {summary['episodes_with_new_membership']:,}",
        f"- Episodes newly covered by new cards: {summary['episodes_newly_covered_by_new_cards']:,}",
        f"- Prior unmapped episodes covered: {summary['old_unmapped_covered_by_new_cards']:,} / {summary['old_unmapped_episode_count']:,}",
        "", "## New cards", "", "| Card | Episodes | Reviews | Papers |", "|---|---:|---:|---:|",
    ]
    for pattern_id in ("N-P01", "N-P02", "N-P03"):
        row = patterns[pattern_id]
        report.append(f"| {pattern_id} | {row['episode_count']:,} | {row['review_count']:,} | {row['paper_count']:,} |")
    report.extend(["", "## Largest new-to-existing overlaps", "", "| Existing | New | Episodes | Jaccard |", "|---|---|---:|---:|"])
    for row in top_overlaps:
        report.append(
            f"| {row['left_pattern_id']} | {row['right_pattern_id']} | {row['episode_count']:,} | {row['jaccard']:.3f} |"
        )
    report.extend([
        "", "## Interpretation guardrail", "",
        "These counts describe the discovery sample and screening behavior. N-P02 positives are a pre-registered challenge target; the three cards remain candidates until independent boundary audits are complete.", "",
    ])
    (directory / "atlas-13-report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    summary = build(args.directory)
    print(json.dumps({
        "episodes": summary["episode_count"],
        "memberships": summary["confirmed_membership_count"],
        "new_covered": summary["episodes_with_new_membership"],
        "newly_covered": summary["episodes_newly_covered_by_new_cards"],
        "old_unmapped_covered": summary["old_unmapped_covered_by_new_cards"],
    }))


if __name__ == "__main__":
    main()
