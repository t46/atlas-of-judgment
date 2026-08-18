"""Repack one full-corpus source shard for packet-size/effort calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .prepare_episode_lite_2026_full import (
        ReviewMemo,
        readonly_connection,
        protocol,
        render_source,
    )
except ImportError:  # Direct script execution.
    from prepare_episode_lite_2026_full import (
        ReviewMemo,
        readonly_connection,
        protocol,
        render_source,
    )


DEFAULT_SOURCE = Path("data/analysis/iclr/episode-lite-2026-full")


def prepare(source: Path, source_shard: int, output: Path, packet_size: int) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to replace calibration directory: {output}")
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads(
        (source / f"source-shard-{source_shard:05d}.json").read_text(encoding="utf-8")
    )
    connection = readonly_connection(Path(source_manifest["database"]))
    try:
        reviews = []
        for item in metadata["reviews"]:
            row = connection.execute(
                "SELECT memo FROM memos WHERE job_id=?",
                (f"initial:{item['review_id']}",),
            ).fetchone()
            if row is None:
                raise ValueError(f"memo missing for {item['review_id']}")
            reviews.append(ReviewMemo(item["paper_id"], item["review_id"], row[0]))
    finally:
        connection.close()
    output.mkdir(parents=True)
    shards = []
    for shard, offset in enumerate(range(0, len(reviews), packet_size), 1):
        batch = reviews[offset:offset + packet_size]
        source_text = render_source(shard, batch)
        suffix = f"{shard:05d}"
        (output / f"source-shard-{suffix}.md").write_text(source_text, encoding="utf-8")
        (output / f"source-shard-{suffix}.json").write_text(
            json.dumps({
                "shard": shard,
                "reviews": [
                    {"paper_id": row.paper_id, "review_id": row.review_id}
                    for row in batch
                ],
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shards.append({
            "shard": shard,
            "review_count": len(batch),
            "memo_chars": sum(len(row.memo) for row in batch),
            "source_chars": len(source_text),
        })
    manifest = {
        "version": 1,
        "scope": f"packet calibration from full source shard {source_shard:05d}",
        "database": source_manifest["database"],
        "schema": source_manifest["schema"],
        "outcome_blind": True,
        "population_census": False,
        "review_count": len(reviews),
        "shard_count": len(shards),
        "max_reviews_per_shard": packet_size,
        "required_outputs": ["episodes", "coverage"],
        "shards": shards,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "AGENT_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--source-shard", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--packet-size", type=int, required=True)
    args = parser.parse_args()
    manifest = prepare(args.source, args.source_shard, args.output, args.packet_size)
    print(json.dumps({"reviews": manifest["review_count"], "shards": manifest["shard_count"]}))


if __name__ == "__main__":
    main()
