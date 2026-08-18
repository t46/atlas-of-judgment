"""Prepare all ICLR 2026 isolated-review jobs for full layered analysis."""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from pathlib import Path

from prepare_pilot import (
    INITIAL_SYSTEM_PROMPT,
    create_output,
    decision_bucket,
    initial_user_prompt,
    parse_rating,
)


DEFAULT_SOURCE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_OUTPUT = Path("data/analysis/iclr/production-2026.sqlite3")


def flush(
    output: sqlite3.Connection,
    papers: list[tuple[object, ...]],
    jobs: list[tuple[object, ...]],
) -> None:
    if not papers and not jobs:
        return
    with output:
        output.executemany(
            """
            INSERT INTO sample_papers (
                paper_id, decision_bucket, review_count, comment_count,
                rating_mean, rating_variance
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            papers,
        )
        output.executemany(
            """
            INSERT INTO jobs (
                job_id, stage, paper_id, review_id, system_prompt,
                user_prompt, max_tokens
            ) VALUES (?, 'initial_blind', ?, ?, ?, ?, 4000)
            """,
            jobs,
        )
    papers.clear()
    jobs.clear()


def prepare(source_path: Path, output_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    output = create_output(output_path)
    try:
        existing_jobs = output.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        existing_papers = output.execute("SELECT COUNT(*) FROM sample_papers").fetchone()[0]
        if existing_jobs or existing_papers:
            raise RuntimeError(
                "refusing to modify a non-empty production database; "
                "choose a new --output path"
            )

        rows = source.execute(
            """
            SELECT p.forum_id, p.title, p.abstract, p.decision,
                   p.review_count, p.comment_count,
                   m.note_id, m.content_text, m.content_json
            FROM papers p
            JOIN messages m
              ON m.year=p.year AND m.forum_id=p.forum_id
            WHERE p.year=2026 AND p.review_count>0
              AND m.kind='official_review'
            ORDER BY p.forum_id, COALESCE(m.cdate, 0), m.note_id
            """
        )

        paper_batch: list[tuple[object, ...]] = []
        job_batch: list[tuple[object, ...]] = []
        current_id: str | None = None
        current_meta: sqlite3.Row | None = None
        current_ratings: list[float] = []
        paper_count = 0
        review_count = 0

        def finish_paper() -> None:
            nonlocal paper_count
            if current_id is None or current_meta is None:
                return
            paper_batch.append(
                (
                    current_id,
                    decision_bucket(current_meta["decision"]),
                    current_meta["review_count"],
                    current_meta["comment_count"],
                    statistics.mean(current_ratings) if current_ratings else None,
                    statistics.pvariance(current_ratings)
                    if len(current_ratings) >= 2
                    else 0.0,
                )
            )
            paper_count += 1

        for row in rows:
            if row["forum_id"] != current_id:
                finish_paper()
                current_id = row["forum_id"]
                current_meta = row
                current_ratings = []
            rating = parse_rating(row["content_json"])
            if rating is not None:
                current_ratings.append(rating)
            job_batch.append(
                (
                    f"initial:{row['note_id']}",
                    row["forum_id"],
                    row["note_id"],
                    INITIAL_SYSTEM_PROMPT,
                    initial_user_prompt(
                        row["title"],
                        row["abstract"],
                        row["content_text"],
                        row["note_id"],
                    ),
                )
            )
            review_count += 1

            if len(job_batch) >= 2_000 and current_id is not None:
                # Do not flush the current paper metadata until all of its
                # reviews have been observed; job rows have no FK to it.
                flush(output, paper_batch, job_batch)
                print(
                    f"prepared {paper_count:,} papers / {review_count:,} reviews",
                    flush=True,
                )

        finish_paper()
        flush(output, paper_batch, job_batch)

        stored_papers = output.execute("SELECT COUNT(*) FROM sample_papers").fetchone()[0]
        stored_jobs = output.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage='initial_blind'"
        ).fetchone()[0]
        expected = source.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(review_count), 0)
            FROM papers WHERE year=2026 AND review_count>0
            """
        ).fetchone()
        if (stored_papers, stored_jobs) != (expected[0], expected[1]):
            raise RuntimeError(
                "production preparation count mismatch: "
                f"stored={(stored_papers, stored_jobs)}, expected={tuple(expected)}"
            )
        print(
            f"complete: {stored_papers:,} papers / {stored_jobs:,} isolated-review jobs",
            flush=True,
        )
    finally:
        output.close()
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    prepare(args.source, args.output)


if __name__ == "__main__":
    main()
