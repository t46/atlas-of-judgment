"""Create a one-review-per-request control corpus from an existing Qwen pilot.

The source reviews are recovered from the immutable production database.  Each
new shard records the original Luna/Qwen shard so the control remains auditable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

try:
    from scripts.prepare_episode_lite_2026_full import ReviewMemo, number_memo_lines
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from prepare_episode_lite_2026_full import ReviewMemo, number_memo_lines


DEFAULT_DATABASE = Path("data/analysis/iclr/production-2026.sqlite3")


def readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)


def selected_reference_shards(run: Path) -> list[int]:
    connection = readonly_connection(run / "state.sqlite3")
    try:
        return [
            int(row[0])
            for row in connection.execute("SELECT shard FROM requests ORDER BY shard")
        ]
    finally:
        connection.close()


def selected_reviews(metadata: dict, selection: str) -> list[dict]:
    reviews = metadata["reviews"]
    if selection == "all":
        return reviews
    return reviews[:1] + (reviews[-1:] if len(reviews) > 1 else [])


def load_review(connection: sqlite3.Connection, review_id: str) -> ReviewMemo:
    row = connection.execute(
        """
        SELECT j.paper_id, j.review_id, m.memo
        FROM jobs AS j JOIN memos AS m USING(job_id)
        WHERE j.stage='initial_blind' AND j.status='complete' AND j.review_id=?
        """,
        (review_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"completed initial_blind memo not found: {review_id}")
    return ReviewMemo(*row)


def render_source(shard: int, reference_shard: int, review: ReviewMemo) -> str:
    return f"""# ICLR 2026 compact reviewer-logic single-review control {shard:05d}

This outcome-blind packet contains exactly one machine-produced initial_blind
memo. Extract the human reviewer's evaluation logic under the supplied compact
protocol. Treat this review independently from every other request.

reference_shard={reference_shard}

## Review 01: {review.review_id}

paper_id={review.paper_id}; review_id={review.review_id}

{number_memo_lines(review.review_id, review.memo)}
"""


def prepare(
    database: Path, source: Path, selection_run: Path, output: Path, selection: str
) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to replace existing control corpus: {output}")
    staging = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    staging.mkdir(parents=True)
    database = database.resolve()
    source = source.resolve()
    selection_run = selection_run.resolve()
    connection = readonly_connection(database)
    shards: list[dict] = []
    seen: set[str] = set()
    try:
        for reference_shard in selected_reference_shards(selection_run):
            metadata = json.loads(
                (source / f"source-shard-{reference_shard:05d}.json").read_text(
                    encoding="utf-8"
                )
            )
            for selected in selected_reviews(metadata, selection):
                review_id = selected["review_id"]
                if review_id in seen:
                    continue
                seen.add(review_id)
                review = load_review(connection, review_id)
                shard = len(shards) + 1
                text = render_source(shard, reference_shard, review)
                suffix = f"{shard:05d}"
                (staging / f"source-shard-{suffix}.md").write_text(text, encoding="utf-8")
                (staging / f"source-shard-{suffix}.json").write_text(
                    json.dumps(
                        {
                            "shard": shard,
                            "reference_shard": reference_shard,
                            "reviews": [
                                {"paper_id": review.paper_id, "review_id": review.review_id}
                            ],
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
                        "review_count": 1,
                        "review_id": review.review_id,
                        "memo_chars": len(review.memo),
                        "source_chars": len(text),
                    }
                )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        connection.close()
    manifest = {
        "version": 1,
        "scope": (
            "ICLR 2026 Qwen one-review control: all reviews from each pilot shard"
            if selection == "all"
            else "ICLR 2026 Qwen one-review control: first and last review of each pilot shard"
        ),
        "database": str(database),
        "source": str(source),
        "selection_run": str(selection_run),
        "outcome_blind": True,
        "population_census": False,
        "review_count": len(shards),
        "shard_count": len(shards),
        "max_reviews_per_shard": 1,
        "shards": shards,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    staging.replace(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--selection-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", choices=("boundary", "all"), default="boundary")
    args = parser.parse_args()
    manifest = prepare(
        args.database, args.source, args.selection_run, args.output, args.selection
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
