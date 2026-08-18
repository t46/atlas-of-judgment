"""Repack a singleton review corpus into auditable two-review packets."""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path


def review_section(path: Path, ordinal: int) -> str:
    text = path.read_text(encoding="utf-8")
    marker = "## Review 01:"
    start = text.index(marker)
    section = text[start:]
    if ordinal != 1:
        section = section.replace(marker, f"## Review {ordinal:02d}:", 1)
    return section.rstrip()


def prepare(source: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to replace existing paired corpus: {output}")
    source = source.resolve()
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    source_rows = source_manifest["shards"]
    if len(source_rows) % 2:
        raise ValueError("singleton control corpus must have an even number of shards")
    staging = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    staging.mkdir(parents=True)
    shards: list[dict] = []
    try:
        for offset in range(0, len(source_rows), 2):
            rows = source_rows[offset:offset + 2]
            reference_shards = {row["reference_shard"] for row in rows}
            if len(reference_shards) != 1:
                raise ValueError(f"control pair crosses reference shards: {rows}")
            shard = len(shards) + 1
            metadata_rows = []
            sections = []
            for ordinal, row in enumerate(rows, 1):
                suffix = f"{int(row['shard']):05d}"
                metadata = json.loads(
                    (source / f"source-shard-{suffix}.json").read_text(encoding="utf-8")
                )
                metadata_rows.extend(metadata["reviews"])
                sections.append(
                    review_section(source / f"source-shard-{suffix}.md", ordinal)
                )
            reference_shard = reference_shards.pop()
            text = f"""# ICLR 2026 compact reviewer-logic paired control {shard:05d}

This outcome-blind packet contains exactly two independent machine-produced
initial_blind memos. Extract each human reviewer's evaluation logic separately
under the supplied compact protocol. Never transfer evidence, claims, or IDs
between reviews.

reference_shard={reference_shard}

{chr(10).join(chr(10) + section for section in sections).lstrip()}
"""
            suffix = f"{shard:05d}"
            (staging / f"source-shard-{suffix}.md").write_text(text, encoding="utf-8")
            (staging / f"source-shard-{suffix}.json").write_text(
                json.dumps(
                    {
                        "shard": shard,
                        "reference_shard": reference_shard,
                        "reviews": metadata_rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            shards.append(
                {
                    "shard": shard,
                    "reference_shard": reference_shard,
                    "review_count": 2,
                    "review_ids": [row["review_id"] for row in metadata_rows],
                    "memo_chars": sum(row["memo_chars"] for row in rows),
                    "source_chars": len(text),
                }
            )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    manifest = {
        "version": 1,
        "scope": "ICLR 2026 Qwen two-review control",
        "database": source_manifest["database"],
        "source": str(source),
        "outcome_blind": True,
        "population_census": False,
        "review_count": sum(row["review_count"] for row in shards),
        "shard_count": len(shards),
        "max_reviews_per_shard": 2,
        "shards": shards,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    staging.replace(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
