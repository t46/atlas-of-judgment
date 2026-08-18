"""Select a diverse ICLR 2026 pilot and prepare open-ended memo requests."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_SOURCE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_OUTPUT = Path("data/analysis/iclr/pilot.sqlite3")
SEED = 20260811

INITIAL_SYSTEM_PROMPT = """You are conducting qualitative metascience research on human peer review.

Your object of analysis is the REVIEWER'S ACTIVITY, not the underlying paper. Write a rich, open-ended analytic memo reconstructing what this reviewer is doing in the review. Do not classify the review into a predetermined taxonomy, do not score the review, and do not independently re-review the paper.

Attend closely to what in the paper the reviewer inspects; what they observe or believe is missing; how they move from those observations to positive, negative, or uncertain evaluations; what comparisons, expectations, assumptions, norms, counterfactuals, or alternative explanations make that move intelligible; and what any requested change or experiment is intended to find out. Preserve tensions and unusual reasoning rather than forcing completeness or uniform headings.

Separate what the reviewer states explicitly from your interpretation. Ground substantive claims with the supplied line IDs. Do not infer motives, standards, or paper facts that the text cannot support. The review decision, other reviewers, author response, and final outcome are intentionally withheld. Write in English Markdown prose."""

FORUM_SYSTEM_PROMPT = """You are conducting qualitative metascience research on human peer review.

Analyze the complete public review forum as an unfolding social and epistemic process. Your primary objects are the human reviewers: what each one inspects, what they count as good or bad, and the reasoning that connects observation to evaluation. Preserve each reviewer's distinct logic and minority views. Also examine author responses, reviewer changes, disagreement, and how the meta-review and decision combine or disregard those judgments.

Write a rich, open-ended analytic memo. Do not impose a predetermined taxonomy and do not independently re-review the paper. Separate explicit statements from interpretation and ground substantive claims with the supplied line IDs. Be alert to hindsight: do not rewrite an initial reviewer's reasoning merely to make it fit the final outcome. Write in English Markdown prose."""


def line_number(text: str, prefix: str) -> str:
    lines = text.splitlines() or [""]
    return "\n".join(f"[{prefix}:L{index:03d}] {line}" for index, line in enumerate(lines, 1))


def parse_rating(content_json: str) -> float | None:
    content = json.loads(content_json)
    for key in ("rating", "recommendation"):
        value = content.get(key)
        if value is None:
            continue
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if match:
            return float(match.group())
    return None


def decision_bucket(decision: str | None) -> str:
    return decision if decision in {"Accept (Oral)", "Accept (Poster)", "Reject"} else "No decision"


def choose_diverse(
    candidates: list[dict[str, Any]],
    count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    variances = [item["rating_variance"] for item in candidates]
    comments = [item["comment_count"] for item in candidates]
    variance_median = statistics.median(variances) if variances else 0
    comment_median = statistics.median(comments) if comments else 0
    groups: dict[tuple[bool, bool], list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        groups[
            (
                item["rating_variance"] >= variance_median,
                item["comment_count"] >= comment_median,
            )
        ].append(item)
    for values in groups.values():
        rng.shuffle(values)

    selected = []
    keys = [(False, False), (False, True), (True, False), (True, True)]
    while len(selected) < count:
        made_progress = False
        for key in keys:
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop())
                made_progress = True
        if not made_progress:
            break
    return selected


def create_output(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sample_papers (
            paper_id TEXT PRIMARY KEY,
            decision_bucket TEXT NOT NULL,
            review_count INTEGER NOT NULL,
            comment_count INTEGER NOT NULL,
            rating_mean REAL,
            rating_variance REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            review_id TEXT,
            system_prompt TEXT NOT NULL,
            user_prompt TEXT NOT NULL,
            max_tokens INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            UNIQUE(stage, paper_id, review_id)
        );
        CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, stage);
        """
    )
    connection.commit()
    return connection


def load_candidates(source: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = source.execute(
        """
        SELECT p.forum_id, p.decision, p.review_count, p.comment_count, m.content_json
        FROM papers AS p
        JOIN messages AS m
          ON m.year = p.year AND m.forum_id = p.forum_id
        WHERE p.year = 2026
          AND p.review_count >= 2
          AND p.title IS NOT NULL
          AND p.abstract IS NOT NULL
          AND m.kind = 'official_review'
        ORDER BY p.forum_id
        """
    )
    grouped: dict[str, dict[str, Any]] = {}
    for forum_id, decision, review_count, comment_count, content_json in rows:
        item = grouped.setdefault(
            forum_id,
            {
                "paper_id": forum_id,
                "decision_bucket": decision_bucket(decision),
                "review_count": review_count,
                "comment_count": comment_count,
                "ratings": [],
            },
        )
        rating = parse_rating(content_json)
        if rating is not None:
            item["ratings"].append(rating)

    candidates = []
    for item in grouped.values():
        ratings = item.pop("ratings")
        candidates.append(
            {
                **item,
                "rating_mean": statistics.mean(ratings) if ratings else None,
                "rating_variance": statistics.pvariance(ratings) if len(ratings) >= 2 else 0.0,
            }
        )
    return candidates


def initial_user_prompt(
    title: str,
    abstract: str,
    review_text: str,
    review_id: str,
) -> str:
    return f"""# Paper context

Title: {title}

Abstract (context only; do not independently evaluate it):
{line_number(abstract, 'A')}

# Isolated public review-form

The following is the public version of one reviewer's review-form response. Administrative fields and numerical ratings may be embedded alongside prose. OpenReview does not expose a complete public content-edit history, so do not assume this is an untouched pre-rebuttal snapshot.

{line_number(review_text, f'R-{review_id}')}

# Task

Produce the open-ended analytic memo described in the system instruction. Focus on the reviewer's objects of attention and their evaluative logic. The absence of author response and final outcome is deliberate; do not make temporal claims about when the public review-form wording was finalized."""


def forum_user_prompt(
    title: str,
    abstract: str,
    messages: list[sqlite3.Row],
) -> str:
    sections = [
        "# Paper context",
        f"Title: {title}",
        "Abstract (context only; do not independently evaluate it):",
        line_number(abstract, "A"),
        "# Complete public forum",
    ]
    for message in messages:
        prefix = f"M-{message['note_id']}"
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
            "Produce the open-ended forum-level analytic memo described in the system instruction. Preserve reviewer-specific reasoning and chronology.",
        ]
    )
    return "\n\n".join(sections)


def prepare(source_path: Path, output_path: Path, sample_size: int) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    output = create_output(output_path)
    rng = random.Random(SEED)
    try:
        completed_or_running = output.execute(
            "SELECT COUNT(*) FROM jobs WHERE status != 'pending'"
        ).fetchone()[0]
        has_memos_table = output.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memos'"
        ).fetchone()
        memo_count = (
            output.execute("SELECT COUNT(*) FROM memos").fetchone()[0]
            if has_memos_table
            else 0
        )
        if completed_or_running or memo_count:
            raise RuntimeError(
                "refusing to replace a pilot database that contains API work; "
                "choose a new --output path"
            )
        candidates = load_candidates(source)
        by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in candidates:
            by_bucket[item["decision_bucket"]].append(item)

        base_targets = {
            "Accept (Oral)": 10,
            "Accept (Poster)": 35,
            "Reject": 35,
            "No decision": 20,
        }
        scale = sample_size / 100
        targets = {
            key: max(1, math.floor(value * scale)) for key, value in base_targets.items()
        }
        while sum(targets.values()) < sample_size:
            targets["Reject"] += 1
        while sum(targets.values()) > sample_size:
            targets["Accept (Poster)"] -= 1

        selected = []
        for bucket, count in targets.items():
            selected.extend(choose_diverse(by_bucket[bucket], count, rng))
        if len(selected) != sample_size:
            raise RuntimeError(
                f"requested {sample_size} papers but decision-bucket targets "
                f"produced {len(selected)}"
            )
        rng.shuffle(selected)

        with output:
            output.execute("DELETE FROM jobs")
            output.execute("DELETE FROM sample_papers")
            output.executemany(
                """
                INSERT INTO sample_papers (
                    paper_id, decision_bucket, review_count, comment_count,
                    rating_mean, rating_variance
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["paper_id"],
                        item["decision_bucket"],
                        item["review_count"],
                        item["comment_count"],
                        item["rating_mean"],
                        item["rating_variance"],
                    )
                    for item in selected
                ],
            )

            jobs = []
            for item in selected:
                paper = source.execute(
                    "SELECT title, abstract FROM papers WHERE year = 2026 AND forum_id = ?",
                    (item["paper_id"],),
                ).fetchone()
                reviews = source.execute(
                    """
                    SELECT note_id, content_text FROM messages
                    WHERE year = 2026 AND forum_id = ? AND kind = 'official_review'
                    ORDER BY COALESCE(cdate, 0), note_id
                    """,
                    (item["paper_id"],),
                ).fetchall()
                for review in reviews:
                    jobs.append(
                        (
                            f"initial:{review['note_id']}",
                            "initial_blind",
                            item["paper_id"],
                            review["note_id"],
                            INITIAL_SYSTEM_PROMPT,
                            initial_user_prompt(
                                paper["title"],
                                paper["abstract"],
                                review["content_text"],
                                review["note_id"],
                            ),
                            4_000,
                        )
                    )

                messages = source.execute(
                    """
                    SELECT note_id, replyto, kind, role, signature, content_text
                    FROM messages
                    WHERE year = 2026 AND forum_id = ?
                    ORDER BY cdate, note_id
                    """,
                    (item["paper_id"],),
                ).fetchall()
                jobs.append(
                    (
                        f"forum:{item['paper_id']}",
                        "forum_direct",
                        item["paper_id"],
                        None,
                        FORUM_SYSTEM_PROMPT,
                        forum_user_prompt(paper["title"], paper["abstract"], messages),
                        8_000,
                    )
                )

            output.executemany(
                """
                INSERT INTO jobs (
                    job_id, stage, paper_id, review_id, system_prompt,
                    user_prompt, max_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                jobs,
            )

        counts = output.execute(
            "SELECT stage, COUNT(*) FROM jobs GROUP BY stage ORDER BY stage"
        ).fetchall()
        print(f"selected {len(selected)} papers")
        for stage, count in counts:
            print(f"{stage}: {count} jobs")
    finally:
        output.close()
        source.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample-size", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sample_size < 4:
        raise SystemExit("--sample-size must be at least 4 to cover every decision bucket")
    prepare(args.source, args.output, args.sample_size)


if __name__ == "__main__":
    main()
