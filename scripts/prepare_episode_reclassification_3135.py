"""Prepare outcome- and prior-membership-blind Lite reclassification packets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_LITE_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_ADJUDICATION = Path(
    "data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication.json"
)
DEFAULT_OUTPUT_DIR = Path("data/analysis/iclr/episode-reclassification-3135")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def protocol() -> str:
    return """# Episode Lite full-reclassification protocol

Classify every Episode Lite record in one `source-shard-NNN.md` using the ten
adjudicated evaluation-logic cards. Write exactly:

1. `classification-shard-NNN.jsonl`
2. `classification-shard-NNN-report.md`

The packet withholds conference outcomes and prior Atlas memberships. Infer
membership from the episode's inspected object, observation, reasoning bridge,
judgment, request, and—most importantly—the operative inference endpoint.
Request wording, paper topic, and positive/negative tone are not membership
rules. Do not force every episode into a card, and preserve valid multi-label
logic when two standards independently drive the judgment.

Use a two-pass decision. First identify the primary operative endpoint. Then
perform a card-by-card second pass over all ten hard gates; do not stop after
finding the best-fitting card. Record every independently warranted endpoint.
Add a second membership only when a different judgment can be stated and would
remain meaningful if the primary endpoint label were removed. A shared
comparator, topic, request, or piece of evidence is not by itself a second
logic. In particular, when a positive or negative local empirical comparison
both establishes a claim-level evidence judgment (A-P08) and warrants a named
downstream decision-facing conclusion (A-P10), retain both if those two
judgments can be stated separately. The mere presence of a comparator does not
create A-P04 unless positioning, distinctiveness, or fairness independently
drives a judgment.

Each card includes an anonymized Deep `anchor_contrast`: one positive case and
one disputed/removed boundary case. Use the contrast to calibrate the operative
endpoint, not as a lexical template. The source episode must still independently
satisfy the inclusion rule and survive the exclusion rule.

Write one JSON object per source episode, in source order, with exactly:

- `episode_id`;
- `memberships`: zero or more objects with `pattern_id`, `fit` (`core`,
  `variant`, or `boundary`), `gate_evidence`, `reason`,
  `decisive_chain_fields`, and `confidence` (`low`, `medium`, or `high`);
- `uncertain_patterns`: zero or more objects with `pattern_id`, `reason`, and
  `missing_links`; use this when a card may fit but provenance or the bridge is
  insufficient;
- `closest_excluded_patterns`: zero to three objects with `pattern_id` and a
  concrete reason the episode fails that card's inclusion rule or meets its
  exclusion rule;
- `unmapped_logic`: null or an object with `label`, `abstract_signature`, and
  `reason`; use it for an operative logic not represented by the ten cards,
  even if another secondary logic is mapped;
- `classification_confidence`: `low`, `medium`, or `high`;
- `needs_source_audit`: boolean.

`decisive_chain_fields` may contain `inspected_objects`, `observations`,
`reasoning_bridge`, `judgments`, `requested_tests_or_changes`, `signatures`, or
`missingness`. Reasons must name the episode-specific object/observation and
the inference endpoint. Do not copy a card definition as the reason.

Before adding a membership, write `gate_evidence` that answers the relevant
hard gate below. If the episode cannot answer the gate, do not assign the card;
use uncertainty, closest exclusion, or unmapped logic instead.

- A-P01: What is held fixed, what is varied, and what causal credit would the
  contrast assign? A mechanism explanation without an intervention is not A-P01.
- A-P02: What missing procedural, expository, dependency, or provenance link
  prevents independent reconstruction? A substantive question that survives
  complete reporting is not A-P02.
- A-P03: Which formal premise, domain assumption, identity, or derivation must
  support which implemented operation or evaluated regime?
- A-P04: Which nearest precedent, relevant alternative, or matched operating
  point determines distinctiveness, contribution value, or comparator fairness?
- A-P05: Which target regime is changed or omitted, and what transfer,
  robustness, or external-validity claim is tested there?
- A-P06: Which construct is invoked, how is it operationalized, and why might
  the metric, proxy, benchmark, label, or reference fail to represent it?
- A-P07: Which resource, dependency, stability, execution, or deployment
  constraint determines practical usability? A merely incomplete experiment
  matrix is not feasibility unless this constraint is the hinge.
- A-P08: Which local empirical claim is supported or undermined by which
  discriminating comparator, control, coverage cell, legibility artifact, or
  integrity check?
- A-P09: Which aligned positive core receives credit, and which explicit
  reservation bounds that same credit? A standalone strength or weakness fails.
- A-P10: Which named downstream action, deployment, or decision-facing
  conclusion is warranted or bounded by the evidence? Name the residual
  uncertainty or practical condition when one is present; a positive bounded
  downstream-decision inference need not invent one.

Membership, uncertainty, and closest-excluded sets must be disjoint. If both
membership and uncertainty are empty, `unmapped_logic` is required: the episode
must not disappear merely because the Atlas lacks a fit. Analytic-wrapper-only
or incomplete episodes are uncertainty, not negative evidence. Use uncertainty
only when the available wrapper makes one or more specific cards genuinely
plausible; use unmapped `unresolved operative endpoint` when it does not expose
enough information to prefer any card. Do not mechanically route every generic
"insufficient support" wrapper to A-P08. Set `needs_source_audit=true` when
primary evidence is needed to decide.

Use `high` confidence only when the inspected object, observation, bridge, and
endpoint are all recoverable and the nearest exclusion is clear. Use `medium`
when the endpoint is inferred but discriminative. Use `low` plus uncertainty or
an unmapped record when missingness could change membership. Do not assign one
confidence level mechanically to the entire shard.

The Markdown report must summarize counts, multi-label cases, uncertain cases,
unmapped candidates, and the hardest boundaries without inferring population
prevalence or conference-outcome effects. Validate and repair both files before
finishing.
"""


def _card(row: dict[str, Any]) -> dict[str, Any]:
    anchor = row["rationale"].replace(
        row["strongest_supporting_episode_id"], "[positive anchor]"
    ).replace(row["decisive_boundary_episode_id"], "[boundary anchor]")
    return {
        "pattern_id": row["pattern_id"],
        "name": row["recommended_name"],
        "decision": row["decision"],
        "core_logic": row["clarified_core_logic"],
        "inclusion_rule": row["inclusion_rule"],
        "exclusion_rule": row["exclusion_rule"],
        "anchor_contrast": anchor,
    }


def prepare(
    lite_dir: Path,
    adjudication_path: Path,
    output_dir: Path,
    selected_shards: set[int] | None = None,
) -> dict[str, Any]:
    lite_manifest = json.loads((lite_dir / "manifest.json").read_text(encoding="utf-8"))
    adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
    cards = [_card(row) for row in adjudication["pattern_decisions"]]
    pattern_ids = {row["pattern_id"] for row in cards}
    if len(cards) != 10 or len(pattern_ids) != 10:
        raise ValueError("adjudication must contain ten unique cards")

    shard_count = lite_manifest["shard_count"]
    shards = selected_shards or set(range(1, shard_count + 1))
    unknown = {shard for shard in shards if shard < 1 or shard > shard_count}
    if unknown:
        raise ValueError(f"invalid shards: {sorted(unknown)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    all_episode_ids: set[str] = set()
    for shard in sorted(shards):
        episodes = load_jsonl(lite_dir / f"episodes-shard-{shard:02d}.jsonl")
        episode_ids = [row["episode_id"] for row in episodes]
        duplicate = set(episode_ids) & all_episode_ids
        if duplicate:
            raise ValueError(f"duplicate episodes across packets: {sorted(duplicate)}")
        all_episode_ids.update(episode_ids)
        lines = [
            f"# Episode Lite reclassification source: shard {shard:03d}",
            "",
            "Prior Atlas memberships and conference outcomes are withheld.",
            "Selected-sample counts are not population prevalence.",
            "",
            "## Adjudicated cards",
            "",
            "```json",
            json.dumps(cards, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Decisive cross-pattern boundaries",
            "",
            "```json",
            json.dumps(
                [
                    {
                        "left_pattern_id": row["left_pattern_id"],
                        "right_pattern_id": row["right_pattern_id"],
                        "distinction": row["distinction"],
                        "decisive_deep_fields": row["decisive_deep_fields"],
                    }
                    for row in adjudication["cross_pattern_boundaries"]
                ],
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "## Episode Lite records",
            "",
            "```jsonl",
            *[json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in episodes],
            "```",
            "",
        ]
        source = "\n".join(lines)
        forbidden = ("decision_bucket", '"rating"', '"pattern_ids"')
        if any(item in source for item in forbidden):
            raise ValueError(f"shard {shard}: blind packet contains forbidden metadata")
        source_path = output_dir / f"source-shard-{shard:03d}.md"
        source_path.write_text(source, encoding="utf-8")
        manifest_rows.append(
            {
                "shard": shard,
                "source_path": str(source_path),
                "episode_ids": episode_ids,
                "episode_count": len(episode_ids),
                "review_count": len({row["source"]["review_id"] for row in episodes}),
                "source_characters": len(source),
            }
        )

    manifest = {
        "version": 1,
        "source_lite_manifest": str(lite_dir / "manifest.json"),
        "source_adjudication": str(adjudication_path),
        "outcome_blind": True,
        "prior_membership_blind": True,
        "pattern_ids": sorted(pattern_ids),
        "source_shard_count": shard_count,
        "prepared_shard_count": len(manifest_rows),
        "episode_count": len(all_episode_ids),
        "shards": manifest_rows,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "RECLASSIFICATION_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lite-dir", type=Path, default=DEFAULT_LITE_DIR)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--shard", type=int, action="append")
    args = parser.parse_args()
    result = prepare(
        args.lite_dir,
        args.adjudication,
        args.output_dir,
        set(args.shard) if args.shard else None,
    )
    print(
        json.dumps(
            {
                "shards": result["prepared_shard_count"],
                "episodes": result["episode_count"],
                "characters": sum(row["source_characters"] for row in result["shards"]),
            }
        )
    )


if __name__ == "__main__":
    main()
