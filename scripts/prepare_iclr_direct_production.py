"""Prepare one open-ended whole-forum job for every reviewed ICLR paper."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from prepare_pilot import create_output, forum_user_prompt


DEFAULT_SOURCE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_OUTPUT = Path("data/analysis/iclr/direct-2018-2026.sqlite3")

DIRECT_SYSTEM_PROMPT = """You are conducting qualitative metascience research on human peer review.

Analyze the complete public review forum as an unfolding social and epistemic process. Your primary objects are the human reviewers: what each one inspects in the paper, what they count as good or bad, and the reasoning that connects observations to positive, negative, or uncertain evaluations. Reconstruct how reviewer standards, comparisons, assumptions, counterfactuals, alternative explanations, and requested changes make those judgments intelligible. Preserve each reviewer's distinct logic, minority views, disagreement, uncertainty, and unresolved issues. Examine author responses, reviewer follow-ups, meta-review or decision reasoning, and how the final outcome combines, prioritizes, transforms, or disregards reviewer judgments.

Do not independently re-review the paper, impose a predetermined taxonomy, or treat the final decision as ground truth. Do not rewrite earlier reasoning to fit the outcome. Separate explicit statements from interpretation. Ground substantive claims with the supplied stable note-and-line IDs. The public forum is a snapshot and may not expose complete content-edit history; do not claim to recover hidden earlier versions. Write a rich, open-ended English Markdown memo focused on what human reviewers did, what evidence they used, and how they reasoned from evidence to evaluation."""


def create_production_output(path: Path) -> sqlite3.Connection:
    connection = create_output(path)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS production_papers (
            paper_id TEXT PRIMARY KEY,
            year INTEGER NOT NULL,
            forum_id TEXT NOT NULL,
            title TEXT,
            abstract TEXT,
            decision TEXT,
            review_count INTEGER NOT NULL,
            comment_count INTEGER NOT NULL,
            UNIQUE(year, forum_id)
        );
        CREATE INDEX IF NOT EXISTS production_papers_year_idx
            ON production_papers(year);
        """
    )
    connection.commit()
    return connection


def prepare(source_path: Path, output_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    output = create_production_output(output_path)
    try:
        rows = source.execute(
            """
            SELECT p.year, p.forum_id, p.title, p.abstract, p.decision,
                   p.review_count, p.comment_count,
                   m.note_id, m.replyto, m.kind, m.role, m.signature,
                   m.content_text
            FROM papers AS p
            JOIN messages AS m
              ON m.year = p.year AND m.forum_id = p.forum_id
            WHERE p.year BETWEEN 2018 AND 2026
              AND p.review_count > 0
            ORDER BY p.year, p.forum_id, COALESCE(m.cdate, 0), m.note_id
            """
        )

        current_key: tuple[int, str] | None = None
        current_meta: sqlite3.Row | None = None
        messages: list[sqlite3.Row] = []
        prepared = 0
        skipped = 0

        def flush_current() -> None:
            nonlocal prepared, skipped, current_key, current_meta, messages
            if current_key is None or current_meta is None:
                return
            year, forum_id = current_key
            paper_id = f"{year}:{forum_id}"
            job_id = f"forum:{year}:{forum_id}"
            existing = output.execute(
                "SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing:
                skipped += 1
            else:
                title = current_meta["title"] or "(title unavailable)"
                abstract = current_meta["abstract"] or "(abstract unavailable)"
                prompt = forum_user_prompt(title, abstract, messages)
                prompt += (
                    "\n\n# Task\n"
                    "Produce the open-ended whole-forum analytic memo described "
                    "in the system instruction. Preserve reviewer-specific "
                    "reasoning and chronology, and distinguish evidence from "
                    "interpretation."
                )
                with output:
                    output.execute(
                        """
                        INSERT OR IGNORE INTO production_papers (
                            paper_id, year, forum_id, title, abstract, decision,
                            review_count, comment_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            paper_id,
                            year,
                            forum_id,
                            current_meta["title"],
                            current_meta["abstract"],
                            current_meta["decision"],
                            current_meta["review_count"],
                            current_meta["comment_count"],
                        ),
                    )
                    output.execute(
                        """
                        INSERT OR IGNORE INTO jobs (
                            job_id, stage, paper_id, review_id, system_prompt,
                            user_prompt, max_tokens
                        ) VALUES (?, 'forum_direct', ?, NULL, ?, ?, 8000)
                        """,
                        (job_id, paper_id, DIRECT_SYSTEM_PROMPT, prompt),
                    )
                prepared += 1
            if (prepared + skipped) % 500 == 0:
                print(
                    f"prepared/skipped {prepared + skipped:,} papers "
                    f"(new={prepared:,}, existing={skipped:,})",
                    flush=True,
                )
            current_key = None
            current_meta = None
            messages = []

        for row in rows:
            key = (row["year"], row["forum_id"])
            if key != current_key:
                flush_current()
                current_key = key
                current_meta = row
            messages.append(row)
        flush_current()

        expected = source.execute(
            """
            SELECT COUNT(*) FROM papers
            WHERE year BETWEEN 2018 AND 2026 AND review_count > 0
            """
        ).fetchone()[0]
        actual = output.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage='forum_direct'"
        ).fetchone()[0]
        papers = output.execute("SELECT COUNT(*) FROM production_papers").fetchone()[0]
        if actual != expected or papers != expected:
            raise RuntimeError(
                f"preparation count mismatch: jobs={actual}, papers={papers}, "
                f"expected={expected}"
            )
        print(
            f"complete: {papers:,} papers / {actual:,} direct jobs "
            f"(new={prepared:,}, existing={skipped:,})",
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
