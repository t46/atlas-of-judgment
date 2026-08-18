"""Prepare a deterministic 1,000-review ICLR 2026 Episode Lite pilot.

The sample balances final decision buckets while preserving two complementary
views of reviewer activity:

* 800 singleton reviews from papers spanning disagreement and discussion; and
* 200 reviews paired within 100 high-disagreement papers.

The generated Markdown shards contain machine-produced ``initial_blind`` memos,
not raw reviews.  Memo lines receive stable analytic-wrapper identifiers, while
the memo's local reviewer line references can be resolved to
``R-<review_id>:L###`` by the extraction agent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_DATABASE = Path("data/analysis/iclr/production-2026.sqlite3")
DEFAULT_SOURCE_DATABASE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_OUTPUT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_SEED = 20260816
BUCKET_ORDER = ("Accept (Oral)", "Accept (Poster)", "Reject", "No decision")
SINGLETON_PAPERS_PER_BUCKET = 200
PAIRED_PAPERS_PER_BUCKET = 25
SHARD_COUNT = 200
REVIEWS_PER_SHARD = 5


@dataclass(frozen=True)
class ReviewCandidate:
    paper_id: str
    review_id: str
    decision_bucket: str
    review_count: int
    comment_count: int
    rating_mean: float | None
    rating_variance: float
    rating: float | None
    confidence: float | None
    soundness: float | None
    presentation: float | None
    contribution: float | None
    review_chars: int


@dataclass(frozen=True)
class Selection:
    candidate: ReviewCandidate
    sample_kind: str
    rationale: str
    pair_id: str | None = None


def stable_key(seed: int, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().split(":", 1)[0])
        except ValueError:
            return None
    return None


def load_review_metadata(
    source_database: Path,
) -> dict[
    str,
    tuple[
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        int,
    ],
]:
    connection = readonly_connection(source_database)
    try:
        rows = connection.execute(
            """
            SELECT note_id, content_json, LENGTH(content_text)
            FROM messages
            WHERE year=2026 AND kind='official_review' AND role='reviewer'
            """
        )
        result: dict[
            str,
            tuple[
                float | None,
                float | None,
                float | None,
                float | None,
                float | None,
                int,
            ],
        ] = {}
        for review_id, content_json, review_chars in rows:
            content = json.loads(content_json)
            result[review_id] = (
                _numeric(content.get("rating")),
                _numeric(content.get("confidence")),
                _numeric(content.get("soundness")),
                _numeric(content.get("presentation")),
                _numeric(content.get("contribution")),
                review_chars,
            )
        return result
    finally:
        connection.close()


def load_candidates(database: Path, source_database: Path) -> list[ReviewCandidate]:
    review_metadata = load_review_metadata(source_database)
    connection = readonly_connection(database)
    try:
        rows = connection.execute(
            """
            SELECT j.paper_id, j.review_id, s.decision_bucket, s.review_count,
                   s.comment_count, s.rating_mean, s.rating_variance
            FROM jobs AS j
            JOIN sample_papers AS s ON s.paper_id=j.paper_id
            WHERE j.stage='initial_blind' AND j.status='complete'
              AND j.review_id IS NOT NULL
            ORDER BY j.paper_id, j.review_id
            """
        )
        candidates = []
        for row in rows:
            metadata = review_metadata.get(row[1], (None, None, None, None, None, 0))
            candidates.append(ReviewCandidate(*row, *metadata))
        return candidates
    finally:
        connection.close()


def quantile_bins(values: dict[str, float], bin_count: int = 5) -> dict[str, int]:
    """Assign stable rank-based bins, avoiding unstable numeric cut points."""
    ordered = sorted(values, key=lambda key: (values[key], key))
    size = len(ordered)
    if not size:
        return {}
    return {
        key: min(bin_count - 1, index * bin_count // size)
        for index, key in enumerate(ordered)
    }


def choose_pair_reviews(
    reviews: Sequence[ReviewCandidate], *, seed: int
) -> tuple[ReviewCandidate, ReviewCandidate]:
    rated = [item for item in reviews if item.rating is not None]
    if len(rated) >= 2:
        low = min(rated, key=lambda item: (item.rating, stable_key(seed, item.review_id)))
        remaining = [item for item in rated if item.review_id != low.review_id]
        high = max(
            remaining,
            key=lambda item: (item.rating, stable_key(seed + 1, item.review_id)),
        )
        return low, high
    ordered = sorted(reviews, key=lambda item: stable_key(seed, item.review_id))
    if len(ordered) < 2:
        raise ValueError("paired paper has fewer than two reviews")
    return ordered[0], ordered[1]


def select_pair_papers(
    papers: dict[str, list[ReviewCandidate]], *, seed: int
) -> list[str]:
    eligible = {}
    for paper_id, items in papers.items():
        rated_substantive = [
            item for item in items if item.rating is not None and item.review_chars >= 400
        ]
        ratings = [item.rating for item in rated_substantive]
        if len(rated_substantive) >= 2 and max(ratings) - min(ratings) >= 2:
            eligible[paper_id] = rated_substantive
    if len(eligible) < PAIRED_PAPERS_PER_BUCKET:
        raise ValueError(f"need {PAIRED_PAPERS_PER_BUCKET} pairable papers")

    comment_bins = quantile_bins(
        {paper_id: float(items[0].comment_count) for paper_id, items in eligible.items()}
    )
    chosen: list[str] = []
    for comment_bin in range(5):
        pool = [paper_id for paper_id in eligible if comment_bins[paper_id] == comment_bin]
        pool.sort(
            key=lambda paper_id: (
                -eligible[paper_id][0].rating_variance,
                stable_key(seed + comment_bin, paper_id),
            )
        )
        chosen.extend(pool[: PAIRED_PAPERS_PER_BUCKET // 5])

    if len(chosen) < PAIRED_PAPERS_PER_BUCKET:
        remainder = [paper_id for paper_id in eligible if paper_id not in chosen]
        remainder.sort(
            key=lambda paper_id: (
                -eligible[paper_id][0].rating_variance,
                stable_key(seed, paper_id),
            )
        )
        chosen.extend(remainder[: PAIRED_PAPERS_PER_BUCKET - len(chosen)])
    return chosen


def select_singleton_papers(
    papers: dict[str, list[ReviewCandidate]],
    *,
    excluded: set[str],
    seed: int,
) -> list[str]:
    eligible = {paper_id: items for paper_id, items in papers.items() if paper_id not in excluded}
    if len(eligible) < SINGLETON_PAPERS_PER_BUCKET:
        raise ValueError(f"need {SINGLETON_PAPERS_PER_BUCKET} singleton papers")

    variance_bins = quantile_bins(
        {paper_id: items[0].rating_variance for paper_id, items in eligible.items()}
    )
    comment_bins = quantile_bins(
        {paper_id: float(items[0].comment_count) for paper_id, items in eligible.items()}
    )
    cells: dict[tuple[int, int], list[str]] = defaultdict(list)
    for paper_id in eligible:
        cells[(variance_bins[paper_id], comment_bins[paper_id])].append(paper_id)

    chosen: list[str] = []
    target_per_cell = SINGLETON_PAPERS_PER_BUCKET // 25
    for variance_bin in range(5):
        for comment_bin in range(5):
            pool = cells[(variance_bin, comment_bin)]
            pool.sort(key=lambda paper_id: stable_key(seed, paper_id))
            chosen.extend(pool[:target_per_cell])

    if len(chosen) < SINGLETON_PAPERS_PER_BUCKET:
        remainder = [paper_id for paper_id in eligible if paper_id not in chosen]
        remainder.sort(key=lambda paper_id: stable_key(seed + 1, paper_id))
        chosen.extend(remainder[: SINGLETON_PAPERS_PER_BUCKET - len(chosen)])
    return chosen


def select_bucket(
    candidates: Sequence[ReviewCandidate], *, seed: int
) -> list[Selection]:
    papers: dict[str, list[ReviewCandidate]] = defaultdict(list)
    for item in candidates:
        papers[item.paper_id].append(item)

    pair_papers = select_pair_papers(papers, seed=seed)
    selections: list[Selection] = []
    for paper_id in pair_papers:
        low, high = choose_pair_reviews(papers[paper_id], seed=seed)
        pair_id = f"P-{paper_id}"
        rationale = "within-paper pair from a high-disagreement paper"
        selections.extend(
            [
                Selection(low, "paired", rationale, pair_id),
                Selection(high, "paired", rationale, pair_id),
            ]
        )

    singleton_papers = select_singleton_papers(
        papers, excluded=set(pair_papers), seed=seed + 10
    )
    for paper_id in singleton_papers:
        review = min(
            papers[paper_id], key=lambda item: stable_key(seed + 20, item.review_id)
        )
        selections.append(
            Selection(
                review,
                "singleton",
                "paper sampled across disagreement and discussion rank strata",
            )
        )

    expected = SINGLETON_PAPERS_PER_BUCKET + 2 * PAIRED_PAPERS_PER_BUCKET
    if len(selections) != expected:
        raise RuntimeError(f"unexpected bucket selection size: {len(selections)}")
    return selections


def _selection_units(selections: Sequence[Selection]) -> list[list[Selection]]:
    pairs: dict[str, list[Selection]] = defaultdict(list)
    singletons: list[list[Selection]] = []
    for item in selections:
        if item.pair_id:
            pairs[item.pair_id].append(item)
        else:
            singletons.append([item])
    if any(len(items) != 2 for items in pairs.values()):
        raise RuntimeError("paired selections must contain exactly two reviews")
    return list(pairs.values()) + singletons


def assign_shards(
    selections_by_bucket: dict[str, list[Selection]], *, seed: int
) -> list[list[Selection]]:
    """Place pairs together while giving every shard exactly 25 reviews."""
    shards: list[list[Selection]] = [[] for _ in range(SHARD_COUNT)]
    for bucket_index, bucket in enumerate(BUCKET_ORDER):
        base_quota, extra = divmod(len(selections_by_bucket[bucket]), SHARD_COUNT)
        quota = [base_quota] * SHARD_COUNT
        extra_start = (bucket_index * extra) % SHARD_COUNT
        for offset in range(extra):
            quota[(extra_start + offset) % SHARD_COUNT] += 1

        units = _selection_units(selections_by_bucket[bucket])
        pairs = [unit for unit in units if len(unit) == 2]
        singles = [unit for unit in units if len(unit) == 1]
        shard_order = sorted(
            [index for index in range(SHARD_COUNT) if quota[index] >= 2],
            key=lambda shard_index: stable_key(seed + bucket_index, str(shard_index)),
        )
        if len(shard_order) < len(pairs):
            raise RuntimeError(f"insufficient pair-preserving shard capacity for {bucket}")
        bucket_counts = [0] * SHARD_COUNT
        for unit, shard_index in zip(pairs, shard_order):
            shards[shard_index].extend(unit)
            bucket_counts[shard_index] += 2

        singles.sort(
            key=lambda unit: stable_key(
                seed + 100 + bucket_index, unit[0].candidate.review_id
            )
        )
        for unit in singles:
            available = [
                index
                for index in range(SHARD_COUNT)
                if bucket_counts[index] < quota[index]
            ]
            if not available:
                raise RuntimeError(f"no shard capacity left for {bucket}")
            shard_index = min(
                available,
                key=lambda index: (
                    bucket_counts[index],
                    len(shards[index]),
                    stable_key(seed + 200 + bucket_index, str(index)),
                ),
            )
            shards[shard_index].extend(unit)
            bucket_counts[shard_index] += 1
        if bucket_counts != quota:
            raise RuntimeError(f"bucket quota mismatch for {bucket}: {bucket_counts}")

    if any(len(shard) != REVIEWS_PER_SHARD for shard in shards):
        raise RuntimeError(f"unbalanced shards: {[len(shard) for shard in shards]}")
    return shards


def number_memo_lines(review_id: str, memo: str) -> str:
    return "\n".join(
        f"[I-{review_id}:L{index:03d}] {line}"
        for index, line in enumerate(memo.splitlines(), 1)
    )


def shard_header(shard_number: int) -> str:
    return f"""# ICLR 2026 Episode Lite extraction shard {shard_number:02d}

This file contains 5 machine-produced `initial_blind` analytic memos, one per
human official review. Extract HUMAN REVIEWER EVALUATION LOGIC, not paper
quality and not memo style.

For each review, recover every substantively distinct evaluation episode that
the memo supports. An episode is the smallest useful chain connecting what the
reviewer inspected, what they observed or found missing, why that mattered, the
resulting judgment, and any requested test or change. Do not split one coherent
argument merely because it spans several sentences; do split independent
arguments within one review. Preserve incomplete chains with empty arrays and
`quality.missing_links` rather than inventing missing logic.

Use `schemas/evaluation-episode-v0.2.json`, `enrichment_level=lite`, and stable
IDs `E-<paper_id>-<review_id>-NN`. Resolve memo-local reviewer citations such as
`L019` to `R-<review_id>:L019` with `provenance_level=primary`. Analytic wrapper
lines in this file have IDs `I-<review_id>:L###` and may be used with
`provenance_level=analytic_wrapper` when no primary line supports the memo's
interpretation. Never present memo or analyst inference as reviewer-explicit.

The concrete signature should retain the paper-specific object. The abstract
signature must remove paper-topic and method names while retaining the complete
evaluation logic. Do not assign pattern labels or taxonomy categories.

The first `quality.notes` entry must begin `Boundary rationale:` and briefly say
why the material belongs in one episode rather than being merged with or split
from adjacent material. The NN suffix in `episode_id` is the review-local order.

Copy `paper_id` and `review_id` exactly from each review's metadata line. Every
claim must describe concrete content from that memo. Never use placeholder prose
such as "the memo identifies a focus" or a generic "clarify or test" request.
When no request is supported, use an empty array and mark `request` missing.

Do not default to one episode per review. A substantive memo commonly contains
2–6 episodes. Split baseline adequacy, mechanism fidelity, robustness,
external validity, clarity blockage, and other independently warranted
judgments even when they all affect the same paper-level recommendation. Merge
only observations that participate in the same inferential chain.

Prefer primary evidence whenever the memo gives a local reviewer citation.
Every episode should contain at least one `R-<review_id>:L###` reference when
the relevant primary line is identifiable. Otherwise add `primary_provenance`
to `quality.missing_links` and explain the limitation in `quality.notes`.
"""


def extraction_protocol() -> str:
    return """# Agent protocol for Episode Lite shard extraction

For one `source-shard-NN.md`, write exactly three sibling outputs:

1. `episodes-shard-NN.jsonl`: one compact schema-valid Episode Lite JSON object
   per line; no Markdown fences and no prose.
2. `coverage-shard-NN.json`: an object with `shard` and one row per review.
   Every row must contain `review_id`, `episode_count`, `status`,
   `review_is_substantive`, `zero_episode_reason`, `provenance_failure`, and
   `notes` (an array of strings). Use `status=complete` when episodes were
   extracted and `status=zero` only when no episode is present. Distinguish a
   non-substantive review from extraction/provenance failure in the reason.
   Use `status=error` only for a review that could not be processed.
3. `patterns-shard-NN.md`: exploratory local patterns, unusual cases, likely
   merge/split ambiguities, and within-paper contrasts. These names are not
   canonical categories and their counts are not prevalence estimates.

Process all 5 reviews. Preserve paired reviews in one analysis. Before
finishing, parse every JSONL line, check every review's declared episode count,
ensure all evidence keys used by claims exist in that episode's registry, and
run `uv run python scripts/validate_episode_lite_1000.py --only-shard NN` from
the repository root. Fix the files until the validator reports zero errors.
"""


def render_shard(
    connection: sqlite3.Connection, shard_number: int, selections: Sequence[Selection]
) -> str:
    parts = [shard_header(shard_number)]
    for index, selection in enumerate(selections, 1):
        item = selection.candidate
        row = connection.execute(
            "SELECT memo FROM memos WHERE job_id=?",
            (f"initial:{item.review_id}",),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"memo missing for review {item.review_id}")
        parts.extend(
            [
                f"## Review {index:02d}: {item.review_id}",
                (
                    f"paper_id={item.paper_id}; decision_bucket={item.decision_bucket}; "
                    f"sample_kind={selection.sample_kind}; pair_id={selection.pair_id or 'none'}; "
                    f"paper_rating_variance={item.rating_variance:.3f}; "
                    f"paper_comments={item.comment_count}"
                ),
                number_memo_lines(item.review_id, row[0]),
            ]
        )
    return "\n\n".join(parts).rstrip() + "\n"


def prepare(
    database: Path,
    source_database: Path,
    output_dir: Path,
    *,
    seed: int,
) -> None:
    candidates = load_candidates(database, source_database)
    selections_by_bucket = {
        bucket: select_bucket(
            [item for item in candidates if item.decision_bucket == bucket],
            seed=seed + bucket_index,
        )
        for bucket_index, bucket in enumerate(BUCKET_ORDER)
    }
    shards = assign_shards(selections_by_bucket, seed=seed)

    connection = readonly_connection(database)
    try:
        rendered = [
            render_shard(connection, index, shard)
            for index, shard in enumerate(shards, 1)
        ]
    finally:
        connection.close()

    manifest = {
        "database": str(database.resolve()),
        "source_database": str(source_database.resolve()),
        "schema": str(Path("schemas/evaluation-episode-v0.2.json").resolve()),
        "seed": seed,
        "selection_count": sum(map(len, shards)),
        "unique_paper_count": len(
            {item.candidate.paper_id for shard in shards for item in shard}
        ),
        "shard_count": SHARD_COUNT,
        "reviews_per_shard": REVIEWS_PER_SHARD,
        "buckets": list(BUCKET_ORDER),
        "sampling_design": "purposive stratified design-validation sample",
        "probability_weights_available": False,
        "permitted_use": [
            "pattern discovery",
            "schema validation",
            "extraction quality and throughput measurement",
        ],
        "prohibited_use": [
            "population prevalence estimation",
            "causal comparison between decision buckets",
        ],
        "reviews": [
            {**asdict(item), "shard": shard_index}
            for shard_index, shard in enumerate(shards, 1)
            for item in shard
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    result_globs = (
        "episodes-shard-*.jsonl",
        "coverage-shard-*.json",
        "patterns-shard-*.md",
    )
    if any(any(output_dir.glob(pattern)) for pattern in result_globs):
        raise FileExistsError(
            f"pilot results already exist in {output_dir}; refusing to replace sources"
        )
    for index, text in enumerate(rendered, 1):
        (output_dir / f"source-shard-{index:02d}.md").write_text(
            text, encoding="utf-8"
        )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "AGENT_PROTOCOL.md").write_text(
        extraction_protocol(), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--source-database", type=Path, default=DEFAULT_SOURCE_DATABASE
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    prepare(
        args.database,
        args.source_database,
        args.output_dir,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
