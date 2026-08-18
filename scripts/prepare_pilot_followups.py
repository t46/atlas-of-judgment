"""Prepare trajectory and paper-synthesis jobs for the ICLR 2026 pilot.

The first-pass review memos must already exist.  This script never rewrites or
deletes completed work: it appends deterministic follow-up jobs to the pilot
database, making it safe to run once per stage as the pilot advances.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

from prepare_pilot import line_number


DEFAULT_SOURCE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_PILOT = Path("data/analysis/iclr/pilot.sqlite3")

TRAJECTORY_SYSTEM_PROMPT = """You are conducting qualitative metascience research on human peer review.

Analyze the public exchange that follows one isolated review-form and reconstruct what happens to that reviewer's evaluative reasoning. The object is the REVIEWER'S ACTIVITY, not the underlying paper. Write a rich, open-ended analytic memo; do not impose a predetermined taxonomy, score the review, or independently re-review the paper.

Examine what the authors understood the challenge to be; what evidence, clarification, comparison, or argument they supplied; whether and why the reviewer accepted, rejected, reframed, or did not address it; what standards and alternative explanations remain active; and what changed in evaluation, confidence, requests, or score. A request is especially informative when its intended discriminating role can be reconstructed. Preserve silence, ambiguity, and unresolved tension rather than manufacturing resolution.

The supplied review-form is the public version available after the process. Public OpenReview data does not expose a complete content-edit history, so do not assume that its wording or rating is an untouched pre-rebuttal snapshot. The frozen first-pass memo analyzed that form without seeing the exchange or outcome; use it as an analytic aid, not as primary evidence. Separate explicit statements from interpretation and cite the supplied evidence line IDs. Write in English Markdown prose."""

SYNTHESIS_SYSTEM_PROMPT = """You are conducting qualitative metascience research on human peer review.

Analyze one paper's review process by synthesizing reviewer-specific memos, public exchanges, and formal outcome messages. The object is the collective activity of HUMAN REVIEWERS: what each reviewer inspected, what they counted as good or bad, the logic connecting observation to evaluation, and how those judgments did or did not shape the result. Do not independently re-review the paper and do not impose a predetermined taxonomy.

Preserve reviewer identities as distinct analytic positions, including minority views, incompatible standards, uncertainty, and issues left unresolved. Reconstruct what evidence moved a reviewer, what failed to move them and why, how improvement requests functioned as tests, and how the meta-review or decision combined, prioritized, transformed, or disregarded reviewer reasoning. Do not treat the final decision as ground truth and do not rewrite earlier reasoning to fit it.

The inputs include analyst memos as well as primary forum messages. Distinguish claims directly supported by forum text from higher-order synthesis. Prefer the globally stable primary note-and-line IDs embedded inside the analyst memos over the I-/T- wrapper IDs added for this prompt; the final memo must remain traceable to OpenReview evidence. Write a rich, open-ended English Markdown memo."""


def messages_for_paper(source: sqlite3.Connection, paper_id: str) -> list[sqlite3.Row]:
    return source.execute(
        """
        SELECT note_id, replyto, kind, role, signature, cdate, content_text
        FROM messages
        WHERE year = 2026 AND forum_id = ?
        ORDER BY COALESCE(cdate, 0), note_id
        """,
        (paper_id,),
    ).fetchall()


def descendant_messages(
    review_id: str,
    messages: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    children: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for message in messages:
        if message["replyto"]:
            children[message["replyto"]].append(message)

    descendants: list[sqlite3.Row] = []
    seen = {review_id}
    queue = deque(children.get(review_id, []))
    while queue:
        message = queue.popleft()
        if message["note_id"] in seen:
            continue
        seen.add(message["note_id"])
        descendants.append(message)
        queue.extend(children.get(message["note_id"], []))
    descendants.sort(key=lambda row: (row["cdate"] or 0, row["note_id"]))
    return descendants


def trajectory_user_prompt(
    title: str,
    abstract: str,
    review: sqlite3.Row,
    first_pass_memo: str,
    descendants: list[sqlite3.Row],
) -> str:
    sections = [
        "# Paper context",
        f"Title: {title}",
        "Abstract (context only; do not independently evaluate it):",
        line_number(abstract, "A"),
        "# Isolated public review-form",
        f"note_id={review['note_id']}",
        line_number(review["content_text"], f"R-{review['note_id']}"),
        "# Frozen first-pass analytic memo",
        "This memo was produced without the exchange or outcome:",
        line_number(first_pass_memo, "P"),
        "# Descendant public exchange",
    ]
    for index, message in enumerate(descendants, 1):
        prefix = f"D-{message['note_id']}"
        sections.extend(
            [
                f"## {prefix}: {message['kind']} by {message['role']} ({message['signature'] or 'unknown'})",
                f"note_id={message['note_id']} replyto={message['replyto']}",
                line_number(message["content_text"], prefix),
            ]
        )
    sections.extend(
        [
            "# Task",
            "Produce the open-ended trajectory memo described in the system instruction. Treat the exchange as evidence about the reviewer's evaluative logic, not merely as a chronological summary.",
        ]
    )
    return "\n\n".join(sections)


def synthesis_user_prompt(
    title: str,
    abstract: str,
    review_memos: list[sqlite3.Row],
    trajectory_memos: dict[str, str],
    unassigned_messages: Iterable[sqlite3.Row],
) -> str:
    sections = [
        "# Paper context",
        f"Title: {title}",
        "Abstract (context only; do not independently evaluate it):",
        line_number(abstract, "A"),
        "# Reviewer-specific analyses",
    ]
    for index, memo in enumerate(review_memos, 1):
        initial_prefix = f"I-{memo['review_id']}"
        sections.extend(
            [
                f"## Reviewer position {index}: review note {memo['review_id']}",
                "### Isolated-review memo",
                line_number(memo["memo"], initial_prefix),
            ]
        )
        trajectory = trajectory_memos.get(memo["review_id"])
        if trajectory:
            trajectory_prefix = f"T-{memo['review_id']}"
            sections.extend(
                [
                    "### Exchange/trajectory memo",
                    line_number(trajectory, trajectory_prefix),
                ]
            )
        else:
            sections.append("### Exchange/trajectory memo\nNo descendant public exchange was present.")

    sections.append("# Paper-level and formal-outcome messages not assigned to a review branch")
    for message in unassigned_messages:
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
            "# Task",
            "Produce the open-ended reviewer-process synthesis described in the system instruction. Explain the relationship between distinct evaluative logics and the outcome without forcing consensus.",
        ]
    )
    return "\n\n".join(sections)


def insert_job(
    pilot: sqlite3.Connection,
    *,
    job_id: str,
    stage: str,
    paper_id: str,
    review_id: str | None,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> bool:
    cursor = pilot.execute(
        """
        INSERT OR IGNORE INTO jobs (
            job_id, stage, paper_id, review_id, system_prompt,
            user_prompt, max_tokens, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (job_id, stage, paper_id, review_id, system_prompt, user_prompt, max_tokens),
    )
    return cursor.rowcount == 1


def prepare_trajectories(source: sqlite3.Connection, pilot: sqlite3.Connection) -> int:
    incomplete = pilot.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'initial_blind' AND status != 'complete'"
    ).fetchone()[0]
    if incomplete:
        raise RuntimeError(f"{incomplete} initial-review jobs are not complete")

    inserted = 0
    papers = pilot.execute("SELECT paper_id FROM sample_papers ORDER BY paper_id").fetchall()
    with pilot:
        for paper_row in papers:
            paper_id = paper_row["paper_id"]
            paper = source.execute(
                "SELECT title, abstract FROM papers WHERE year = 2026 AND forum_id = ?",
                (paper_id,),
            ).fetchone()
            messages = messages_for_paper(source, paper_id)
            reviews = {row["note_id"]: row for row in messages if row["kind"] == "official_review"}
            memos = pilot.execute(
                """
                SELECT review_id, memo FROM memos
                WHERE stage = 'initial_blind' AND paper_id = ?
                ORDER BY review_id
                """,
                (paper_id,),
            ).fetchall()
            if {memo["review_id"] for memo in memos} != set(reviews):
                raise RuntimeError(
                    f"{paper_id}: cannot prepare trajectories until every "
                    "official review has exactly one isolated memo"
                )
            for memo in memos:
                descendants = descendant_messages(memo["review_id"], messages)
                if not descendants:
                    continue
                review = reviews[memo["review_id"]]
                inserted += insert_job(
                    pilot,
                    job_id=f"trajectory:{memo['review_id']}",
                    stage="trajectory",
                    paper_id=paper_id,
                    review_id=memo["review_id"],
                    system_prompt=TRAJECTORY_SYSTEM_PROMPT,
                    user_prompt=trajectory_user_prompt(
                        paper["title"],
                        paper["abstract"],
                        review,
                        memo["memo"],
                        descendants,
                    ),
                    max_tokens=6_000,
                )
    return inserted


def prepare_syntheses(source: sqlite3.Connection, pilot: sqlite3.Connection) -> int:
    incomplete = pilot.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'trajectory' AND status != 'complete'"
    ).fetchone()[0]
    if incomplete:
        raise RuntimeError(f"{incomplete} trajectory jobs are not complete")

    inserted = 0
    papers = pilot.execute("SELECT paper_id FROM sample_papers ORDER BY paper_id").fetchall()
    with pilot:
        for paper_row in papers:
            paper_id = paper_row["paper_id"]
            paper = source.execute(
                "SELECT title, abstract FROM papers WHERE year = 2026 AND forum_id = ?",
                (paper_id,),
            ).fetchone()
            messages = messages_for_paper(source, paper_id)
            review_ids = [row["note_id"] for row in messages if row["kind"] == "official_review"]
            assigned_ids: set[str] = set()
            for review_id in review_ids:
                assigned_ids.update(row["note_id"] for row in descendant_messages(review_id, messages))
            unassigned = [
                row
                for row in messages
                if row["kind"] != "official_review" and row["note_id"] not in assigned_ids
            ]

            review_memos = pilot.execute(
                """
                SELECT review_id, memo FROM memos
                WHERE stage = 'initial_blind' AND paper_id = ?
                ORDER BY review_id
                """,
                (paper_id,),
            ).fetchall()
            trajectory_memos = {
                row["review_id"]: row["memo"]
                for row in pilot.execute(
                    """
                    SELECT review_id, memo FROM memos
                    WHERE stage = 'trajectory' AND paper_id = ?
                    """,
                    (paper_id,),
                ).fetchall()
            }
            memo_review_ids = {row["review_id"] for row in review_memos}
            if memo_review_ids != set(review_ids):
                missing = sorted(set(review_ids) - memo_review_ids)
                extra = sorted(memo_review_ids - set(review_ids))
                raise RuntimeError(
                    f"{paper_id}: isolated memo/review mismatch; "
                    f"missing={missing}, extra={extra}"
                )
            expected_trajectories = {
                review_id
                for review_id in review_ids
                if descendant_messages(review_id, messages)
            }
            if set(trajectory_memos) != expected_trajectories:
                missing = sorted(expected_trajectories - set(trajectory_memos))
                extra = sorted(set(trajectory_memos) - expected_trajectories)
                raise RuntimeError(
                    f"{paper_id}: trajectory memo/thread mismatch; "
                    f"missing={missing}, extra={extra}"
                )
            inserted += insert_job(
                pilot,
                job_id=f"synthesis:{paper_id}",
                stage="paper_synthesis",
                paper_id=paper_id,
                review_id=None,
                system_prompt=SYNTHESIS_SYSTEM_PROMPT,
                user_prompt=synthesis_user_prompt(
                    paper["title"],
                    paper["abstract"],
                    review_memos,
                    trajectory_memos,
                    unassigned,
                ),
                max_tokens=8_000,
            )
    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--stage", choices=["trajectory", "paper_synthesis"], required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    pilot = sqlite3.connect(args.pilot)
    pilot.row_factory = sqlite3.Row
    try:
        if args.stage == "trajectory":
            inserted = prepare_trajectories(source, pilot)
        else:
            inserted = prepare_syntheses(source, pilot)
        total = pilot.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = ?", (args.stage,)
        ).fetchone()[0]
        print(f"{args.stage}: inserted {inserted}, total {total}")
    finally:
        pilot.close()
        source.close()


if __name__ == "__main__":
    main()
