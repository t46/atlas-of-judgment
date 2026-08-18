"""Prepare deterministic ICLR 2026 paper-synthesis shards for episode discovery.

The pilot deliberately samples across outcome, rating disagreement, and public
discussion volume.  It does not define a reviewer-behavior taxonomy; the
generated shards ask analysts to discover evaluation episodes in free text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_DATABASE = Path("data/analysis/iclr/production-2026.sqlite3")
DEFAULT_SOURCE_DATABASE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_OUTPUT_DIR = Path("data/analysis/iclr/evaluation-episode-pilot")
DEFAULT_SEED = 20260816
BUCKET_ORDER = ("Accept (Oral)", "Accept (Poster)", "Reject", "No decision")
SHARD_COUNT = 4
PAPERS_PER_BUCKET = 6


@dataclass(frozen=True)
class Candidate:
    paper_id: str
    decision_bucket: str
    review_count: int
    comment_count: int
    rating_mean: float | None
    rating_variance: float
    memo_chars: int


@dataclass(frozen=True)
class Selection:
    candidate: Candidate
    rationale: str


def stable_key(seed: int, paper_id: str) -> str:
    return hashlib.sha256(f"{seed}:{paper_id}".encode()).hexdigest()


def _choose_stable(
    candidates: Iterable[Candidate], *, seed: int, excluded: set[str]
) -> Candidate | None:
    available = [item for item in candidates if item.paper_id not in excluded]
    if not available:
        return None
    return min(available, key=lambda item: stable_key(seed, item.paper_id))


def select_bucket(candidates: list[Candidate], *, seed: int) -> list[Selection]:
    """Select six papers: four median quadrants plus two edge cases."""
    if len(candidates) < PAPERS_PER_BUCKET:
        raise ValueError(
            f"need at least {PAPERS_PER_BUCKET} candidates, found {len(candidates)}"
        )
    variance_median = statistics.median(item.rating_variance for item in candidates)
    comment_median = statistics.median(item.comment_count for item in candidates)
    selected: list[Selection] = []
    excluded: set[str] = set()

    quadrants = (
        (False, False, "below-median disagreement / below-median discussion"),
        (
            False,
            True,
            "below-median disagreement / at-or-above-median discussion",
        ),
        (
            True,
            False,
            "at-or-above-median disagreement / below-median discussion",
        ),
        (
            True,
            True,
            "at-or-above-median disagreement / at-or-above-median discussion",
        ),
    )
    for high_variance, high_comments, label in quadrants:
        pool = [
            item
            for item in candidates
            if (item.rating_variance >= variance_median) == high_variance
            and (item.comment_count >= comment_median) == high_comments
        ]
        choice = _choose_stable(pool, seed=seed, excluded=excluded)
        if choice is None:
            # A boundary-heavy bucket can have an empty strict quadrant. Keep
            # the sample complete while making the fallback explicit.
            choice = _choose_stable(candidates, seed=seed, excluded=excluded)
            label += " (fallback: quadrant empty)"
        assert choice is not None
        selected.append(Selection(choice, label))
        excluded.add(choice.paper_id)

    remaining = [item for item in candidates if item.paper_id not in excluded]
    highest_disagreement = max(
        remaining,
        key=lambda item: (
            item.rating_variance,
            item.comment_count,
            stable_key(seed, item.paper_id),
        ),
    )
    selected.append(Selection(highest_disagreement, "maximum remaining disagreement"))
    excluded.add(highest_disagreement.paper_id)

    remaining = [item for item in candidates if item.paper_id not in excluded]
    highest_discussion = max(
        remaining,
        key=lambda item: (
            item.comment_count,
            item.rating_variance,
            stable_key(seed, item.paper_id),
        ),
    )
    selected.append(Selection(highest_discussion, "maximum remaining discussion"))
    return selected


def assign_shards(
    selections_by_bucket: dict[str, list[Selection]],
) -> list[list[Selection]]:
    shards: list[list[Selection]] = [[] for _ in range(SHARD_COUNT)]
    for bucket_index, bucket in enumerate(BUCKET_ORDER):
        selections = selections_by_bucket[bucket]
        for selection_index, selection in enumerate(selections):
            shard_index = (selection_index + bucket_index) % SHARD_COUNT
            shards[shard_index].append(selection)
    return shards


def load_candidates(connection: sqlite3.Connection) -> list[Candidate]:
    rows = connection.execute(
        """
        SELECT s.paper_id, s.decision_bucket, s.review_count, s.comment_count,
               s.rating_mean, s.rating_variance, LENGTH(m.memo)
        FROM sample_papers AS s
        JOIN memos AS m
          ON m.paper_id=s.paper_id AND m.stage='paper_synthesis'
        ORDER BY s.paper_id
        """
    )
    return [Candidate(*row) for row in rows]


def shard_header(shard_number: int) -> str:
    return f"""# Evaluation-episode discovery shard {shard_number}

These are machine-produced layered syntheses of public ICLR 2026 review
forums. Analyze HUMAN REVIEWER ACTIVITY, not paper quality and not memo style.

For each paper, recover concrete evaluation episodes: what a reviewer inspected,
what they observed, which explicit or inferred standard/comparison/assumption
made that observation evaluatively relevant, how the reasoning led to a
positive/negative/uncertain judgment, and what evidence or change could alter
the judgment. An episode may span multiple sentences.

Do not impose a fixed topic taxonomy. Preserve unusual logic, incomplete chains,
conflicting interpretations, and cases that do not fit the proposed episode
shape. Separate explicit reviewer statements from analyst interpretation. Cite
the primary M-<note_id>:L### references already embedded in the memo whenever
possible. Finish with: (1) candidate episode fields, (2) recurring but still
free-form patterns, (3) counterexamples or missing fields, and (4) 8 especially
informative episode examples.
"""


def render_shard(
    analysis: sqlite3.Connection,
    source: sqlite3.Connection,
    shard_number: int,
    selections: list[Selection],
) -> str:
    sections = [shard_header(shard_number)]
    for index, selection in enumerate(selections, 1):
        item = selection.candidate
        row = analysis.execute(
            """
            SELECT m.memo
            FROM memos AS m
            WHERE m.stage='paper_synthesis' AND m.paper_id=?
            """,
            (item.paper_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"paper synthesis missing: {item.paper_id}")
        memo = row[0]
        title_row = source.execute(
            "SELECT title FROM papers WHERE year=2026 AND forum_id=?",
            (item.paper_id,),
        ).fetchone()
        title = title_row[0] if title_row else None
        sections.extend(
            [
                f"## Paper {index}: {item.paper_id}",
                f"Title: {title or '(title unavailable)'}",
                (
                    f"Decision bucket: {item.decision_bucket}; reviews: "
                    f"{item.review_count}; comments: {item.comment_count}; "
                    f"rating variance: {item.rating_variance:.3f}; "
                    f"sampling rationale: {selection.rationale}"
                ),
                memo,
            ]
        )
    return "\n\n".join(sections).rstrip() + "\n"


def prepare(
    database: Path, source_database: Path, output_dir: Path, *, seed: int
) -> None:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        source = sqlite3.connect(
            f"file:{source_database.resolve()}?mode=ro", uri=True
        )
        try:
            candidates = load_candidates(connection)
            by_bucket = {
                bucket: [item for item in candidates if item.decision_bucket == bucket]
                for bucket in BUCKET_ORDER
            }
            selections_by_bucket = {
                bucket: select_bucket(items, seed=seed + bucket_index)
                for bucket_index, (bucket, items) in enumerate(by_bucket.items())
            }
            shards = assign_shards(selections_by_bucket)
            if any(len(shard) != 6 for shard in shards):
                raise RuntimeError(
                    f"unbalanced shard sizes: {[len(shard) for shard in shards]}"
                )

            # Render everything before touching prior pilot artifacts. A query
            # or schema failure therefore leaves the previous complete set intact.
            rendered = [
                render_shard(connection, source, shard_index, selections)
                for shard_index, selections in enumerate(shards, 1)
            ]
            manifest = {
                "database": str(database.resolve()),
                "source_database": str(source_database.resolve()),
                "seed": seed,
                "shard_count": SHARD_COUNT,
                "papers_per_bucket": PAPERS_PER_BUCKET,
                "papers": [],
            }
            for shard_index, selections in enumerate(shards, 1):
                for selection in selections:
                    manifest["papers"].append(
                        {
                            **asdict(selection.candidate),
                            "rationale": selection.rationale,
                            "shard": shard_index,
                        }
                    )

            output_dir.mkdir(parents=True, exist_ok=True)
            for shard_index, text in enumerate(rendered, 1):
                (output_dir / f"shard-{shard_index:02d}.md").write_text(
                    text, encoding="utf-8"
                )
            (output_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        finally:
            source.close()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--source-database", type=Path, default=DEFAULT_SOURCE_DATABASE
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    prepare(args.database, args.source_database, args.output_dir, seed=args.seed)


if __name__ == "__main__":
    main()
