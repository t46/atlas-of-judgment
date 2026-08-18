"""Prepare four fresh-agent packets that merge group-level logic patterns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")
GROUPS_PER_META = 5


def meta_protocol() -> str:
    return """# Protocol for cross-group meta-pattern synthesis

For one `meta-source-NN.md`, write `meta-patterns-NN.json` and
`meta-report-NN.md`. Work only from the supplied group pattern cards and group
reports. Do not read raw reviews or other meta outputs.

The JSON object contains `meta_group`, `patterns`,
`unmerged_source_pattern_ids`, and `coverage_notes`. Produce 8–14 provisional
meta-patterns. Each pattern contains:

- `pattern_id`: `MNN-PNN`
- `provisional_name`
- `logic`: nonempty string lists `inspected_object_types`,
  `observation_forms`, `judgment_forms`, `request_roles`; and specific strings
  `evaluative_standard`, `reasoning_template`
- `source_pattern_ids` drawn from at least two different source groups
- `inclusion_rule`, `exclusion_rule`, and `merge_rationale`
- 1–5 `representative_episode_ids`, at most 5 `boundary_episode_ids`, and at
  most 5 `counterexample_episode_ids`
- `confusable_with`, `confidence`, and a string-list `notes`

Every source pattern must be mapped to one or more meta-patterns or listed once
in `unmerged_source_pattern_ids`. Membership may be multi-label. Do not merge
by paper topic, requested experiment, valence, or outcome. Merge only when the
inspected-object role, observation form, evaluative standard/counterfactual,
reasoning bridge, and judgment relation are materially homologous. Split the
same observation under different standards and the same judgment under
different warrants. No umbrella, generic claim-to-evidence, or coverage
pattern is allowed. Keep unmerged source patterns below 15%.

The report explains recurring meta-logics, important variants, merge/split
decisions, contrasts, boundary cases, and still-unmerged patterns. Counts are
support in this synthesis packet, not population prevalence.
"""


def prepare(directory: Path) -> dict[str, Any]:
    group_manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    group_count = group_manifest["group_count"]
    if group_count % GROUPS_PER_META:
        raise ValueError("group count must be divisible by groups per meta packet")
    meta_rows = []
    for meta_group, start in enumerate(range(1, group_count + 1, GROUPS_PER_META), 1):
        groups = list(range(start, start + GROUPS_PER_META))
        source_patterns: dict[str, dict[str, Any]] = {}
        episode_ids: set[str] = set()
        lines = [
            f"# Meta-pattern source {meta_group:02d}",
            "",
            f"Source groups: {groups}",
            "",
        ]
        for group in groups:
            payload = json.loads(
                (directory / f"group-patterns-{group:02d}.json").read_text(encoding="utf-8")
            )
            lines.extend(
                [
                    f"## Group {group:02d} pattern cards",
                    "",
                    "```json",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    "```",
                    "",
                    f"## Group {group:02d} synthesis report",
                    "",
                    (directory / f"group-report-{group:02d}.md").read_text(encoding="utf-8").strip(),
                    "",
                ]
            )
            for pattern in payload["patterns"]:
                source_patterns[pattern["pattern_id"]] = {
                    "group": group,
                    "member_episode_ids": pattern["member_episode_ids"],
                }
                episode_ids.update(pattern["member_episode_ids"])
        rendered = "\n".join(lines).rstrip() + "\n"
        source_path = directory / f"meta-source-{meta_group:02d}.md"
        source_path.write_text(rendered, encoding="utf-8")
        meta_rows.append(
            {
                "meta_group": meta_group,
                "groups": groups,
                "source_pattern_count": len(source_patterns),
                "source_patterns": source_patterns,
                "episode_ids": sorted(episode_ids),
                "source_path": str(source_path),
                "source_characters": len(rendered),
            }
        )
    manifest = {
        "version": 1,
        "groups_per_meta": GROUPS_PER_META,
        "meta_group_count": len(meta_rows),
        "source_pattern_count": sum(row["source_pattern_count"] for row in meta_rows),
        "meta_groups": meta_rows,
    }
    (directory / "meta-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "META_PROTOCOL.md").write_text(meta_protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    manifest = prepare(args.directory)
    print(
        json.dumps(
            {
                "meta_groups": manifest["meta_group_count"],
                "source_patterns": manifest["source_pattern_count"],
                "characters": sum(row["source_characters"] for row in manifest["meta_groups"]),
            }
        )
    )


if __name__ == "__main__":
    main()
