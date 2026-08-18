"""Prepare a compact packet for independent global Atlas-extension proposals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-reclassification-3135/unmapped-discovery")
DEFAULT_ADJUDICATION = Path("data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication.json")


def prepare(directory: Path, adjudication_path: Path) -> dict:
    adjudication = json.loads(adjudication_path.read_text())
    atlas = [
        {
            "pattern_id": row["pattern_id"], "name": row["recommended_name"],
            "core_logic": row["clarified_core_logic"],
            "inclusion_rule": row["inclusion_rule"],
            "exclusion_rule": row["exclusion_rule"],
        }
        for row in adjudication["pattern_decisions"]
    ]
    regional = []
    for region in (1, 2):
        data = json.loads((directory / f"regional-patterns-{region:02d}.json").read_text())
        regional.extend(data["regional_patterns"])
    local = []
    for group in range(1, 9):
        data = json.loads((directory / f"local-patterns-{group:02d}.json").read_text())
        local.extend(data["candidate_patterns"])
    packet = {"current_atlas": atlas, "regional_candidates": regional, "local_candidates": local}
    (directory / "global-source.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    protocol = """# Global Atlas-extension proposal protocol

Independently evaluate all 12 regional candidates against the current ten-card
Atlas and the 18 local hypotheses. Write one proposal JSON with:

- `proposal_id`;
- `regional_decisions`, exactly once and in source order for all regional IDs;
- `proposed_new_cards`;
- `cross_boundaries`;
- `residual_singleton_families`;
- `method_notes`.

Each regional decision has `regional_pattern_id`, `decision` (`new_card`,
`merge_new`, `atlas_variant`, `retire`, or `split`), `target_ids`, `reason`, and
`confidence`. New card IDs are `N-PNN`; each card has `name`, a six-field
`chain_template`, `inclusion_rule`, `exclusion_rule`, `supporting_regional_ids`,
`supporting_local_ids`, `representative_episode_ids`, `boundary_episode_ids`,
`nearest_atlas_patterns` with decisive differences, `variants`, `confidence`,
and `full_corpus_test`.

A new card must represent a recurring operative inference endpoint that cannot
be expressed as a variant or conjunction of A-P01–A-P10. Similar wording,
sentiment, topic, publication criteria, or requested action is insufficient.
Challenge especially: explanation vs causal attribution; conceptual
intelligibility vs auditability/formal alignment; editorial presentation vs
reconstructability; unreserved contribution credit vs A-P09; field significance
vs comparative distinctiveness or decision warrant; representation legibility
vs construct validity. Prefer a smaller discriminative extension. Preserve a
candidate when the boundary is real even if selected-sample support is modest;
do not treat support counts as population prevalence.
"""
    (directory / "GLOBAL_PROTOCOL.md").write_text(protocol)
    return {"regional_candidates": len(regional), "local_candidates": len(local)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.adjudication)))


if __name__ == "__main__":
    main()
