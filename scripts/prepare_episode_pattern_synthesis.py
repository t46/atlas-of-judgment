"""Prepare compact ten-shard packets for inductive Episode Lite synthesis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_PILOT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_OUTPUT_DIR = DEFAULT_PILOT_DIR / "synthesis"
DEFAULT_SHARDS_PER_GROUP = 10


def compact_claim(claim: dict[str, Any]) -> dict[str, Any]:
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
        "inspected_objects": [
            compact_claim(claim) for claim in chain["inspected_objects"]
        ],
        "observations": [compact_claim(claim) for claim in chain["observations"]],
        "reasoning_bridge": [
            compact_claim(claim) for claim in chain["reasoning_bridge"]
        ],
        "judgments": [compact_claim(claim) for claim in chain["judgments"]],
        "requested_tests_or_changes": [
            compact_claim(claim) for claim in chain["requested_tests_or_changes"]
        ],
        "concrete_signature": episode["signatures"]["concrete"],
        "abstract_signature": episode["signatures"]["abstract"],
        "missing_links": episode["quality"]["missing_links"],
        "extraction_confidence": episode["quality"]["extraction_confidence"],
        "provenance_levels": sorted(
            {source["provenance_level"] for source in episode["evidence"].values()}
        ),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def protocol() -> str:
    return """# Protocol for fresh-agent group pattern synthesis

Analyze one `group-source-NN.md` and write exactly:

1. `group-patterns-NN.json`
2. `group-report-NN.md`

The JSON object must contain `group`, `patterns`, `unassigned_episode_ids`,
`outlier_episode_ids`, and `coverage_notes`. Each pattern must contain:

- `pattern_id`: `GNN-PNN`
- `provisional_name`
- `logic`: `inspected_object_types`, `observation_forms`,
  `evaluative_standard`, `reasoning_template`, `judgment_forms`, and
  `request_roles`
- `inclusion_rule` and `exclusion_rule`
- `member_episode_ids`, `representative_episode_ids`,
  `boundary_episode_ids`, and `counterexample_episode_ids`
- `confusable_with`, `confidence` (`low`, `medium`, or `high`), and `notes`

Patterns are provisional, overlapping evaluation-logics—not paper topics,
method families, weakness labels, or decision buckets. A pattern must preserve
what is inspected, what is observed, the standard or counterfactual that makes
it matter, the inference to judgment, and the role of any requested evidence.
Do not merge episodes merely because both ask for an experiment. Keep apart the
same observation used under different standards and the same judgment reached
through different warrants.

Every source episode must appear in at least one `member_episode_ids` list or
exactly one of `unassigned_episode_ids` and `outlier_episode_ids`. Membership
may be multi-label. A recurring pattern needs at least three episodes from at
least two reviews. Use outliers for coherent but nonrecurring logic and
unassigned for insufficient or ambiguous chains. Representatives and boundary
cases must be members; counterexamples may be any source episode. Do not invent
episode IDs.

Produce 6–12 discriminative patterns. No pattern may be an umbrella, coverage
scaffold, generic "claim-to-evidence" chain, or contain more than 65% of the
group's episodes. Different patterns must have materially different standards,
reasoning templates, and memberships; do not copy the same logic or episode set
under multiple names. Keep outliers at or below 20% and unassigned episodes at
or below 10%. Each pattern uses lists of concrete strings for
`inspected_object_types`, `observation_forms`, `judgment_forms`, and
`request_roles`; `evaluative_standard` and `reasoning_template` are specific
strings. Give each pattern 1–5 representatives and at most 5 boundary examples
and 5 counterexamples. `coverage_notes` and each pattern's `notes` are lists of
strings.

The Markdown report should explain the strongest recurring logics, important
variants, same-observation/different-judgment contrasts, same-judgment/different-
warrant contrasts, boundary cases, outliers, and likely merge/split errors.
Treat local shard notes as fallible leads. Base membership on the compact
episodes. Counts are local support only, never population prevalence.
"""


def prepare(
    pilot_dir: Path,
    output_dir: Path,
    *,
    shards_per_group: int = DEFAULT_SHARDS_PER_GROUP,
) -> dict[str, Any]:
    manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
    shard_count = manifest["shard_count"]
    if shards_per_group < 1 or shard_count % shards_per_group:
        raise ValueError("shards_per_group must divide the manifest shard count")

    output_dir.mkdir(parents=True, exist_ok=True)
    groups = []
    for group_index, start in enumerate(range(1, shard_count + 1, shards_per_group), 1):
        shards = list(range(start, start + shards_per_group))
        episodes = [
            compact_episode(episode)
            for shard in shards
            for episode in load_jsonl(
                pilot_dir / f"episodes-shard-{shard:02d}.jsonl"
            )
        ]
        review_count = len({episode["review_id"] for episode in episodes})
        paper_count = len({episode["paper_id"] for episode in episodes})
        lines = [
            f"# Episode pattern synthesis group {group_index:02d}",
            "",
            f"Shards: {shards[0]}-{shards[-1]}",
            f"Episodes: {len(episodes)}; reviews with episodes: {review_count}; papers: {paper_count}",
            "",
            "## Compact Episode Lite records",
            "",
            "```jsonl",
            *[
                json.dumps(episode, ensure_ascii=False, separators=(",", ":"))
                for episode in episodes
            ],
            "```",
            "",
            "## Fallible local pattern notes",
            "",
        ]
        for shard in shards:
            lines.extend(
                [
                    f"### Shard {shard:02d}",
                    "",
                    (pilot_dir / f"patterns-shard-{shard:02d}.md").read_text(
                        encoding="utf-8"
                    ).strip(),
                    "",
                ]
            )
        source_path = output_dir / f"group-source-{group_index:02d}.md"
        rendered = "\n".join(lines).rstrip() + "\n"
        source_path.write_text(rendered, encoding="utf-8")
        groups.append(
            {
                "group": group_index,
                "shards": shards,
                "episode_count": len(episodes),
                "review_count_with_episodes": review_count,
                "paper_count": paper_count,
                "episode_ids": [episode["episode_id"] for episode in episodes],
                "source_path": str(source_path),
                "source_characters": len(rendered),
            }
        )

    synthesis_manifest = {
        "version": 1,
        "source_manifest": str(pilot_dir / "manifest.json"),
        "shards_per_group": shards_per_group,
        "group_count": len(groups),
        "episode_count": sum(group["episode_count"] for group in groups),
        "groups": groups,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(synthesis_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "GROUP_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return synthesis_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--shards-per-group", type=int, default=DEFAULT_SHARDS_PER_GROUP)
    args = parser.parse_args()
    result = prepare(
        args.pilot_dir,
        args.output_dir,
        shards_per_group=args.shards_per_group,
    )
    print(
        json.dumps(
            {
                "groups": result["group_count"],
                "episodes": result["episode_count"],
                "characters": sum(group["source_characters"] for group in result["groups"]),
            }
        )
    )


if __name__ == "__main__":
    main()
