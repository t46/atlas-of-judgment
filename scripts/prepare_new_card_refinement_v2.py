"""Prepare blind candidate-only refinement packets under audited card boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-reclassification-3135")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def protocol() -> str:
    return """# Candidate-card refinement protocol v2

Re-evaluate candidate card/episode pairs under boundaries tightened by a
skeptical collision audit. The packet hides the v1 positive-versus-uncertain
decision and conference outcomes. Existing Atlas memberships are shown only so
you can test whether the candidate is a genuinely independent endpoint.

## N-P01 — Mechanistic explanation and remedy-space inference

Confirm only when all are explicit in the Episode Lite chain:

1. an already credible empirical phenomenon, failure, or intervention outcome;
2. a missing producing-process account or concrete competing mechanism;
3. an episode-specific discriminating next test, intervention, or concrete
   remedy that follows from that mechanism and is part of the reviewer logic.

Generic calls for explanation, deeper insight, analysis, aggregate-score
diagnosis, or unlinked anomaly discussion fail the gate. A matched ablation or
threshold variation whose endpoint is causal credit is A-P01; formal
premise-to-operation validity is A-P03; missing claim-relevant support is A-P08.
Do not invent a remedy merely because a mechanism explanation could in theory
suggest one.

## N-P02 — Conceptual rationale and task-boundary intelligibility

Confirm only if, after separately resolving reconstruction/specification,
formal premise or operation validity, comparative distinctiveness, scope or
transfer, construct validity, whole-chain credit, causal attribution,
evaluation interpretation, practical significance, and failure-handling logic,
one of two substantive routes remains: (A) an explicit task/problem rationale
is judged against the chosen design, representation, or operation; or (B) a
named conceptual/category boundary is judged for compatibility with a concrete
design or operation (for example, whether internally generated targets fit an
"untargeted" category). Both sides of the selected route must be named in the
episode, and the judgment must survive complete reporting. A
conceptual-sounding restatement of A-P02/A-P03/A-P04/A-P05/A-P06/A-P09 is not
N-P02.

## N-P03 — Presentation and communication readiness

Confirm only for an explicit surface-level reader-facing presentation,
copyediting, legibility, or organization defect whose repair leaves the claim,
method, proof, and evidence independently reconstructable and inspectable.
Exclude missing explanation, context, comparator, semantic label, setup detail,
or figure relationship needed to inspect, interpret, validate, or position a
scientific object. Exclude ordinary reader disagreement without a concrete
communication defect and repair. Typography, localized typos, cosmetic object
labels, spacing, and emphasis/organization may qualify when truly surface-only.

## Output

Write one JSON object per packet row, exact order, with all keys:

- `episode_id`, `card_id`;
- `verdict`: `confirmed`, `excluded`, or `uncertain`;
- `fit`: `core`, `variant`, `boundary`, or null;
- `gate_evidence`: one episode-specific string covering every required gate
  component when confirmed, otherwise null;
- `reason`;
- `strongest_existing_rival`: A-Pxx or null;
- `n_p02_route`: `task_design`, `concept_boundary_operation`, or `none` for
  N-P02; null for the other cards;
- `missing_links`: a list, empty unless uncertain;
- `confidence`: low, medium, or high;
- `needs_source_audit`: boolean.

Confirmed means every v2 component is present and independent from the rival.
Excluded is the default when any component is absent. Uncertain is only for
specific missing source provenance that could flip the result and requires
source audit. For every N-P02 pair, test route A and route B separately before
choosing `none`; an N-P02 exclusion reason must state why each route fails or is
absorbed by a rival. Never infer prevalence or outcomes.

The report must give verdict/card counts, collision pairs, hard exclusions,
ambiguities, and source-audit counts. It must explicitly confirm that no remedy,
task-to-design side, or intact-inspectability premise was invented.
"""


def prepare(directory: Path, output: Path, shard_size: int = 20) -> dict[str, Any]:
    joined = load_jsonl(directory / "atlas-13-membership.jsonl")
    lite_dir = directory.parent / "episode-lite-1000"
    episodes: dict[str, dict[str, Any]] = {}
    for path in sorted(lite_dir.glob("episodes-shard-*.jsonl")):
        for row in load_jsonl(path):
            episodes[row["episode_id"]] = row
    pairs: list[dict[str, Any]] = []
    for row in joined:
        candidate_ids = set(row["new_pattern_ids"]) | {
            item["card_id"] for item in row["uncertain_new_cards"]
        }
        for card_id in sorted(candidate_ids):
            pairs.append({
                "episode_id": row["episode_id"], "card_id": card_id,
                "episode": episodes[row["episode_id"]],
                "existing_memberships": [
                    item for item in row["memberships"] if item["pattern_id"].startswith("A-")
                ],
            })
    pairs.sort(key=lambda row: hashlib.sha256(f"v2:{row['episode_id']}:{row['card_id']}".encode()).hexdigest())
    output.mkdir(parents=True, exist_ok=True)
    shards = []
    for index, offset in enumerate(range(0, len(pairs), shard_size), 1):
        rows = pairs[offset:offset + shard_size]
        path = output / f"source-shard-{index:03d}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        shards.append({
            "shard": index, "pair_count": len(rows),
            "keys": [[row["episode_id"], row["card_id"]] for row in rows],
        })
    manifest = {
        "version": 2, "candidate_only_monotonic_narrowing": True,
        "outcome_blind": True, "prior_v1_verdict_blind": True,
        "pair_count": len(pairs), "shard_count": len(shards), "shards": shards,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "REFINEMENT_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR / "new-card-refinement-v2")
    parser.add_argument("--shard-size", type=int, default=20)
    args = parser.parse_args()
    manifest = prepare(args.directory, args.output, args.shard_size)
    print(json.dumps({"pairs": manifest["pair_count"], "shards": manifest["shard_count"]}))


if __name__ == "__main__":
    main()
