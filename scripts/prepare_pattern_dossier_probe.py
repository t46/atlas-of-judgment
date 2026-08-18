"""Prepare a bounded Lite-only packet for Pattern Dossier regeneration."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_PILOT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_SYNTHESIS_DIR = DEFAULT_PILOT_DIR / "synthesis"


def load_episodes(pilot_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        episode["episode_id"]: episode
        for path in sorted(pilot_dir.glob("episodes-shard-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for episode in [json.loads(line)]
    }


def prepare(
    pilot_dir: Path,
    synthesis_dir: Path,
    pattern_id: str,
    *,
    extra_examples: int = 16,
) -> dict[str, Any]:
    atlas = json.loads((synthesis_dir / "final-pattern-atlas.json").read_text(encoding="utf-8"))
    pattern = next(row for row in atlas["patterns"] if row["pattern_id"] == pattern_id)
    episodes = load_episodes(pilot_dir)
    source_manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
    review_bucket = {
        row["candidate"]["review_id"]: row["candidate"]["decision_bucket"]
        for row in source_manifest["reviews"]
    }
    roles: dict[str, set[str]] = defaultdict(set)
    for role, key in (
        ("representative", "representative_episode_ids"),
        ("boundary", "boundary_episode_ids"),
        ("counterexample", "counterexample_episode_ids"),
    ):
        for episode_id in pattern[key]:
            roles[episode_id].add(role)

    candidates = []
    for episode_id in pattern["member_episode_ids"]:
        episode = episodes[episode_id]
        judgments = episode["chain"]["judgments"]
        valence = judgments[0]["valence"] if judgments else "missing"
        bucket = review_bucket[episode["source"]["review_id"]]
        candidates.append((bucket, valence, episode_id))
    candidates.sort()
    seen_strata: set[tuple[str, str]] = set()
    for bucket, valence, episode_id in candidates:
        stratum = (bucket, valence)
        if stratum in seen_strata or episode_id in roles:
            continue
        roles[episode_id].add("stratified_extra")
        seen_strata.add(stratum)
        if sum("stratified_extra" in value for value in roles.values()) >= extra_examples:
            break

    records = []
    for episode_id, episode_roles in sorted(roles.items()):
        episode = episodes[episode_id]
        records.append(
            {
                "roles": sorted(episode_roles),
                "decision_bucket_for_descriptive_comparison_only": review_bucket[
                    episode["source"]["review_id"]
                ],
                "episode": episode,
            }
        )
    lines = [
        f"# Lite-only Pattern Dossier probe: {pattern_id}",
        "",
        "This packet deliberately contains no source memo or raw review text.",
        "Decision bucket metadata may be described after the logic is reconstructed; it must not be used to infer the logic.",
        "",
        "## Validated Atlas pattern card and derived pilot support",
        "",
        "```json",
        json.dumps(pattern, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Representative, boundary, counterexample, and stratified Lite episodes",
        "",
        "```jsonl",
        *[json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records],
        "```",
    ]
    output_path = synthesis_dir / f"dossier-probe-{pattern_id}-source.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"pattern_id": pattern_id, "episode_count": len(records), "output": str(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--synthesis-dir", type=Path, default=DEFAULT_SYNTHESIS_DIR)
    parser.add_argument("--pattern-id", default="A-P01")
    parser.add_argument("--extra-examples", type=int, default=16)
    args = parser.parse_args()
    print(json.dumps(prepare(args.pilot_dir, args.synthesis_dir, args.pattern_id, extra_examples=args.extra_examples)))


if __name__ == "__main__":
    main()
