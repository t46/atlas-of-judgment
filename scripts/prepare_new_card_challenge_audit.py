"""Prepare skeptical, outcome-blind challenge packets for the three new cards."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-reclassification-3135")
RIVALS = {
    "N-P01": {"A-P01", "A-P03", "A-P08"},
    "N-P02": {"A-P02", "A-P03", "A-P04", "A-P06", "A-P09"},
    "N-P03": {"A-P02", "A-P05", "A-P08"},
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stable(rows: list[dict[str, Any]], salt: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: hashlib.sha256(f"{salt}:{row['episode_id']}".encode()).hexdigest())


def select(rows: list[dict[str, Any]], card_id: str, size: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    ids: set[str] = set()

    def take(pool: list[dict[str, Any]], count: int, label: str) -> int:
        added = 0
        for row in stable(pool, f"{card_id}:{label}"):
            if row["episode_id"] in ids:
                continue
            selected.append(row)
            ids.add(row["episode_id"])
            added += 1
            if added == count:
                break
        return added

    rivals = RIVALS[card_id]
    overlap = [row for row in rows if set(row["existing_pattern_ids"]) & rivals]
    new_only = [row for row in rows if not row["existing_pattern_ids"]]
    boundary = [row for row in rows if row["candidate_membership"]["fit"] == "boundary"]
    variant = [row for row in rows if row["candidate_membership"]["fit"] == "variant"]
    counts = {
        "nearest_existing_overlap": take(overlap, 16, "overlap"),
        "new_only": take(new_only, 12, "new-only"),
        "boundary": take(boundary, 10, "boundary"),
        "variant": take(variant, 6, "variant"),
    }
    counts["remainder"] = take(rows, max(0, size - len(selected)), "remainder")
    return selected[:size], counts


def protocol(card_id: str, card: dict[str, Any]) -> str:
    return f"""# Skeptical challenge audit: {card_id}

You are auditing whether a proposed card is truly a distinct reviewer reasoning
endpoint or should be absorbed into an existing Atlas card. The sample
deliberately over-represents hard collisions and is not a prevalence sample.

## Candidate

```json
{json.dumps(card, ensure_ascii=False, indent=2)}
```

For every case, attempt to falsify the proposed membership. Do not defer to the
screening reason. Compare the actual Episode Lite chain with the listed existing
memberships and nearest rivals. A valid multi-label result requires two genuinely
distinct inference endpoints, not two descriptions of one defect.

Write one JSON object per case in exact packet order with:

- `episode_id`, `candidate_card_id`;
- `verdict`: `retain_distinct`, `absorb_existing`, `revise_boundary`, or
  `source_insufficient`;
- `strongest_existing_rival`: an A-Pxx ID or null;
- `gate_components`: a non-empty list of objects with `component`, `present`,
  and episode-specific `evidence`;
- `decisive_endpoint`, `reason`, `confidence` (`low`, `medium`, `high`);
- `proposed_boundary_change`: a concrete string or null.

`retain_distinct` means the complete hard gate is present and the endpoint is
not reducible to the rival. `absorb_existing` means an existing card already
captures the operative inference. `revise_boundary` means the card is useful
but the current inclusion/exclusion rule admits a systematic false-positive
class. `source_insufficient` is for missing source links, not ordinary
ambiguity.

The report must count verdicts, name repeated failure modes and collision pairs,
give a candidate-level recommendation (`retain`, `revise`, or `retire`), and
state an exact boundary revision if recommending revise. Do not infer conference
outcomes or population prevalence.
"""


def prepare(directory: Path, output: Path, sample_size: int = 48) -> dict[str, Any]:
    joined = load_jsonl(directory / "atlas-13-membership.jsonl")
    lite_dir = directory.parent / "episode-lite-1000"
    lite_rows: dict[str, dict[str, Any]] = {}
    for path in sorted(lite_dir.glob("episodes-shard-*.jsonl")):
        for row in load_jsonl(path):
            lite_rows[row["episode_id"]] = row
    adjudication = json.loads(
        (directory / "unmapped-discovery/global-adjudication.json").read_text(encoding="utf-8")
    )
    cards = {row["card_id"]: row for row in adjudication["final_cards"]}
    output.mkdir(parents=True, exist_ok=True)
    manifest_cards: list[dict[str, Any]] = []
    for card_id in ("N-P01", "N-P02", "N-P03"):
        candidates = []
        for row in joined:
            membership = next(
                (item for item in row["memberships"] if item["pattern_id"] == card_id), None
            )
            if membership is None:
                continue
            candidates.append({
                "episode_id": row["episode_id"],
                "episode": lite_rows[row["episode_id"]],
                "candidate_membership": membership,
                "existing_pattern_ids": row["existing_pattern_ids"],
                "existing_memberships": [
                    item for item in row["memberships"] if item["pattern_id"].startswith("A-")
                ],
                "nearest_rivals": sorted(RIVALS[card_id]),
            })
        chosen, strata = select(candidates, card_id, sample_size)
        packet = output / f"challenge-{card_id}.jsonl"
        packet.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chosen),
            encoding="utf-8",
        )
        (output / f"PROTOCOL-{card_id}.md").write_text(protocol(card_id, cards[card_id]), encoding="utf-8")
        manifest_cards.append({
            "card_id": card_id, "population_positive_count": len(candidates),
            "sample_count": len(chosen), "episode_ids": [row["episode_id"] for row in chosen],
            "selection_strata_added": strata,
        })
    manifest = {
        "version": 1, "outcome_blind": True, "sample_is_not_prevalence": True,
        "cards": manifest_cards,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR / "new-card-challenge-audit")
    parser.add_argument("--sample-size", type=int, default=48)
    args = parser.parse_args()
    manifest = prepare(args.directory, args.output, args.sample_size)
    print(json.dumps({row["card_id"]: row["sample_count"] for row in manifest["cards"]}))


if __name__ == "__main__":
    main()
