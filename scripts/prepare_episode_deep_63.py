"""Prepare outcome-blind, one-review packets for selective Episode Deep enrichment."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_PILOT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_CANDIDATES = DEFAULT_PILOT_DIR / "synthesis/deep-candidates.json"
DEFAULT_OUTPUT_DIR = Path("data/analysis/iclr/episode-deep-63")
REVIEW_HEADING_RE = re.compile(r"(?m)^## Review \d+: (?P<review_id>\S+)\s*$")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_episodes(pilot_dir: Path) -> dict[str, dict[str, Any]]:
    episodes: dict[str, dict[str, Any]] = {}
    for path in sorted(pilot_dir.glob("episodes-shard-*.jsonl")):
        for episode in load_jsonl(path):
            episode_id = episode["episode_id"]
            if episode_id in episodes:
                raise ValueError(f"duplicate episode ID: {episode_id}")
            episodes[episode_id] = episode
    return episodes


def review_to_shard(pilot_dir: Path) -> dict[str, int]:
    manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
    mapping: dict[str, int] = {}
    for row in manifest["reviews"]:
        candidate = row["candidate"]
        review_id = candidate["review_id"]
        if review_id in mapping:
            raise ValueError(f"duplicate review in source manifest: {review_id}")
        mapping[review_id] = row["shard"]
    return mapping


def extract_review_section(source: str, review_id: str) -> str:
    matches = list(REVIEW_HEADING_RE.finditer(source))
    for index, match in enumerate(matches):
        if match.group("review_id") != review_id:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        section = source[match.start() : end].strip()
        # Sampling and conference-decision metadata must not influence logic reconstruction.
        section = re.sub(
            rf"(?m)^paper_id=(\S+); decision_bucket=.*$",
            rf"paper_id=\1; review_id={review_id}; outcome_metadata=withheld",
            section,
            count=1,
        )
        return section
    raise ValueError(f"review {review_id} not found in source shard")


def protocol() -> str:
    return """# Episode Deep enrichment protocol

Process one outcome-blind `source-review-NN.md`. Write exactly:

1. `deep-review-NN.jsonl`
2. `deep-review-NN-report.md`

The JSONL must contain exactly one full v0.2 episode for every focal episode ID
listed in the source packet. Start from the supplied Lite record. Preserve its
`episode_id`, `source`, `chain`, `signatures`, and `quality` exactly. Preserve
every existing evidence-registry entry exactly; additional source references
may be added. Set `enrichment_level` to `deep` and add `deep`.

The source packet deliberately contains no Atlas pattern, cluster role, or
conference outcome. Reconstruct the evaluation logic before classification.
Do not search for or read Atlas outputs, other review packets, other Deep
outputs, decisions, rebuttals, or later discussion.

`deep` must contain:

- `focal_factors`: the concrete factor whose value or adequacy drives judgment;
- `standards`: the threshold, norm, desideratum, or burden of proof applied;
- `comparisons`: actual or implied comparators that make the observation matter;
- `assumptions`: premises the reviewer must rely on for the inference, not merely
  assumptions made by the paper;
- `alternative_explanations`: rival accounts left open by the observed evidence;
- `counterfactuals`: what different observation or result would change the
  inference or distinguish rival accounts;
- `inference_steps`: ordered, atomic links from observation through standard to
  judgment;
- `expected_information_gain`: what a requested test/change would distinguish
  and how possible outcomes would update the judgment;
- `repair_conditions`: evidence or revision that would resolve or materially
  narrow the criticism;
- `intervention_spec`: `held_fixed`, `varied`, and `contrasts` only for a genuine
  discriminating intervention; otherwise `null`;
- `trajectory_links`: always `[]` because the packet contains only the initial
  reviewer branch.

Use an empty list whenever a field is not supported. Depth is decomposition,
not filling every field. Never turn a generic request for more evidence into a
causal intervention. A counterfactual must state what changes and how the
reviewer's inference would differ. Expected information gain must distinguish
live alternatives; if the Lite episode contains no requested test/change, it
must be empty. Repair conditions are not promises that the score would change.

Epistemic status is claim-specific:

- `reviewer_explicit`: directly stated in a primary `R-...` line;
- `memo_inferred`: stated by the supplied `I-...` analytic memo but not directly
  by the reviewer;
- `analyst_inferred`: newly reconstructed for this Deep record;
- `mixed` or `unclear`: only when the sentence itself combines or cannot resolve
  those sources.

Every claim must cite one or more keys from the episode evidence registry.
`reviewer_explicit` claims require primary evidence. New evidence keys must map
to exact `R-<review_id>:L###` or `I-<review_id>:L###` locators present in the
packet, with the corresponding provenance level. Do not cite the abstract as
reviewer evidence and do not invent line locators. Keep wording concrete to the
method component, experiment, comparison, quantity, claim, or failure mode.

Neighbor Lite episodes are supplied only to protect episode boundaries. Do not
merge them into the focal episode or output nonfocal episodes. Treat every
nonfocal episode as an exclusion boundary: do not import its object,
observation, standard, comparison, alternative explanation, counterfactual,
requested evidence, or repair into the focal Deep record. Every Deep claim must
decompose the focal Lite chain itself, not another concern found elsewhere in
the review. In particular, do not attach an adjacent negative measurement or
validity concern to a focal positive-credit episode. Additional evidence may be
used only when it directly supports a link already inside the focal chain; it
must not broaden the focal judgment. If the source shows that a Lite inference
is questionable, preserve the Lite record and explain the challenge in the
report rather than silently rewriting or compensating for it with a neighbor.

The Markdown report must list: focal IDs; strongest explicit support; each new
analyst inference; unsupported fields left empty; a boundary audit naming any
nonfocal concern deliberately excluded; every additional evidence locator and
why it supports the focal chain; and any Lite claim that source inspection
weakens. Run the validator and repair outputs until it succeeds.
"""


def prepare(
    pilot_dir: Path,
    candidates_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    episodes = load_episodes(pilot_dir)
    candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates = candidates_payload["candidates"]
    source_shards = review_to_shard(pilot_dir)
    focal_by_review: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        episode_id = candidate["episode_id"]
        if episode_id not in episodes:
            raise ValueError(f"candidate episode is missing: {episode_id}")
        episode = episodes[episode_id]
        if episode["source"]["review_id"] != candidate["review_id"]:
            raise ValueError(f"candidate review mismatch: {episode_id}")
        focal_by_review[candidate["review_id"]].append(candidate)

    all_by_review: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes.values():
        all_by_review[episode["source"]["review_id"]].append(episode)
    for values in all_by_review.values():
        values.sort(key=lambda row: row["episode_id"])

    output_dir.mkdir(parents=True, exist_ok=True)
    reviews = []
    for unit, review_id in enumerate(sorted(focal_by_review), 1):
        focal = sorted(focal_by_review[review_id], key=lambda row: row["episode_id"])
        focal_ids = [row["episode_id"] for row in focal]
        shard = source_shards[review_id]
        source_path = pilot_dir / f"source-shard-{shard:02d}.md"
        review_section = extract_review_section(
            source_path.read_text(encoding="utf-8"), review_id
        )
        neighbors = all_by_review[review_id]
        lines = [
            f"# Outcome-blind Episode Deep source review {unit:02d}",
            "",
            "Atlas memberships, selection roles, and conference outcome are withheld.",
            "Analyze only the focal IDs; neighboring Lite episodes protect boundaries.",
            "",
            "## Focal episode IDs",
            "",
            *[f"- `{episode_id}`" for episode_id in focal_ids],
            "",
            "## Existing Lite episodes from this review",
            "",
            "```jsonl",
            *[
                json.dumps(
                    {"focal": episode["episode_id"] in focal_ids, "episode": episode},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for episode in neighbors
            ],
            "```",
            "",
            "## Isolated initial-review source",
            "",
            review_section,
            "",
        ]
        packet = "\n".join(lines)
        packet_path = output_dir / f"source-review-{unit:02d}.md"
        packet_path.write_text(packet, encoding="utf-8")
        reviews.append(
            {
                "unit": unit,
                "review_id": review_id,
                "paper_id": focal[0]["paper_id"],
                "source_shard": shard,
                "focal_episode_ids": focal_ids,
                "all_review_episode_ids": [row["episode_id"] for row in neighbors],
                "selection": focal,
                "source_path": str(packet_path),
                "source_characters": len(packet),
            }
        )

    manifest = {
        "version": 1,
        "source_pilot": str(pilot_dir),
        "source_candidates": str(candidates_path),
        "candidate_count": len(candidates),
        "review_count": len(reviews),
        "outcome_blind": True,
        "atlas_blind": True,
        "reviews": reviews,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "DEEP_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    result = prepare(args.pilot_dir, args.candidates, args.output_dir)
    print(
        json.dumps(
            {
                "reviews": result["review_count"],
                "episodes": result["candidate_count"],
                "characters": sum(row["source_characters"] for row in result["reviews"]),
            }
        )
    )


if __name__ == "__main__":
    main()
