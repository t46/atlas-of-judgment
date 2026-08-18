"""Audit exact cross-review text reuse in Episode Lite JSONL outputs."""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_GLOB = "data/analysis/iclr/episode-lite-1000/episodes-shard-*.jsonl"
WHITESPACE_RE = re.compile(r"\s+")


def text_fields(episode: dict[str, Any]) -> Iterable[tuple[str, str]]:
    chain = episode.get("chain", {})
    for field in (
        "inspected_objects",
        "observations",
        "reasoning_bridge",
        "judgments",
        "requested_tests_or_changes",
    ):
        for claim in chain.get(field, []):
            yield field, claim.get("text", "")
    for field in ("concrete", "abstract"):
        yield f"signature_{field}", episode.get("signatures", {}).get(field, "")


def normalize(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip().casefold()


def audit(paths: list[Path], min_reviews: int) -> dict[str, Any]:
    occurrences: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "reviews": set(), "episodes": set(), "text": ""}
    )
    instances_by_field: dict[str, int] = defaultdict(int)
    episode_count = 0
    review_ids: set[str] = set()

    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            episode = json.loads(line)
            episode_count += 1
            review_id = episode["source"]["review_id"]
            review_ids.add(review_id)
            for field, text in text_fields(episode):
                normalized = normalize(text)
                if not normalized:
                    continue
                instances_by_field[field] += 1
                row = occurrences[(field, normalized)]
                row["count"] += 1
                row["reviews"].add(review_id)
                row["episodes"].add(episode["episode_id"])
                row["text"] = text

    repeated = []
    for (field, _), row in occurrences.items():
        if len(row["reviews"]) < min_reviews:
            continue
        repeated.append(
            {
                "field": field,
                "text": row["text"],
                "occurrence_count": row["count"],
                "distinct_review_count": len(row["reviews"]),
                "review_ids": sorted(row["reviews"]),
                "episode_ids": sorted(row["episodes"]),
            }
        )
    repeated.sort(
        key=lambda row: (
            -row["distinct_review_count"],
            -row["occurrence_count"],
            row["field"],
            row["text"],
        )
    )

    field_summary = {}
    for field in sorted(instances_by_field):
        field_rows = [
            row for (row_field, _), row in occurrences.items() if row_field == field
        ]
        field_summary[field] = {
            "instances": instances_by_field[field],
            "unique_normalized_texts": len(field_rows),
            "texts_reused_across_threshold_reviews": sum(
                len(row["reviews"]) >= min_reviews for row in field_rows
            ),
            "max_distinct_review_count": max(
                (len(row["reviews"]) for row in field_rows), default=0
            ),
        }

    return {
        "input_files": [str(path) for path in paths],
        "input_file_count": len(paths),
        "episode_count": episode_count,
        "reviews_with_episodes": len(review_ids),
        "minimum_distinct_reviews": min_reviews,
        "field_summary": field_summary,
        "cross_review_exact_duplicates": repeated,
        "cross_review_exact_duplicate_count": len(repeated),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-glob", default=DEFAULT_GLOB)
    parser.add_argument("--min-reviews", type=int, default=3)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.min_reviews < 2:
        parser.error("--min-reviews must be at least 2")
    paths = sorted(Path(path) for path in glob.glob(args.input_glob))
    if not paths:
        parser.error(f"--input-glob matched no files: {args.input_glob}")
    result = audit(paths, args.min_reviews)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
