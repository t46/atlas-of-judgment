"""Prepare blind, stratified packets for discovering logic outside the Atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_RECLASSIFICATION = Path("data/analysis/iclr/episode-reclassification-3135")
DEFAULT_LITE = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_ADJUDICATION = Path(
    "data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication.json"
)
DEFAULT_OUTPUT = DEFAULT_RECLASSIFICATION / "unmapped-discovery"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _compact_claim(claim: dict[str, Any]) -> dict[str, Any]:
    result = {"text": claim["text"], "status": claim["status"]}
    for key in ("valence", "discriminating_role"):
        if key in claim:
            result[key] = claim[key]
    return result


def compact_episode(episode: dict[str, Any]) -> dict[str, Any]:
    chain = episode["chain"]
    return {
        "episode_id": episode["episode_id"],
        "paper_id": episode["source"]["paper_id"],
        "review_id": episode["source"]["review_id"],
        **{
            key: [_compact_claim(claim) for claim in chain[key]]
            for key in (
                "inspected_objects", "observations", "reasoning_bridge",
                "judgments", "requested_tests_or_changes",
            )
        },
        "concrete_signature": episode["signatures"]["concrete"],
        "abstract_signature": episode["signatures"]["abstract"],
        "missing_links": episode["quality"]["missing_links"],
        "extraction_confidence": episode["quality"]["extraction_confidence"],
        "provenance_levels": sorted(
            {value["provenance_level"] for value in episode["evidence"].values()}
        ),
    }


def protocol() -> str:
    return """# Unmapped evaluation-logic discovery protocol

Analyze every record in one `source-group-NN.md`. These episodes were not
confirmed under the current ten-card Atlas. Some contain genuinely missing
evaluation logics; others are Atlas boundary cases or source-insufficient
wrappers. Do not assume that every record represents a new pattern.

Write exactly `local-patterns-NN.json` and `local-report-NN.md`. The JSON has:

- `group`;
- `candidate_patterns`;
- `assignments`: exactly one object per source episode, in source order;
- `coverage_notes`.

Each assignment contains `episode_id`, `disposition`,
`candidate_pattern_ids`, `nearest_atlas_pattern_ids`, `reason`, and
`confidence`. Allowed dispositions are:

- `candidate_pattern`: recurring logic outside the Atlas;
- `atlas_boundary`: a known card is plausible but its gate is not recoverable
  or the case exposes a useful boundary rather than a new logic;
- `source_insufficient`: provenance/bridge/endpoint is too weak to infer logic;
- `singleton_new_logic`: coherent Atlas-external logic with no local recurrence.

Candidate IDs must be `U-NN-PNN`. Every candidate pattern contains:

- `candidate_pattern_id`, `provisional_name`;
- `chain_template`: specific strings for `inspected_object`, `observation`,
  `evaluative_standard`, `reasoning_bridge`, `judgment`, and `repair_role`;
- `inclusion_rule`, `exclusion_rule`;
- `member_episode_ids`, `representative_episode_ids`,
  `boundary_episode_ids`, `counterexample_episode_ids`;
- `nearest_atlas_patterns`: objects with `pattern_id` and `decisive_difference`;
- `variants`, `confidence`, and `notes`.

Discover patterns by the operative inference endpoint: what is inspected, what
is observed, which standard makes it matter, how that warrants a judgment, and
what evidence or change would resolve it. Do not organize by paper topic,
method family, positive/negative tone, request wording, or generic labels such
as novelty, clarity, significance, or evidence gap.

A recurring candidate requires at least three member episodes from at least
two reviews. Multi-label candidate membership is allowed only for independently
stateable endpoints. A singleton must not be inflated into a recurring pattern.
Do not route an analytic wrapper to A-P08 merely because it says evidence is
insufficient. `source_insufficient` is not a negative judgment and is not a new
logic. Existing Atlas cards are contrasts, not a requirement to force fit.

For each candidate, state the decisive difference from its nearest Atlas card.
If no difference survives a counterexample, use `atlas_boundary`. Reports must
explain recurring candidates, source-insufficient mass, singleton logics,
nearest-card boundaries, and likely merge/split errors. Counts are selected-
sample support only, never population prevalence.
"""


def prepare(
    reclassification: Path,
    lite: Path,
    adjudication_path: Path,
    output: Path,
    group_count: int = 8,
) -> dict[str, Any]:
    unmapped = load_jsonl(reclassification / "unmapped-candidates.jsonl")
    by_id = {row["episode_id"]: row for row in unmapped}
    source = {}
    for path in sorted(lite.glob("episodes-shard-*.jsonl")):
        for row in load_jsonl(path):
            if row["episode_id"] in by_id:
                source[row["episode_id"]] = row
    if set(source) != set(by_id):
        raise ValueError("unmapped/source coverage differs")
    adjudication = json.loads(adjudication_path.read_text())
    cards = [
        {
            "pattern_id": row["pattern_id"],
            "name": row["recommended_name"],
            "core_logic": row["clarified_core_logic"],
            "inclusion_rule": row["inclusion_rule"],
            "exclusion_rule": row["exclusion_rule"],
        }
        for row in adjudication["pattern_decisions"]
    ]

    review_groups: dict[str, list[str]] = defaultdict(list)
    for episode_id, row in by_id.items():
        review_groups[row["review_id"]].append(episode_id)
    buckets: list[list[str]] = [[] for _ in range(group_count)]
    bucket_weights = [0] * group_count
    ordered_reviews = sorted(
        review_groups,
        key=lambda review_id: (
            -len(review_groups[review_id]),
            hashlib.sha256(review_id.encode()).hexdigest(),
        ),
    )
    for review_id in ordered_reviews:
        episode_ids = sorted(review_groups[review_id])
        # Prefer a small bucket, alternating generic/audited groups across buckets.
        target = min(range(group_count), key=lambda index: (bucket_weights[index], index))
        buckets[target].extend(episode_ids)
        bucket_weights[target] += sum(
            len(json.dumps(compact_episode(source[episode_id]), ensure_ascii=False))
            for episode_id in episode_ids
        )

    output.mkdir(parents=True, exist_ok=True)
    groups = []
    for group, episode_ids in enumerate(buckets, 1):
        records = []
        for episode_id in episode_ids:
            compact = compact_episode(source[episode_id])
            prior = by_id[episode_id]
            compact["current_unmapped_assessment"] = prior["unmapped_logic"]
            compact["closest_excluded_patterns"] = prior["closest_excluded_patterns"]
            compact["classification_confidence"] = prior["classification_confidence"]
            compact["needs_source_audit"] = prior["needs_source_audit"]
            records.append(compact)
        lines = [
            f"# Unmapped logic discovery group {group:02d}",
            "",
            f"Episodes: {len(records)}; reviews: {len({row['review_id'] for row in records})}",
            "",
            "## Current Atlas cards",
            "",
            "```json",
            json.dumps(cards, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Unmapped Episode Lite records",
            "",
            "```jsonl",
            *[json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in records],
            "```",
            "",
        ]
        rendered = "\n".join(lines)
        path = output / f"source-group-{group:02d}.md"
        path.write_text(rendered)
        groups.append(
            {
                "group": group,
                "episode_ids": episode_ids,
                "episode_count": len(episode_ids),
                "review_count": len({by_id[x]["review_id"] for x in episode_ids}),
                "source_characters": len(rendered),
            }
        )
    manifest = {
        "version": 1,
        "source": str(reclassification / "unmapped-candidates.jsonl"),
        "group_count": group_count,
        "episode_count": len(unmapped),
        "outcome_blind": True,
        "groups": groups,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "DISCOVERY_PROTOCOL.md").write_text(protocol())
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reclassification", type=Path, default=DEFAULT_RECLASSIFICATION)
    parser.add_argument("--lite", type=Path, default=DEFAULT_LITE)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--group-count", type=int, default=8)
    args = parser.parse_args()
    result = prepare(
        args.reclassification, args.lite, args.adjudication, args.output, args.group_count
    )
    print(json.dumps({"groups": result["group_count"], "episodes": result["episode_count"]}))


if __name__ == "__main__":
    main()
