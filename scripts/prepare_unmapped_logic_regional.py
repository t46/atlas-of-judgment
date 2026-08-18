"""Prepare two independent regional syntheses over local unmapped discoveries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DIRECTORY = Path(
    "data/analysis/iclr/episode-reclassification-3135/unmapped-discovery"
)


def _source_records(directory: Path, group: int) -> dict[str, dict[str, Any]]:
    text = (directory / f"source-group-{group:02d}.md").read_text()
    block = text.split("```jsonl\n", 1)[1].split("\n```", 1)[0]
    return {row["episode_id"]: row for row in map(json.loads, block.splitlines()) if row}


def protocol() -> str:
    return """# Regional synthesis protocol for Atlas-external logic

Synthesize one regional packet spanning four independent local groups. Merge
only by operative inference endpoint, never by topic, sentiment, generic label,
or requested action. Reassess local singletons: cross-group recurrence can turn
them into a regional pattern. Local candidate patterns are fallible hypotheses.

Write `regional-patterns-NN.json` and `regional-report-NN.md`. JSON contains:

- `region`;
- `regional_patterns`;
- `episode_assignments`, exactly once per supplied episode in source order;
- `local_pattern_decisions`, exactly once per supplied local pattern;
- `notes`.

Episode assignments contain `episode_id`, `disposition`
(`regional_pattern`, `atlas_boundary`, or `singleton_new_logic`),
`regional_pattern_ids`, `nearest_atlas_pattern_ids`, `reason`, and `confidence`.
Local decisions contain `local_pattern_id`, `decision` (`merge`,
`atlas_boundary`, `retire`), `regional_pattern_ids`, and `reason`.

Each regional pattern contains `regional_pattern_id` (`R-NN-PNN`),
`provisional_name`, a six-field `chain_template` (`inspected_object`,
`observation`, `evaluative_standard`, `reasoning_bridge`, `judgment`,
`repair_role`), `inclusion_rule`, `exclusion_rule`,
`supporting_local_pattern_ids`, `member_episode_ids`,
`representative_episode_ids`, `boundary_episode_ids`,
`counterexample_episode_ids`, `nearest_atlas_patterns` with decisive
differences, `variants`, `confidence`, and `notes`.

A regional candidate needs at least three episodes from at least two reviews.
State why it is not A-P01–A-P10. Keep positive contribution credit separate
from calibrated credit A-P09 unless a reservation bounds the same positive
core. Keep explanatory adequacy separate from causal attribution A-P01 unless
an intervention isolates credit. Keep substantive conceptual intelligibility
separate from reconstructability A-P02. Keep publication-facing communication
quality separate from technical auditability. Do not turn source insufficiency
into a substantive pattern. Counts are discovery-sample support, not prevalence.
"""


def prepare(directory: Path) -> dict[str, Any]:
    regions = []
    for region, groups in enumerate((range(1, 5), range(5, 9)), 1):
        local_payloads = []
        episodes = []
        seen = set()
        for group in groups:
            payload = json.loads((directory / f"local-patterns-{group:02d}.json").read_text())
            local_payloads.append(payload)
            records = _source_records(directory, group)
            for assignment in payload["assignments"]:
                if assignment["disposition"] == "source_insufficient":
                    continue
                episode_id = assignment["episode_id"]
                record = records[episode_id]
                episodes.append(
                    {
                        "episode_id": episode_id,
                        "paper_id": record["paper_id"],
                        "review_id": record["review_id"],
                        "abstract_signature": record["abstract_signature"],
                        "concrete_signature": record["concrete_signature"],
                        "inspected_objects": record["inspected_objects"],
                        "observations": record["observations"],
                        "reasoning_bridge": record["reasoning_bridge"],
                        "judgments": record["judgments"],
                        "requested_tests_or_changes": record["requested_tests_or_changes"],
                        "local_assignment": assignment,
                    }
                )
                if episode_id in seen:
                    raise ValueError(f"duplicate regional episode: {episode_id}")
                seen.add(episode_id)
        local_patterns = [
            pattern
            for payload in local_payloads
            for pattern in payload["candidate_patterns"]
        ]
        lines = [
            f"# Regional unmapped-logic synthesis {region:02d}", "",
            f"Local groups: {groups.start}-{groups.stop - 1}",
            f"Episodes excluding source-insufficient: {len(episodes)}",
            f"Local candidates: {len(local_patterns)}", "",
            "## Local candidate hypotheses", "", "```json",
            json.dumps(local_patterns, ensure_ascii=False, indent=2), "```", "",
            "## Episode evidence and local dispositions", "", "```jsonl",
            *[json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in episodes],
            "```", "",
        ]
        rendered = "\n".join(lines)
        (directory / f"regional-source-{region:02d}.md").write_text(rendered)
        regions.append(
            {
                "region": region,
                "groups": list(groups),
                "episode_ids": [row["episode_id"] for row in episodes],
                "review_by_episode": {row["episode_id"]: row["review_id"] for row in episodes},
                "local_pattern_ids": [row["candidate_pattern_id"] for row in local_patterns],
                "source_characters": len(rendered),
            }
        )
    manifest = {"version": 1, "regions": regions}
    (directory / "regional-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (directory / "REGIONAL_PROTOCOL.md").write_text(protocol())
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    result = prepare(args.directory)
    print(json.dumps({"regions": len(result["regions"]), "characters": [x["source_characters"] for x in result["regions"]]}))


if __name__ == "__main__":
    main()
