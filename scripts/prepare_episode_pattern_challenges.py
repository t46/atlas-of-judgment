"""Prepare one Deep-evidence challenge packet for each provisional Atlas pattern."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DEEP_DIR = Path("data/analysis/iclr/episode-deep-63")
DEFAULT_SYNTHESIS_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")
DEFAULT_OUTPUT_DIR = DEFAULT_DEEP_DIR / "pattern-challenges"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def protocol() -> str:
    return """# Deep-evidence Atlas pattern challenge protocol

Process one `pattern-source-A-PNN.md` and write exactly:

1. `pattern-challenge-A-PNN.json`
2. `pattern-challenge-A-PNN-report.md`

This is an adversarial audit, not a request to defend the provisional Atlas.
Read the target card, its confusable cards, and every selected Deep episode.
Judge the evaluation logic—object, observation, standard, assumptions,
alternatives, inference, judgment, and requested evidence—not the paper topic.

The JSON object must contain `pattern_id`, `candidate_episode_ids`,
`episode_assessments`, and `pattern_assessment`.

Each candidate episode appears exactly once in `episode_assessments` with:

- `episode_id`;
- `membership_verdict`: `core`, `variant`, `boundary_keep`, `remove`,
  `counterexample`, or `insufficient`;
- `reason`: a concrete contrast between the Deep chain and the target logic;
- `boundary_with`: zero or more Atlas pattern IDs;
- `decisive_deep_fields`: one or more of `focal_factors`, `standards`,
  `comparisons`, `assumptions`, `alternative_explanations`, `counterfactuals`,
  `inference_steps`, `expected_information_gain`, `repair_conditions`, or
  `missingness`;
- `confidence`: `low`, `medium`, or `high`.

`pattern_assessment` must contain:

- `status`: `stable`, `revise`, `split_candidate`, `merge_candidate`,
  `retire_candidate`, or `underdetermined`;
- `recommended_name`, `core_logic`, `inclusion_rule`, and `exclusion_rule`;
- `split_proposals`: each with `name`, `logic`, and supporting candidate IDs;
- `merge_targets`: each with an existing other Atlas `pattern_id`, `reason`,
  and supporting candidate IDs;
- `retained_counterexample_ids`, `key_findings`, `missingness_limits`, and
  `full_corpus_implication`.

Apply status labels consistently:

- `stable`: the original core, inclusion/exclusion boundary, and selected
  representative/boundary memberships survive materially unchanged; removals
  are limited to cases selected only as counterexamples or insufficient probes;
- `revise`: the core remains recognizable, but at least one original
  representative/boundary/low-confidence member should be removed, or the
  inclusion/exclusion rule must be tightened or broadened;
- `split_candidate`: at least two distinct recurring standards or inference
  templates are currently combined;
- `merge_candidate`: another Atlas card uses materially the same standard and
  inference template;
- `retire_candidate`: no coherent recurring core survives;
- `underdetermined`: missingness prevents those judgments.

Do not call a pattern stable merely because its name or intuitive core can be
retained when its current representative membership or boundary rule must
change. Conversely, a selected counterexample that remains outside the core
does not by itself require revision.

Do not force change. A stable result is valid when Deep evidence sharpens the
same boundary. Conversely, do not preserve a pattern merely because it has
large Lite support. A split must identify at least two distinct standards or
inference templates, not merely positive versus negative valence or different
paper topics. A merge must show that the supposedly separate patterns use the
same operative standard and inference, not merely similar requests. A removal
must explain where the episode fits better or why evidence is insufficient.

Candidate support is purposefully selected and cannot estimate prevalence.
Outcome labels remain unavailable. The Markdown report should explain the
strongest confirmation, strongest falsification, merge/split tests, important
missingness, and the exact change—if any—that should be propagated to the full
3,135-episode Atlas. Validate and repair both files before finishing.
"""


def prepare(
    deep_dir: Path,
    synthesis_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    deep_manifest = json.loads((deep_dir / "manifest.json").read_text(encoding="utf-8"))
    candidates = json.loads((synthesis_dir / "deep-candidates.json").read_text(encoding="utf-8"))["candidates"]
    atlas = json.loads((synthesis_dir / "final-pattern-atlas.json").read_text(encoding="utf-8"))
    atlas_by_id = {row["pattern_id"]: row for row in atlas["patterns"]}
    unknown_confusable_ids = {
        confusable_id
        for pattern in atlas["patterns"]
        for confusable_id in pattern.get("confusable_with", [])
        if confusable_id not in atlas_by_id
    }
    if unknown_confusable_ids:
        raise ValueError(
            f"Atlas contains unknown confusable pattern IDs: {sorted(unknown_confusable_ids)}"
        )
    deep_by_id: dict[str, dict[str, Any]] = {}
    for row in deep_manifest["reviews"]:
        path = deep_dir / f"deep-review-{row['unit']:02d}.jsonl"
        if not path.exists():
            raise ValueError(f"missing Deep output: {path}")
        for episode in load_jsonl(path):
            episode_id = episode["episode_id"]
            if episode_id in deep_by_id:
                raise ValueError(f"duplicate Deep episode: {episode_id}")
            deep_by_id[episode_id] = episode
    expected_ids = {row["episode_id"] for row in candidates}
    if set(deep_by_id) != expected_ids:
        raise ValueError(
            f"Deep corpus differs from candidates: missing={sorted(expected_ids-set(deep_by_id))} extra={sorted(set(deep_by_id)-expected_ids)}"
        )

    candidates_by_pattern: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        for pattern_id in candidate["pattern_ids"]:
            candidates_by_pattern[pattern_id].append(candidate)

    output_dir.mkdir(parents=True, exist_ok=True)
    patterns = []
    for pattern_id in sorted(atlas_by_id):
        pattern = atlas_by_id[pattern_id]
        selected = sorted(candidates_by_pattern[pattern_id], key=lambda row: row["episode_id"])
        confusable_ids = sorted(
            set(pattern.get("confusable_with", []))
            | {
                other_id
                for candidate in selected
                for other_id in candidate["pattern_ids"]
                if other_id != pattern_id
            }
        )
        records = [
            {
                "selection_roles": candidate["roles"],
                "other_current_pattern_ids": [
                    item for item in candidate["pattern_ids"] if item != pattern_id
                ],
                "deep_episode": deep_by_id[candidate["episode_id"]],
            }
            for candidate in selected
        ]
        lines = [
            f"# Deep challenge source: {pattern_id}",
            "",
            "Counts are selected-case support, not prevalence. Conference outcomes are withheld.",
            "",
            "## Target provisional Atlas card",
            "",
            "```json",
            json.dumps(pattern, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Currently overlapping/confusable Atlas cards",
            "",
            "```json",
            json.dumps([atlas_by_id[item] for item in confusable_ids], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Selected Deep episodes",
            "",
            "```jsonl",
            *[json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records],
            "```",
            "",
        ]
        source = "\n".join(lines)
        source_path = output_dir / f"pattern-source-{pattern_id}.md"
        source_path.write_text(source, encoding="utf-8")
        patterns.append(
            {
                "pattern_id": pattern_id,
                "candidate_episode_ids": [row["episode_id"] for row in selected],
                "confusable_pattern_ids": confusable_ids,
                "source_path": str(source_path),
                "source_characters": len(source),
            }
        )
    manifest = {
        "version": 1,
        "source_atlas": str(synthesis_dir / "final-pattern-atlas.json"),
        "source_deep_manifest": str(deep_dir / "manifest.json"),
        "pattern_count": len(patterns),
        "patterns": patterns,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "PATTERN_CHALLENGE_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deep-dir", type=Path, default=DEFAULT_DEEP_DIR)
    parser.add_argument("--synthesis-dir", type=Path, default=DEFAULT_SYNTHESIS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    result = prepare(args.deep_dir, args.synthesis_dir, args.output_dir)
    print(json.dumps({"patterns": result["pattern_count"], "characters": sum(row["source_characters"] for row in result["patterns"])}))


if __name__ == "__main__":
    main()
