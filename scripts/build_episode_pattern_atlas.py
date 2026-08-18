"""Derive Atlas support, memberships, and Deep candidates from validated IDs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_PILOT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_SYNTHESIS_DIR = DEFAULT_PILOT_DIR / "synthesis"


def load_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for path in paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build(pilot_dir: Path, synthesis_dir: Path) -> dict[str, Any]:
    source_manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
    final_manifest = json.loads((synthesis_dir / "final-manifest.json").read_text(encoding="utf-8"))
    pattern_map = json.loads((synthesis_dir / "final-pattern-map.json").read_text(encoding="utf-8"))
    episodes = load_jsonl(sorted(pilot_dir.glob("episodes-shard-*.jsonl")))
    episode_by_id = {episode["episode_id"]: episode for episode in episodes}
    if len(episode_by_id) != len(episodes):
        raise ValueError("duplicate episode IDs in Episode Lite inputs")
    review_metadata = {
        row["candidate"]["review_id"]: row["candidate"] for row in source_manifest["reviews"]
    }
    all_papers = {row["candidate"]["paper_id"] for row in source_manifest["reviews"]}
    bucket_review_denominators = Counter(
        row["candidate"]["decision_bucket"] for row in source_manifest["reviews"]
    )
    bucket_paper_denominators: dict[str, set[str]] = defaultdict(set)
    for row in source_manifest["reviews"]:
        bucket_paper_denominators[row["candidate"]["decision_bucket"]].add(
            row["candidate"]["paper_id"]
        )

    memberships: dict[str, set[str]] = defaultdict(set)
    enriched_patterns = []
    candidate_roles: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"patterns": set(), "roles": set()}
    )
    for pattern in pattern_map["patterns"]:
        member_ids = sorted(
            {
                episode_id
                for meta_id in pattern["source_meta_pattern_ids"]
                for episode_id in final_manifest["meta_patterns"][meta_id]["member_episode_ids"]
            }
        )
        member_episodes = [episode_by_id[episode_id] for episode_id in member_ids]
        review_ids = {episode["source"]["review_id"] for episode in member_episodes}
        paper_ids = {episode["source"]["paper_id"] for episode in member_episodes}
        bucket_reviews: dict[str, set[str]] = defaultdict(set)
        bucket_papers: dict[str, set[str]] = defaultdict(set)
        valences = Counter()
        claim_statuses = Counter()
        missing_links = Counter()
        provenance_levels = Counter()
        for episode in member_episodes:
            episode_id = episode["episode_id"]
            memberships[episode_id].add(pattern["pattern_id"])
            review_id = episode["source"]["review_id"]
            bucket = review_metadata[review_id]["decision_bucket"]
            bucket_reviews[bucket].add(review_id)
            bucket_papers[bucket].add(episode["source"]["paper_id"])
            for judgment in episode["chain"]["judgments"]:
                valences[judgment["valence"]] += 1
            for field in episode["chain"].values():
                for claim in field:
                    claim_statuses[claim["status"]] += 1
            missing_links.update(episode["quality"]["missing_links"])
            provenance_levels.update(
                source["provenance_level"] for source in episode["evidence"].values()
            )

        bucket_support = {}
        for bucket in sorted(bucket_review_denominators):
            bucket_support[bucket] = {
                "reviews": len(bucket_reviews[bucket]),
                "review_denominator": bucket_review_denominators[bucket],
                "review_share": len(bucket_reviews[bucket]) / bucket_review_denominators[bucket],
                "papers": len(bucket_papers[bucket]),
                "paper_denominator": len(bucket_paper_denominators[bucket]),
                "paper_share": len(bucket_papers[bucket]) / len(bucket_paper_denominators[bucket]),
            }

        enriched = {
            **pattern,
            "derived_support": {
                "episodes": len(member_ids),
                "reviews": len(review_ids),
                "review_denominator": len(source_manifest["reviews"]),
                "review_share": len(review_ids) / len(source_manifest["reviews"]),
                "papers": len(paper_ids),
                "paper_denominator": len(all_papers),
                "paper_share": len(paper_ids) / len(all_papers),
                "decision_buckets": bucket_support,
                "judgment_valences": dict(sorted(valences.items())),
                "claim_statuses": dict(sorted(claim_statuses.items())),
                "missing_links": dict(sorted(missing_links.items())),
                "provenance_levels": dict(sorted(provenance_levels.items())),
            },
            "member_episode_ids": member_ids,
        }
        enriched_patterns.append(enriched)

        role_fields = (
            ("representative", pattern["representative_episode_ids"][:3]),
            ("boundary", pattern["boundary_episode_ids"][:2]),
            ("counterexample", pattern["counterexample_episode_ids"][:2]),
        )
        for role, episode_ids in role_fields:
            for episode_id in episode_ids:
                candidate_roles[episode_id]["patterns"].add(pattern["pattern_id"])
                candidate_roles[episode_id]["roles"].add(role)
        ranked = sorted(
            member_episodes,
            key=lambda episode: (
                episode["quality"]["extraction_confidence"], episode["episode_id"]
            ),
        )
        for episode in ranked[:2]:
            candidate_roles[episode["episode_id"]]["patterns"].add(pattern["pattern_id"])
            candidate_roles[episode["episode_id"]]["roles"].add("low_extraction_confidence")
        incomplete = [episode for episode in ranked if episode["quality"]["missing_links"]]
        if incomplete:
            candidate_roles[incomplete[0]["episode_id"]]["patterns"].add(pattern["pattern_id"])
            candidate_roles[incomplete[0]["episode_id"]]["roles"].add("incomplete_chain")

    membership_rows = [
        {"episode_id": episode_id, "pattern_ids": sorted(pattern_ids)}
        for episode_id, pattern_ids in sorted(memberships.items())
    ]
    missing_membership = sorted(set(episode_by_id) - set(memberships))
    if missing_membership:
        raise ValueError(f"episodes without Atlas membership: {missing_membership}")
    duplicate_episode_count = sum(len(row["pattern_ids"]) > 1 for row in membership_rows)
    derived = {
        "version": 1,
        "scope": {
            "conference": "ICLR",
            "year": 2026,
            "sample_reviews": len(source_manifest["reviews"]),
            "sample_papers": len(all_papers),
            "episodes": len(episodes),
            "zero_episode_reviews": len(source_manifest["reviews"])
            - len({episode["source"]["review_id"] for episode in episodes}),
            "sampling_warning": "Stratified discovery pilot; support is not ICLR population prevalence.",
        },
        "patterns": enriched_patterns,
        "global_contrasts": pattern_map["global_contrasts"],
        "limitations": pattern_map["limitations"],
        "membership_summary": {
            "episodes_with_membership": len(membership_rows),
            "episodes_with_multiple_patterns": duplicate_episode_count,
            "mean_patterns_per_episode": sum(len(row["pattern_ids"]) for row in membership_rows)
            / len(membership_rows),
        },
    }
    (synthesis_dir / "final-pattern-atlas.json").write_text(
        json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (synthesis_dir / "episode-pattern-membership.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in membership_rows),
        encoding="utf-8",
    )
    deep_candidates = {
        "version": 1,
        "selection_policy": {
            "per_pattern": "up to 3 representatives, 2 boundaries, 2 counterexamples, 2 lowest-confidence members, and 1 incomplete chain",
            "deduplication": "episode-level; roles and pattern IDs are accumulated",
        },
        "candidates": [
            {
                "episode_id": episode_id,
                "pattern_ids": sorted(values["patterns"]),
                "roles": sorted(values["roles"]),
                "review_id": episode_by_id[episode_id]["source"]["review_id"],
                "paper_id": episode_by_id[episode_id]["source"]["paper_id"],
                "extraction_confidence": episode_by_id[episode_id]["quality"]["extraction_confidence"],
                "missing_links": episode_by_id[episode_id]["quality"]["missing_links"],
            }
            for episode_id, values in sorted(candidate_roles.items())
        ],
    }
    (synthesis_dir / "deep-candidates.json").write_text(
        json.dumps(deep_candidates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "patterns": len(enriched_patterns),
        "memberships": len(membership_rows),
        "multi_label_episodes": duplicate_episode_count,
        "deep_candidates": len(deep_candidates["candidates"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--synthesis-dir", type=Path, default=DEFAULT_SYNTHESIS_DIR)
    args = parser.parse_args()
    print(json.dumps(build(args.pilot_dir, args.synthesis_dir)))


if __name__ == "__main__":
    main()
