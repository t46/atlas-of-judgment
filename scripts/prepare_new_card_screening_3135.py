"""Prepare blind packets to screen all 3,135 episodes against three new cards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_LITE = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_ADJUDICATION = Path(
    "data/analysis/iclr/episode-reclassification-3135/unmapped-discovery/global-adjudication.json"
)
DEFAULT_OUTPUT = Path(
    "data/analysis/iclr/episode-reclassification-3135/new-card-screening"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def protocol(cards: list[dict[str, Any]]) -> str:
    card_json = json.dumps(cards, ensure_ascii=False, indent=2)
    return f"""# New-card screening protocol for all 3,135 Episode Lite records

Screen every episode independently against N-P01, N-P02, and N-P03. Existing
Atlas memberships and conference outcomes are withheld. This is an exhaustive
screen, not a search restricted to the previously unmapped set. Zero, one, or
multiple new-card memberships are allowed.

## Candidate cards

```json
{card_json}
```

Write one JSON object per source episode, in exact source order, with:

- `episode_id`;
- `new_memberships`: zero or more objects with `card_id`, `fit` (`core`,
  `variant`, `boundary`), `gate_evidence`, `reason`, `decisive_chain_fields`,
  and `confidence`;
- `uncertain_new_cards`: zero or more objects with `card_id`, `reason`, and
  `missing_links`;
- `closest_excluded_new_cards`: zero to three objects with `card_id` and
  episode-specific `reason`;
- `screen_confidence` (`low`, `medium`, `high`);
- `needs_source_audit`.

Membership, uncertainty, and closest-excluded sets must be disjoint. Evaluate
all three gates independently; do not stop after the first match. Do not assign
a card from words such as mechanism, rationale, conceptual, clarity, figure,
presentation, or explanation alone.

Hard gates:

- **N-P01:** Name the already credible/observed behavior, failure, or result;
  name the missing why/process account; and state how that explanation would
  generate a discriminating next intervention or remedy. Explanation needed
  merely to reconstruct the method is A-P02, a matched intervention isolating
  causal credit is A-P01, and generic missing evidence is A-P08—not N-P01.
- **N-P02:** Apply the complete-reporting counterfactual: if procedure,
  provenance, notation, and evidence were fully reported, would a substantive
  problem/task-to-design rationale or conceptual-boundary judgment remain?
  Name both sides of that rationale. Formal premise validity is A-P03,
  construct representation is A-P06, and editorial wording is N-P03.
- **N-P03:** Establish that the underlying scientific claim and required
  reconstruction remain inspectable; then name a concrete exposition, figure,
  notation, organization, copyediting, or professional-register defect whose
  endpoint is reader communication/publication readiness. If the defect blocks
  reconstruction use A-P02; if it blocks a claim-relevant empirical inference
  use A-P08; if it changes semantic scope use A-P05.

Use uncertainty when missing provenance could change a plausible gate result.
Use `high` only when object, observation, bridge, and endpoint are explicit;
do not assign one confidence mechanically. The report must summarize counts,
multi-label cases, uncertainty, hardest exclusions, ambiguities, discretion,
and retries. Never infer population prevalence or conference outcomes.
"""


def prepare(
    lite: Path, adjudication_path: Path, output: Path,
    selected_shards: set[int] | None = None,
) -> dict[str, Any]:
    lite_manifest = json.loads((lite / "manifest.json").read_text())
    adjudication = json.loads(adjudication_path.read_text())
    cards = [
        {
            "card_id": row["card_id"], "name": row["name"],
            "chain_template": row["chain_template"],
            "inclusion_rule": row["inclusion_rule"],
            "exclusion_rule": row["exclusion_rule"],
            "nearest_atlas_patterns": row["nearest_atlas_patterns"],
            "variants": row["variants"], "confidence": row["confidence"],
        }
        for row in adjudication["final_cards"]
    ]
    if {row["card_id"] for row in cards} != {"N-P01", "N-P02", "N-P03"}:
        raise ValueError("expected exactly N-P01, N-P02, N-P03")
    shard_count = lite_manifest["shard_count"]
    shards = selected_shards or set(range(1, shard_count + 1))
    output.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    all_ids = set()
    for shard in sorted(shards):
        episodes = load_jsonl(lite / f"episodes-shard-{shard:02d}.jsonl")
        ids = [row["episode_id"] for row in episodes]
        if overlap := set(ids) & all_ids:
            raise ValueError(f"duplicate IDs: {sorted(overlap)}")
        all_ids.update(ids)
        lines = [
            f"# New-card screen source shard {shard:03d}", "",
            "Prior memberships and conference outcomes are withheld.", "",
            "```jsonl",
            *[json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in episodes],
            "```", "",
        ]
        rendered = "\n".join(lines)
        if any(term in rendered for term in ("decision_bucket", '"rating"', '"pattern_ids"')):
            raise ValueError(f"shard {shard} contains forbidden metadata")
        (output / f"source-shard-{shard:03d}.md").write_text(rendered)
        manifest_rows.append(
            {"shard": shard, "episode_ids": ids, "episode_count": len(ids),
             "source_characters": len(rendered)}
        )
    manifest = {
        "version": 1, "source_manifest": str(lite / "manifest.json"),
        "outcome_blind": True, "prior_membership_blind": True,
        "card_ids": ["N-P01", "N-P02", "N-P03"],
        "source_shard_count": shard_count,
        "prepared_shard_count": len(manifest_rows),
        "episode_count": len(all_ids), "shards": manifest_rows,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "SCREENING_PROTOCOL.md").write_text(protocol(cards))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lite", type=Path, default=DEFAULT_LITE)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--shard", type=int, action="append")
    args = parser.parse_args()
    result = prepare(
        args.lite, args.adjudication, args.output,
        set(args.shard) if args.shard else None,
    )
    print(json.dumps({"shards": result["prepared_shard_count"], "episodes": result["episode_count"]}))


if __name__ == "__main__":
    main()
