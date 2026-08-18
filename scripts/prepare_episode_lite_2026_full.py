"""Prepare outcome-blind Episode Lite extraction shards for every ICLR 2026 review."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


DEFAULT_DATABASE = Path("data/analysis/iclr/production-2026.sqlite3")
DEFAULT_OUTPUT = Path("data/analysis/iclr/episode-lite-2026-full")
DEFAULT_MAX_REVIEWS = 8
DEFAULT_MAX_MEMO_CHARS = 96_000


@dataclass(frozen=True)
class ReviewMemo:
    paper_id: str
    review_id: str
    memo: str


def readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)


def pack_reviews(
    reviews: Iterable[ReviewMemo], *, max_reviews: int, max_memo_chars: int
) -> Iterator[list[ReviewMemo]]:
    """Pack deterministic review streams without splitting a review."""
    if max_reviews < 1 or max_memo_chars < 1:
        raise ValueError("packing limits must be positive")
    batch: list[ReviewMemo] = []
    chars = 0
    for review in reviews:
        if batch and (len(batch) >= max_reviews or chars + len(review.memo) > max_memo_chars):
            yield batch
            batch, chars = [], 0
        batch.append(review)
        chars += len(review.memo)
    if batch:
        yield batch


def number_memo_lines(review_id: str, memo: str) -> str:
    return "\n".join(
        f"[I-{review_id}:L{index:03d}] {line}"
        for index, line in enumerate(memo.splitlines(), 1)
    )


def source_header(shard: int, review_count: int) -> str:
    return f"""# ICLR 2026 full-corpus Episode Lite extraction shard {shard:05d}

This packet contains {review_count} machine-produced `initial_blind` memos, one
per human official review. Neither decision nor score metadata is present.
Extract HUMAN REVIEWER EVALUATION LOGIC, not paper quality and not memo style.

For every review, recover every substantively distinct evaluation episode: the
smallest useful chain connecting what the reviewer inspected, what they
observed or found missing, why it mattered, the resulting judgment, and any
requested test or change. Do not split one coherent argument merely because it
spans sentences; do split independently warranted arguments. Preserve
incomplete chains with empty arrays and `quality.missing_links`; never invent
missing logic or requested changes.

Use `schemas/evaluation-episode-v0.2.json`, `enrichment_level=lite`, and IDs
`E-<paper_id>-<review_id>-NN`. Memo-local reviewer citations such as `L019`
resolve to `R-<review_id>:L019` with `provenance_level=primary`. Wrapper lines
in this packet are `I-<review_id>:L###` and may be cited as
`analytic_wrapper` only when primary support is unavailable. Never present memo
inference as reviewer-explicit.

The concrete signature must retain paper-specific objects. Each abstract
signature must encode the object class, observed relation or absence, operative
standard/counterfactual, and judgment endpoint while removing topic and method
names. Never reuse a stock abstract sentence across reviews and never append an
ID or ordinal merely to make it unique. Do not assign pattern labels. The first `quality.notes` entry must begin
`Boundary rationale:`. Copy IDs exactly. Concrete fields must name the actual
component, comparison, quantity, claim, or failure mode; placeholders and
generic "clarify or test" requests are invalid. A substantive review commonly
contains 2–6 episodes, but yield is determined by its content, not a quota. A
one-episode result is valid only when the memo truly supports one coherent
chain; independently warranted strengths, comparisons, scope concerns,
mechanism questions, robustness concerns, and presentation blockages remain
separate episodes even if they affect the same recommendation.
"""


def protocol() -> str:
    return """# Full-corpus Episode Lite extraction protocol

For one `source-shard-NNNNN.md`, write exactly two sibling outputs:

1. `episodes-shard-NNNNN.jsonl`: one compact schema-valid Episode Lite object
   per line, with no Markdown fences or prose.
2. `coverage-shard-NNNNN.json`: `{\"shard\": N, \"reviews\": [...]}` with one
   row per source review. Every row has `review_id`, `episode_count`, `status`,
   `review_is_substantive`, `zero_episode_reason`, `provenance_failure`, and
   `notes`. Use `complete`, `zero`, or `error` exactly as appropriate.

Process every review. Preserve incomplete logic instead of completing it from
general knowledge. Prefer primary evidence whenever a memo gives a local
reviewer line. Parse every JSONL line and run the shard validator before
finishing. Write no exploratory taxonomy or pattern report: ontology membership
is a later, separate pass.

Before writing, enumerate the distinct candidate chains in each memo and test
whether each has its own observation-to-judgment bridge. Do not collapse the
whole review into one summary episode. A claim supported only by an `I-` wrapper
is `memo_inferred` or `mixed`, never `reviewer_explicit`. Abstract signatures
must be review-specific logical forms rather than a shared template. Copy the
exact visible source-line body into every wrapper evidence record's `text`.
Semantic episode fields must be authored review by review from the memo: do not
use a script, loop, keyword selector, or template filler to generate them.
"""


def render_source(shard: int, reviews: list[ReviewMemo]) -> str:
    parts = [source_header(shard, len(reviews))]
    for index, review in enumerate(reviews, 1):
        parts.extend(
            [
                f"## Review {index:02d}: {review.review_id}",
                f"paper_id={review.paper_id}; review_id={review.review_id}",
                number_memo_lines(review.review_id, review.memo),
            ]
        )
    return "\n\n".join(parts).rstrip() + "\n"


def iter_reviews(connection: sqlite3.Connection) -> Iterator[ReviewMemo]:
    rows = connection.execute(
        """
        SELECT j.paper_id, j.review_id, m.memo
        FROM jobs AS j JOIN memos AS m USING(job_id)
        WHERE j.stage='initial_blind' AND j.status='complete'
          AND j.review_id IS NOT NULL
        ORDER BY j.review_id
        """
    )
    seen: set[str] = set()
    for paper_id, review_id, memo in rows:
        if review_id in seen:
            raise ValueError(f"duplicate initial_blind review: {review_id}")
        seen.add(review_id)
        yield ReviewMemo(paper_id, review_id, memo)


def prepare(
    database: Path,
    output: Path,
    *,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
    max_memo_chars: int = DEFAULT_MAX_MEMO_CHARS,
) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to replace existing full-corpus directory: {output}")
    staging = output.with_name(output.name + ".preparing")
    if staging.exists():
        raise FileExistsError(f"staging directory already exists: {staging}")
    staging.mkdir(parents=True)

    connection = readonly_connection(database)
    try:
        expected = connection.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE stage='initial_blind' AND status='complete' AND review_id IS NOT NULL
            """
        ).fetchone()[0]
        shards = []
        review_count = 0
        for shard, reviews in enumerate(
            pack_reviews(
                iter_reviews(connection),
                max_reviews=max_reviews,
                max_memo_chars=max_memo_chars,
            ),
            1,
        ):
            source = render_source(shard, reviews)
            suffix = f"{shard:05d}"
            (staging / f"source-shard-{suffix}.md").write_text(source, encoding="utf-8")
            metadata = {
                "shard": shard,
                "reviews": [
                    {"paper_id": row.paper_id, "review_id": row.review_id}
                    for row in reviews
                ],
            }
            (staging / f"source-shard-{suffix}.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            review_count += len(reviews)
            shards.append(
                {
                    "shard": shard,
                    "review_count": len(reviews),
                    "memo_chars": sum(len(row.memo) for row in reviews),
                    "source_chars": len(source),
                }
            )
    finally:
        connection.close()
    if review_count != expected:
        raise RuntimeError(f"review coverage mismatch: prepared={review_count} expected={expected}")

    manifest = {
        "version": 1,
        "scope": "ICLR 2026 official reviews with completed initial_blind memos",
        "database": str(database.resolve()),
        "schema": str(Path("schemas/evaluation-episode-v0.2.json").resolve()),
        "outcome_blind": True,
        "population_census": True,
        "review_count": review_count,
        "shard_count": len(shards),
        "max_reviews_per_shard": max_reviews,
        "max_memo_chars_per_shard": max_memo_chars,
        "required_outputs": ["episodes", "coverage"],
        "shards": shards,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (staging / "AGENT_PROTOCOL.md").write_text(protocol(), encoding="utf-8")
    staging.replace(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-reviews", type=int, default=DEFAULT_MAX_REVIEWS)
    parser.add_argument("--max-memo-chars", type=int, default=DEFAULT_MAX_MEMO_CHARS)
    args = parser.parse_args()
    manifest = prepare(
        args.database,
        args.output,
        max_reviews=args.max_reviews,
        max_memo_chars=args.max_memo_chars,
    )
    print(json.dumps({
        "reviews": manifest["review_count"],
        "shards": manifest["shard_count"],
        "outcome_blind": manifest["outcome_blind"],
    }))


if __name__ == "__main__":
    main()
