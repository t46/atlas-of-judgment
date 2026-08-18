"""Prepare a global adjudication packet from all independent pattern challenges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_CHALLENGE_DIR = Path("data/analysis/iclr/episode-deep-63/pattern-challenges")
DEFAULT_SYNTHESIS_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")


def protocol() -> str:
    return """# Global Atlas adjudication protocol

Read `atlas-adjudication-source.md` and write exactly:

1. `atlas-adjudication.json`
2. `atlas-adjudication-report.md`

The ten pattern challenges were produced independently. Reconcile them without
assuming agreement. This selected Deep sample can test boundaries but cannot
estimate prevalence or automatically remap all 3,135 Lite episodes. Preserve
the original Atlas unless the Deep evidence identifies a specific revision;
mark splits and merges as pending full-corpus reclassification rather than
pretending selected cases prove a final taxonomy.

The JSON must contain `version`, `atlas_disposition`, `global_findings`,
`pattern_decisions`, `cross_pattern_boundaries`, `limits`, and
`recommended_next_step`.

- `atlas_disposition`: `stable`, `targeted_revision`, `major_reclassification`,
  or `underdetermined`.
- `global_findings` and `limits`: nonempty lists of concrete strings.
- Each of the ten `pattern_decisions` contains:
  - `pattern_id`;
  - `decision`: `retain`, `revise`, `split_pending`, `merge_pending`,
    `retire_pending`, or `underdetermined`;
  - `recommended_name`, `clarified_core_logic`, `inclusion_rule`, and
    `exclusion_rule`;
  - `accepted_candidate_episode_ids`, `disputed_candidate_episode_ids`, and
    `removed_candidate_episode_ids`, which partition that pattern's selected
    candidate IDs exactly;
  - `strongest_supporting_episode_id`, which must be one accepted candidate,
    and `decisive_boundary_episode_id`, which must be one disputed or removed
    candidate;
  - `merge_with` (other Atlas IDs), `split_axes` (specific proposed logics),
    `rationale`, `confidence`, and `full_corpus_action`. Each `rationale` must
    explain concretely why `strongest_supporting_episode_id` positively
    instantiates that pattern's operative standard and why
    `decisive_boundary_episode_id` does not. Do not label an exclusion example
    as the strongest support, and do not reuse a generic rationale template
    across patterns.
- Each `cross_pattern_boundaries` item contains `left_pattern_id`,
  `right_pattern_id`, `distinction`, `decisive_deep_fields`, and supporting
  `episode_ids`. Every `distinction` must name the left pattern's operative
  inference endpoint and the right pattern's different inference endpoint in
  concrete domain language—for example, causal credit versus comparative
  distinctiveness. A sentence that merely says each pattern applies its own or
  a different standard is invalid. Write 6–10 high-value boundaries, not a
  mechanical enumeration of the manifest's entire confusable graph. Each
  distinction should be at most 90 words and use the cited episode(s) to state
  the concrete fork in the inference—for example, whether an ablation assigns
  causal credit or merely completes the evidence package. Do not copy either
  card's full inclusion rule and do not reuse a sentence frame across pairs.

Do not split on valence, paper topic, or request surface. Separate patterns
when their operative standards or inference templates differ. Merge only when
those are materially the same. Treat `insufficient` and analytic-wrapper-only
records as uncertainty, not negative evidence. Candidate IDs may be multi-label
and can support a boundary between patterns.

The report should answer: what human reviewers inspect; what turns an
observation into good/bad judgment; which apparently similar requests embody
different logic; which original Atlas boundaries survive; what must change;
and what Deep still cannot establish. It must be one coherent Markdown
document with exactly one H1 title; do not prepend a second summary document or
append another report. Validate before finishing.
"""


def prepare(challenge_dir: Path, synthesis_dir: Path) -> dict:
    manifest = json.loads((challenge_dir / "manifest.json").read_text(encoding="utf-8"))
    atlas = json.loads((synthesis_dir / "final-pattern-atlas.json").read_text(encoding="utf-8"))
    challenges = []
    for row in manifest["patterns"]:
        pattern_id = row["pattern_id"]
        json_path = challenge_dir / f"pattern-challenge-{pattern_id}.json"
        report_path = challenge_dir / f"pattern-challenge-{pattern_id}-report.md"
        if not json_path.exists() or not report_path.exists():
            raise ValueError(f"missing challenge output for {pattern_id}")
        challenges.append(
            {
                "manifest": row,
                "structured_challenge": json.loads(json_path.read_text(encoding="utf-8")),
                "report": report_path.read_text(encoding="utf-8").strip(),
            }
        )
    lines = [
        "# Global selected-Deep Atlas adjudication source",
        "",
        "Decision/outcome metadata is absent. Selected support is not prevalence.",
        "",
        "## Original provisional Atlas",
        "",
        "```json",
        json.dumps(atlas, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Ten independent pattern challenges",
        "",
    ]
    for challenge in challenges:
        pattern_id = challenge["manifest"]["pattern_id"]
        lines.extend(
            [
                f"### {pattern_id}",
                "",
                "```json",
                json.dumps(challenge["structured_challenge"], ensure_ascii=False, indent=2),
                "```",
                "",
                challenge["report"],
                "",
            ]
        )
    source = "\n".join(lines)
    (challenge_dir / "atlas-adjudication-source.md").write_text(source, encoding="utf-8")
    (challenge_dir / "ATLAS_ADJUDICATION_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    adjudication_manifest = {
        "version": 1,
        "pattern_ids": [row["pattern_id"] for row in manifest["patterns"]],
        "candidate_episode_ids_by_pattern": {
            row["pattern_id"]: row["candidate_episode_ids"] for row in manifest["patterns"]
        },
        "source_characters": len(source),
    }
    (challenge_dir / "atlas-adjudication-manifest.json").write_text(
        json.dumps(adjudication_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return adjudication_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--challenge-dir", type=Path, default=DEFAULT_CHALLENGE_DIR)
    parser.add_argument("--synthesis-dir", type=Path, default=DEFAULT_SYNTHESIS_DIR)
    args = parser.parse_args()
    result = prepare(args.challenge_dir, args.synthesis_dir)
    print(json.dumps({"patterns": len(result["pattern_ids"]), "characters": result["source_characters"]}))


if __name__ == "__main__":
    main()
