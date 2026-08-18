"""Build outcome-blind structural profiles from reclassified Episode Lite data."""

from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-reclassification-3135")
DEFAULT_LITE_DIRECTORY = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_ADJUDICATION = Path(
    "data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication.json"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_profile(
    directory: Path,
    lite_directory: Path,
    adjudication_path: Path,
) -> dict[str, Any]:
    rows = load_jsonl(directory / "reclassified-membership.jsonl")
    adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
    cards = {
        row["pattern_id"]: {
            "name": row["recommended_name"],
            "core_logic": row["clarified_core_logic"],
        }
        for row in adjudication["pattern_decisions"]
    }
    source: dict[str, dict[str, Any]] = {}
    for path in sorted(lite_directory.glob("episodes-shard-*.jsonl")):
        for episode in load_jsonl(path):
            source[episode["episode_id"]] = episode
    if set(source) != {row["episode_id"] for row in rows}:
        raise ValueError("source and reclassification coverage differ")

    by_review: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transition_matrix: dict[str, Counter[str]] = defaultdict(Counter)
    pattern_episode_ids: dict[str, set[str]] = defaultdict(set)
    pattern_review_ids: dict[str, set[str]] = defaultdict(set)
    pattern_paper_ids: dict[str, set[str]] = defaultdict(set)
    pattern_fit: dict[str, Counter[str]] = defaultdict(Counter)
    pattern_confidence: dict[str, Counter[str]] = defaultdict(Counter)
    pattern_valence: dict[str, Counter[str]] = defaultdict(Counter)
    pattern_source_audit: Counter[str] = Counter()

    for row in rows:
        by_review[row["review_id"]].append(row)
        by_paper[row["paper_id"]].append(row)
        new_ids = row["new_pattern_ids"] or ["NONE"]
        for old_id in row["old_pattern_ids"] or ["NONE"]:
            transition_matrix[old_id].update(new_ids)
        episode = source[row["episode_id"]]
        valences = {
            claim.get("valence", "unspecified")
            for claim in episode["chain"]["judgments"]
        } or {"unspecified"}
        for membership in row["memberships"]:
            pattern_id = membership["pattern_id"]
            pattern_episode_ids[pattern_id].add(row["episode_id"])
            pattern_review_ids[pattern_id].add(row["review_id"])
            pattern_paper_ids[pattern_id].add(row["paper_id"])
            pattern_fit[pattern_id][membership["fit"]] += 1
            pattern_confidence[pattern_id][membership["confidence"]] += 1
            pattern_valence[pattern_id].update(valences)
            if row["needs_source_audit"]:
                pattern_source_audit[pattern_id] += 1

    episode_overlap: Counter[tuple[str, str]] = Counter()
    review_cooccurrence: Counter[tuple[str, str]] = Counter()
    for row in rows:
        for pair in itertools.combinations(sorted(set(row["new_pattern_ids"])), 2):
            episode_overlap[pair] += 1
    review_logic_counts = []
    review_episode_counts = []
    for review_rows in by_review.values():
        pattern_ids = sorted(
            {pattern_id for row in review_rows for pattern_id in row["new_pattern_ids"]}
        )
        review_logic_counts.append(len(pattern_ids))
        review_episode_counts.append(len(review_rows))
        for pair in itertools.combinations(pattern_ids, 2):
            review_cooccurrence[pair] += 1

    patterns = {}
    for pattern_id in sorted(cards):
        episode_ids = pattern_episode_ids[pattern_id]
        old_ids = {
            row["episode_id"] for row in rows if pattern_id in row["old_pattern_ids"]
        }
        patterns[pattern_id] = {
            **cards[pattern_id],
            "episode_count": len(episode_ids),
            "review_count": len(pattern_review_ids[pattern_id]),
            "paper_count": len(pattern_paper_ids[pattern_id]),
            "fit_counts": dict(sorted(pattern_fit[pattern_id].items())),
            "confidence_counts": dict(sorted(pattern_confidence[pattern_id].items())),
            "judgment_valence_counts": dict(sorted(pattern_valence[pattern_id].items())),
            "source_audit_count": pattern_source_audit[pattern_id],
            "old_new_jaccard": round(
                len(episode_ids & old_ids) / len(episode_ids | old_ids), 6
                if episode_ids | old_ids
                else 0.0,
            ),
        }

    profile = {
        "scope": "ICLR 2026 1,000-review discovery sample",
        "population_prevalence_permitted": False,
        "episodes": len(rows),
        "reviews": len(by_review),
        "papers": len(by_paper),
        "review_structure": {
            "episode_count_mean": round(mean(review_episode_counts), 4),
            "episode_count_median": median(review_episode_counts),
            "distinct_logic_count_mean": round(mean(review_logic_counts), 4),
            "distinct_logic_count_median": median(review_logic_counts),
            "reviews_with_no_confirmed_logic": sum(count == 0 for count in review_logic_counts),
            "reviews_with_multiple_logics": sum(count > 1 for count in review_logic_counts),
            "distinct_logic_count_distribution": dict(sorted(Counter(review_logic_counts).items())),
        },
        "patterns": patterns,
        "old_to_new_transition_matrix": {
            old_id: dict(sorted(counts.items()))
            for old_id, counts in sorted(transition_matrix.items())
        },
        "episode_level_overlaps": [
            {"left": left, "right": right, "episode_count": count}
            for (left, right), count in sorted(
                episode_overlap.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "review_level_cooccurrences": [
            {"left": left, "right": right, "review_count": count}
            for (left, right), count in sorted(
                review_cooccurrence.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }
    (directory / "structure-profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# Reclassified evaluation-logic structure profile",
        "",
        "> Discovery-sample structure only; not an ICLR population prevalence estimate.",
        "",
        "## Review-level structure",
        "",
        f"- Reviews: {profile['reviews']}",
        f"- Mean episodes per review: {profile['review_structure']['episode_count_mean']}",
        f"- Mean confirmed logic types per review: {profile['review_structure']['distinct_logic_count_mean']}",
        f"- Reviews with multiple confirmed logic types: {profile['review_structure']['reviews_with_multiple_logics']}",
        f"- Reviews with no confirmed logic type: {profile['review_structure']['reviews_with_no_confirmed_logic']}",
        "",
        "## Atlas cards after reclassification",
        "",
        "| Pattern | Episodes | Reviews | Core | Variant | Boundary | High | Medium | Low | Source audit | Old/new Jaccard |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pattern_id, row in patterns.items():
        lines.append(
            f"| {pattern_id} {row['name']} | {row['episode_count']} | {row['review_count']} | "
            f"{row['fit_counts'].get('core', 0)} | {row['fit_counts'].get('variant', 0)} | "
            f"{row['fit_counts'].get('boundary', 0)} | {row['confidence_counts'].get('high', 0)} | "
            f"{row['confidence_counts'].get('medium', 0)} | {row['confidence_counts'].get('low', 0)} | "
            f"{row['source_audit_count']} | {row['old_new_jaccard']:.3f} |"
        )
    lines.extend(["", "## Strongest episode-level overlaps", ""])
    for row in profile["episode_level_overlaps"][:15]:
        lines.append(f"- {row['left']} × {row['right']}: {row['episode_count']} episodes")
    lines.extend(["", "## Strongest review-level co-occurrences", ""])
    for row in profile["review_level_cooccurrences"][:15]:
        lines.append(f"- {row['left']} × {row['right']}: {row['review_count']} reviews")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The old/new transition matrix measures ontology revision sensitivity, not model accuracy. Large movement identifies cards whose earlier boundaries were broad or unstable. Episode overlap captures genuinely compound warrants; review-level co-occurrence captures the bundle of evaluative activities performed within one review.",
            "",
        ]
    )
    (directory / "structure-profile.md").write_text("\n".join(lines), encoding="utf-8")
    return profile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--lite-directory", type=Path, default=DEFAULT_LITE_DIRECTORY)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    args = parser.parse_args()
    profile = build_profile(args.directory, args.lite_directory, args.adjudication)
    print(json.dumps(profile, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
