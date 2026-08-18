"""Prepare hierarchical synthesis of the blinded method-comparison memos."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from prepare_pilot import line_number


DEFAULT_PILOT = Path("data/analysis/iclr/pilot.sqlite3")

BATCH_SYSTEM = """You are synthesizing blinded method-comparison judgments from a qualitative metascience pilot. Each input reports whether a direct whole-forum memo or a layered reviewer-by-reviewer/trajectory/paper-synthesis memo better reconstructed human reviewer activity against primary evidence.

Inductively identify why each method wins or loses: what reasoning, minority positions, chronology, improvement proposals, or evidential distinctions one preserves; where it becomes repetitive, speculative, hindsight-driven, or obscures the main logic; and whether apparent superiority may merely reflect verbosity. Do not impose a fixed rubric beyond what the judgments actually say. Preserve counterexamples and conditions. Write an evidence-grounded English Markdown synthesis citing input line IDs."""

GLOBAL_SYSTEM = """You are producing the final methodological conclusion for a qualitative metascience pilot comparing two scalable ways to analyze OpenReview forums: (1) direct whole-forum analysis, and (2) layered isolated-review, exchange-trajectory, and paper-level synthesis.

Synthesize the supplied batch-level analyses and numerical result. Explain what additional analytical value the layered method actually delivers, when the direct method is better, what failure modes remain, whether verbosity could bias the comparison, and what production design best balances depth and cost for all ICLR reviews. Treat the same-model blinded judge as evidence, not ground truth. Do not introduce a semantic taxonomy for reviewer behavior. Write a clear English Markdown methodological memo citing batch line IDs."""


def decoded_winner(mapping: str, memo: str) -> str:
    if memo.startswith("PREFERRED: A"):
        choice = "A"
    elif memo.startswith("PREFERRED: B"):
        choice = "B"
    elif memo.startswith("PREFERRED: TIE"):
        return "tie"
    else:
        raise ValueError("comparison memo does not begin with a valid preference")
    if (choice == "A" and mapping.startswith("A=forum_direct")) or (
        choice == "B" and mapping.endswith("B=forum_direct")
    ):
        return "forum_direct"
    return "paper_synthesis"


def prepare_batches(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT m.paper_id, m.review_id AS mapping, m.memo,
               s.decision_bucket
        FROM memos m JOIN sample_papers s USING(paper_id)
        WHERE m.stage='method_comparison'
        ORDER BY m.paper_id
        """
    ).fetchall()
    if len(rows) != 100:
        raise RuntimeError(f"expected 100 comparison memos, found {len(rows)}")
    inserted = 0
    with connection:
        for batch_index in range(5):
            batch = rows[batch_index * 20 : (batch_index + 1) * 20]
            sections = [f"# Comparison-judgment batch {batch_index + 1}"]
            for index, row in enumerate(batch, 1):
                prefix = f"J{index:02d}"
                winner = decoded_winner(row["mapping"], row["memo"])
                sections.extend(
                    [
                        f"## {prefix}: paper={row['paper_id']} decision={row['decision_bucket']} winner={winner}",
                        line_number(row["memo"], prefix),
                    ]
                )
            sections.extend(
                [
                    "# Task",
                    "Produce the inductive batch synthesis described in the system instruction. Focus on the judge's stated reasons, not merely the winner counts.",
                ]
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    job_id, stage, paper_id, review_id, system_prompt,
                    user_prompt, max_tokens, status
                ) VALUES (?, 'method_comparison_batch', ?, NULL, ?, ?, 6000, 'pending')
                """,
                (
                    f"compare-batch:{batch_index + 1}",
                    f"batch-{batch_index + 1}",
                    BATCH_SYSTEM,
                    "\n\n".join(sections),
                ),
            )
            inserted += cursor.rowcount
    return inserted


def prepare_global(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT paper_id, memo FROM memos
        WHERE stage='method_comparison_batch'
        ORDER BY paper_id
        """
    ).fetchall()
    if len(rows) != 5:
        raise RuntimeError(f"expected 5 batch memos, found {len(rows)}")
    sections = [
        "# Numerical result",
        "Across 100 deterministic-randomized blinded comparisons: layered paper synthesis preferred in 69; direct whole-forum memo preferred in 31; ties 0.",
        "# Batch-level qualitative syntheses",
    ]
    for index, row in enumerate(rows, 1):
        prefix = f"B{index:02d}"
        sections.extend(
            [
                f"## {prefix}: {row['paper_id']}",
                line_number(row["memo"], prefix),
            ]
        )
    sections.extend(
        [
            "# Task",
            "Produce the final methodological memo described in the system instruction.",
        ]
    )
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO jobs (
                job_id, stage, paper_id, review_id, system_prompt,
                user_prompt, max_tokens, status
            ) VALUES (
                'compare-global', 'method_comparison_global', 'global', NULL,
                ?, ?, 5000, 'pending'
            )
            """,
            (GLOBAL_SYSTEM, "\n\n".join(sections)),
        )
    return cursor.rowcount


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--stage", choices=["batches", "global"], required=True)
    args = parser.parse_args()
    connection = sqlite3.connect(args.pilot)
    connection.row_factory = sqlite3.Row
    try:
        inserted = (
            prepare_batches(connection)
            if args.stage == "batches"
            else prepare_global(connection)
        )
        print(f"{args.stage}: inserted {inserted}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
