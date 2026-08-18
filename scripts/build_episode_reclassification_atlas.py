"""Aggregate validated Episode Lite reclassification shards without outcomes."""

from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-reclassification-3135")
DEFAULT_LITE_DIRECTORY = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_OLD_MEMBERSHIP = (
    DEFAULT_LITE_DIRECTORY / "synthesis/episode-pattern-membership.jsonl"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def aggregate(
    directory: Path,
    lite_directory: Path,
    old_membership_path: Path,
) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    pattern_ids = manifest["pattern_ids"]
    source_rows: list[dict[str, Any]] = []
    classifications: dict[str, dict[str, Any]] = {}
    for shard in manifest["shards"]:
        shard_number = shard["shard"]
        source_rows.extend(
            load_jsonl(lite_directory / f"episodes-shard-{shard_number:02d}.jsonl")
        )
        classification_path = directory / f"classification-shard-{shard_number:03d}.jsonl"
        if not classification_path.exists():
            raise ValueError(f"missing classification shard: {shard_number:03d}")
        for row in load_jsonl(classification_path):
            episode_id = row["episode_id"]
            if episode_id in classifications:
                raise ValueError(f"duplicate classification: {episode_id}")
            classifications[episode_id] = row

    source_ids = [row["episode_id"] for row in source_rows]
    if set(source_ids) != set(classifications) or len(source_ids) != len(classifications):
        raise ValueError("source and classification episode coverage differ")
    old = {
        row["episode_id"]: row["pattern_ids"]
        for row in load_jsonl(old_membership_path)
    }
    if set(source_ids) != set(old):
        raise ValueError("source and old membership episode coverage differ")

    enriched: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    fit_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    pattern_new: dict[str, set[str]] = defaultdict(set)
    pattern_old: dict[str, set[str]] = defaultdict(set)
    pattern_uncertain: dict[str, set[str]] = defaultdict(set)
    overlap_counts: Counter[tuple[str, str]] = Counter()
    transition_counts: Counter[str] = Counter()

    for source in source_rows:
        episode_id = source["episode_id"]
        result = classifications[episode_id]
        old_ids = sorted(old[episode_id])
        new_ids = sorted(member["pattern_id"] for member in result["memberships"])
        uncertain_ids = sorted(row["pattern_id"] for row in result["uncertain_patterns"])
        for pattern_id in old_ids:
            pattern_old[pattern_id].add(episode_id)
        for member in result["memberships"]:
            pattern_new[member["pattern_id"]].add(episode_id)
            fit_counts[member["fit"]] += 1
        for pattern_id in uncertain_ids:
            pattern_uncertain[pattern_id].add(episode_id)
        for pair in itertools.combinations(new_ids, 2):
            overlap_counts[pair] += 1
        confidence_counts[result["classification_confidence"]] += 1

        if old_ids == new_ids:
            transition = "unchanged"
        elif not new_ids:
            transition = "no_confirmed_membership"
        elif not old_ids:
            transition = "newly_mapped"
        else:
            transition = "changed"
        transition_counts[transition] += 1

        record = {
            "episode_id": episode_id,
            "paper_id": source["source"]["paper_id"],
            "review_id": source["source"]["review_id"],
            "old_pattern_ids": old_ids,
            "new_pattern_ids": new_ids,
            "transition": transition,
            "memberships": result["memberships"],
            "uncertain_patterns": result["uncertain_patterns"],
            "closest_excluded_patterns": result["closest_excluded_patterns"],
            "unmapped_logic": result["unmapped_logic"],
            "classification_confidence": result["classification_confidence"],
            "needs_source_audit": result["needs_source_audit"],
        }
        enriched.append(record)
        if result["unmapped_logic"]:
            unmapped.append(
                {
                    **record,
                    "abstract_signature": source["signatures"]["abstract"],
                    "concrete_signature": source["signatures"]["concrete"],
                }
            )
        if result["uncertain_patterns"]:
            uncertain.append(record)

    pattern_summary = {}
    for pattern_id in pattern_ids:
        old_set = pattern_old[pattern_id]
        new_set = pattern_new[pattern_id]
        pattern_summary[pattern_id] = {
            "old_episode_count": len(old_set),
            "new_episode_count": len(new_set),
            "retained_episode_count": len(old_set & new_set),
            "removed_episode_count": len(old_set - new_set),
            "added_episode_count": len(new_set - old_set),
            "uncertain_episode_count": len(pattern_uncertain[pattern_id]),
            "new_review_count": len(
                {row["review_id"] for row in enriched if pattern_id in row["new_pattern_ids"]}
            ),
            "new_paper_count": len(
                {row["paper_id"] for row in enriched if pattern_id in row["new_pattern_ids"]}
            ),
        }

    overlaps = [
        {
            "left_pattern_id": left,
            "right_pattern_id": right,
            "episode_count": count,
            "jaccard": round(
                count / len(pattern_new[left] | pattern_new[right]), 6
            ),
        }
        for (left, right), count in sorted(overlap_counts.items())
    ]
    summary = {
        "scope": "ICLR 2026 1,000-review discovery sample",
        "population_prevalence_permitted": False,
        "episode_count": len(enriched),
        "review_count": len({row["review_id"] for row in enriched}),
        "paper_count": len({row["paper_id"] for row in enriched}),
        "confirmed_membership_count": sum(len(row["memberships"]) for row in enriched),
        "episodes_with_confirmed_membership": sum(bool(row["memberships"]) for row in enriched),
        "multi_label_episode_count": sum(len(row["memberships"]) > 1 for row in enriched),
        "uncertain_episode_count": len(uncertain),
        "unmapped_episode_count": len(unmapped),
        "source_audit_episode_count": sum(row["needs_source_audit"] for row in enriched),
        "classification_confidence_counts": dict(sorted(confidence_counts.items())),
        "membership_fit_counts": dict(sorted(fit_counts.items())),
        "transition_counts": dict(sorted(transition_counts.items())),
        "patterns": pattern_summary,
        "pairwise_overlaps": overlaps,
    }

    write_jsonl(directory / "reclassified-membership.jsonl", enriched)
    write_jsonl(directory / "unmapped-candidates.jsonl", unmapped)
    write_jsonl(directory / "uncertain-candidates.jsonl", uncertain)
    (directory / "reclassification-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Episode reclassification transition report",
        "",
        "> Selected-sample structural counts only. These are not ICLR population prevalence estimates.",
        "",
        "## Coverage",
        "",
        f"- Episodes: {summary['episode_count']}",
        f"- Reviews: {summary['review_count']}",
        f"- Papers: {summary['paper_count']}",
        f"- Confirmed memberships: {summary['confirmed_membership_count']}",
        f"- Multi-label episodes: {summary['multi_label_episode_count']}",
        f"- Uncertain episodes: {summary['uncertain_episode_count']}",
        f"- Unmapped episodes: {summary['unmapped_episode_count']}",
        "",
        "## Old-to-new structure",
        "",
        *[
            f"- {key}: {value}"
            for key, value in sorted(summary["transition_counts"].items())
        ],
        "",
        "## Pattern support in the discovery sample",
        "",
        "| Pattern | Old | New | Retained | Removed | Added | Uncertain | Reviews | Papers |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pattern_id in pattern_ids:
        row = pattern_summary[pattern_id]
        lines.append(
            f"| {pattern_id} | {row['old_episode_count']} | {row['new_episode_count']} | "
            f"{row['retained_episode_count']} | {row['removed_episode_count']} | "
            f"{row['added_episode_count']} | {row['uncertain_episode_count']} | "
            f"{row['new_review_count']} | {row['new_paper_count']} |"
        )
    lines.extend(["", "## Pairwise overlap", ""])
    if overlaps:
        lines.extend(
            f"- {row['left_pattern_id']} × {row['right_pattern_id']}: "
            f"{row['episode_count']} episodes (Jaccard {row['jaccard']:.3f})"
            for row in sorted(overlaps, key=lambda row: (-row["episode_count"], row["left_pattern_id"], row["right_pattern_id"]))
        )
    else:
        lines.append("- No confirmed multi-label overlap.")
    lines.extend(
        [
            "",
            "## Next analysis",
            "",
            "Cluster `unmapped-candidates.jsonl` by operative inference endpoint, then challenge candidate new cards against the retained ten-card Atlas. Treat uncertainty and source-audit cases separately from genuine Atlas-external logic.",
            "",
        ]
    )
    (directory / "transition-report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--lite-directory", type=Path, default=DEFAULT_LITE_DIRECTORY)
    parser.add_argument("--old-membership", type=Path, default=DEFAULT_OLD_MEMBERSHIP)
    args = parser.parse_args()
    summary = aggregate(args.directory, args.lite_directory, args.old_membership)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
