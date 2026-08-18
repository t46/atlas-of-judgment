"""Prepare blinded comparisons of direct and layered paper-level memos."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

from prepare_pilot import line_number


DEFAULT_SOURCE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_PILOT = Path("data/analysis/iclr/pilot.sqlite3")

SYSTEM_PROMPT = """You are evaluating two machine-produced qualitative metascience memos against the same primary OpenReview forum.

Judge which memo better serves the research question: what each human reviewer inspected, what they observed, what standards, comparisons, assumptions, counterfactuals, or alternative explanations connected those observations to positive/negative/uncertain evaluation, what proposed changes were intended to find out, how author responses changed or failed to change reasoning, and how reviewer judgments related to the outcome.

Do not judge the underlying paper. Evaluate faithfulness to the supplied forum, depth of evaluative-logic reconstruction, preservation of distinct and minority reviewer positions, chronology without hindsight distortion, separation of explicit evidence from interpretation, useful attention to improvement proposals, and avoidance of unsupported speculation. Do not prefer length, polish, or number of headings for their own sake.

The memo labels are randomly assigned. Begin with exactly one line: `PREFERRED: A`, `PREFERRED: B`, or `PREFERRED: TIE`. Then write a concise but evidence-grounded comparison citing forum line IDs and memo line IDs. Write in English Markdown prose."""


def comparison_prompt(
    source: sqlite3.Connection,
    paper_id: str,
    memo_a: str,
    memo_b: str,
) -> str:
    paper = source.execute(
        "SELECT title, abstract FROM papers WHERE year = 2026 AND forum_id = ?",
        (paper_id,),
    ).fetchone()
    messages = source.execute(
        """
        SELECT note_id, replyto, kind, role, signature, content_text
        FROM messages
        WHERE year = 2026 AND forum_id = ?
        ORDER BY COALESCE(cdate, 0), note_id
        """,
        (paper_id,),
    ).fetchall()
    sections = [
        "# Paper context",
        f"Title: {paper['title']}",
        "Abstract (context only):",
        line_number(paper["abstract"], "AB"),
        "# Primary public forum",
    ]
    for message in messages:
        prefix = f"F-{message['note_id']}"
        sections.extend(
            [
                f"## {prefix}: {message['kind']} by {message['role']} ({message['signature'] or 'unknown'})",
                f"note_id={message['note_id']} replyto={message['replyto'] or 'paper'}",
                line_number(message["content_text"], prefix),
            ]
        )
    sections.extend(
        [
            "# Candidate memo A",
            line_number(memo_a, "MA"),
            "# Candidate memo B",
            line_number(memo_b, "MB"),
            "# Task",
            "Choose the more useful and faithful analytic memo under the system criteria, or TIE when neither has a meaningful overall advantage.",
        ]
    )
    return "\n\n".join(sections)


def prepare(source_path: Path, pilot_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    pilot = sqlite3.connect(pilot_path)
    pilot.row_factory = sqlite3.Row
    try:
        missing = pilot.execute(
            """
            SELECT COUNT(*) FROM sample_papers s
            WHERE NOT EXISTS (
                SELECT 1 FROM memos m
                WHERE m.paper_id=s.paper_id AND m.stage='forum_direct'
            ) OR NOT EXISTS (
                SELECT 1 FROM memos m
                WHERE m.paper_id=s.paper_id AND m.stage='paper_synthesis'
            )
            """
        ).fetchone()[0]
        if missing:
            raise RuntimeError(f"{missing} sampled papers lack one or both candidate memos")

        inserted = 0
        with pilot:
            for paper in pilot.execute(
                "SELECT paper_id FROM sample_papers ORDER BY paper_id"
            ).fetchall():
                paper_id = paper["paper_id"]
                memos = {
                    row["stage"]: row["memo"]
                    for row in pilot.execute(
                        """
                        SELECT stage, memo FROM memos
                        WHERE paper_id=? AND stage IN ('forum_direct', 'paper_synthesis')
                        """,
                        (paper_id,),
                    ).fetchall()
                }
                direct_first = hashlib.sha256(paper_id.encode()).digest()[0] % 2 == 0
                if direct_first:
                    memo_a, memo_b = memos["forum_direct"], memos["paper_synthesis"]
                    mapping = "A=forum_direct;B=paper_synthesis"
                else:
                    memo_a, memo_b = memos["paper_synthesis"], memos["forum_direct"]
                    mapping = "A=paper_synthesis;B=forum_direct"
                cursor = pilot.execute(
                    """
                    INSERT OR IGNORE INTO jobs (
                        job_id, stage, paper_id, review_id, system_prompt,
                        user_prompt, max_tokens, status
                    ) VALUES (?, 'method_comparison', ?, ?, ?, ?, 3000, 'pending')
                    """,
                    (
                        f"compare:{paper_id}",
                        paper_id,
                        mapping,
                        SYSTEM_PROMPT,
                        comparison_prompt(source, paper_id, memo_a, memo_b),
                    ),
                )
                inserted += cursor.rowcount
        total = pilot.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage='method_comparison'"
        ).fetchone()[0]
        print(f"method_comparison: inserted {inserted}, total {total}")
    finally:
        pilot.close()
        source.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    prepare(args.source, args.pilot)
