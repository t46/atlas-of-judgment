"""Prepare the final cross-meta reviewer-evaluation Atlas synthesis packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")


def protocol() -> str:
    return """# Protocol for the final Evaluation Logic Atlas synthesis

Read `final-source.md` and write exactly `final-pattern-map.json` and
`atlas-report.md`. This is an inductive map of reviewer evaluation logic, not a
taxonomy of paper topics, weaknesses, requests, valence, or decisions.

The JSON object contains `patterns`, `unmerged_meta_pattern_ids`,
`global_contrasts`, and `limitations`. Produce 10–18 Atlas patterns. Each has:

- `pattern_id`: `A-PNN`
- `name`
- `core_logic`: string lists `inspected_object_types`, `observation_forms`,
  `judgment_forms`, `request_roles`; specific strings `evaluative_standard` and
  `reasoning_template`
- `source_meta_pattern_ids` from at least two different meta groups
- `variants`: a list of `{name, distinction, source_meta_pattern_ids}`
- `inclusion_rule`, `exclusion_rule`, and `merge_rationale`
- 2–6 `representative_episode_ids`, at most 6 `boundary_episode_ids`, and at
  most 6 `counterexample_episode_ids`
- `confusable_with`, `confidence`, and string-list `notes`

Every source meta-pattern must map to one or more Atlas patterns or appear once
in `unmerged_meta_pattern_ids`; keep unmerged below 15%. Merge only homologous
inspect -> observe -> standard/counterfactual -> inference -> judgment logic.
Do not merge construct validity with reproducibility, causal attribution with
fair comparison, transfer with perturbation robustness, novelty with empirical
superiority, or formal correctness with operational usefulness unless the
source cards explicitly show one shared warrant. No umbrella or generic
claim-to-evidence pattern is allowed.

`global_contrasts` is a list of concrete same-observation/different-judgment or
same-judgment/different-warrant contrasts, each with episode IDs.
`limitations` is a string list. The Markdown report should be a deep analytic
account of what reviewers inspect, the standards they use, how those standards
turn observations into judgments, what requested changes discriminate, how
patterns relate and differ, surprising cases, and what cannot be claimed from
this stratified pilot. Counts are pilot support, not ICLR prevalence.
"""


def prepare(directory: Path) -> dict[str, Any]:
    meta_manifest = json.loads((directory / "meta-manifest.json").read_text(encoding="utf-8"))
    meta_patterns: dict[str, dict[str, Any]] = {}
    lines = ["# Final Evaluation Logic Atlas source", ""]
    for meta_group in range(1, meta_manifest["meta_group_count"] + 1):
        payload = json.loads(
            (directory / f"meta-patterns-{meta_group:02d}.json").read_text(encoding="utf-8")
        )
        lines.extend(
            [
                f"## Meta group {meta_group:02d} pattern cards",
                "",
                "```json",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "```",
                "",
                f"## Meta group {meta_group:02d} report",
                "",
                (directory / f"meta-report-{meta_group:02d}.md").read_text(encoding="utf-8").strip(),
                "",
            ]
        )
        meta_row = next(row for row in meta_manifest["meta_groups"] if row["meta_group"] == meta_group)
        source_patterns = meta_row["source_patterns"]
        for pattern in payload["patterns"]:
            member_episode_ids = sorted(
                {
                    episode_id
                    for source_id in pattern["source_pattern_ids"]
                    for episode_id in source_patterns[source_id]["member_episode_ids"]
                }
            )
            meta_patterns[pattern["pattern_id"]] = {
                "meta_group": meta_group,
                "source_pattern_ids": pattern["source_pattern_ids"],
                "member_episode_ids": member_episode_ids,
            }
    rendered = "\n".join(lines).rstrip() + "\n"
    (directory / "final-source.md").write_text(rendered, encoding="utf-8")
    manifest = {
        "version": 1,
        "meta_pattern_count": len(meta_patterns),
        "meta_patterns": meta_patterns,
        "episode_ids": sorted(
            {episode_id for row in meta_patterns.values() for episode_id in row["member_episode_ids"]}
        ),
        "source_characters": len(rendered),
    }
    (directory / "final-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "FINAL_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    result = prepare(args.directory)
    print(json.dumps({"meta_patterns": result["meta_pattern_count"], "episodes": len(result["episode_ids"]), "characters": result["source_characters"]}))


if __name__ == "__main__":
    main()
