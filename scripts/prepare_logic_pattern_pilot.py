"""Prepare a deterministic ICLR 2026 pilot for reviewer-logic discovery.

The full paper-synthesis memos already contain a free-form first-pass analysis.
This script extracts only sections likely to describe what reviewers inspected,
the standards they applied, and the logic connecting observations to judgments.
It deliberately avoids defining an evaluation taxonomy before the pilot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_DATABASE = Path("data/analysis/iclr/production-2026.sqlite3")
DEFAULT_OUTPUT_DIR = Path("data/analysis/iclr/logic-pattern-pilot")
DEFAULT_SEED = 20260816
BUCKET_ORDER = ("Accept (Oral)", "Accept (Poster)", "Reject", "No decision")
PAPERS_PER_BUCKET = 25
SHARD_COUNT = 8
MAX_SECTION_CHARS = 4_000
MAX_PAPER_CHARS = 12_000

HEADING_RE = re.compile(r"(?m)^(#{2,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    path: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class Candidate:
    paper_id: str
    decision_bucket: str
    review_count: int
    comment_count: int
    rating_variance: float
    memo_chars: int
    detected_section_count: int
    included_section_count: int


def stable_key(seed: int, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def parse_sections(markdown: str) -> list[Section]:
    """Split Markdown into heading sections while retaining each heading path."""
    matches = list(HEADING_RE.finditer(markdown))
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[body_start:body_end].strip()
        sections.append(
            Section(
                level=level,
                title=title,
                path=tuple(item[1] for item in stack),
                body=body,
            )
        )
    return sections


def normalize_heading(title: str) -> str:
    text = title.casefold().replace("’", "'")
    text = re.sub(r"[`*_\"]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def section_role(title: str) -> str | None:
    """Return a broad retrieval role, not an evaluative taxonomy."""
    heading = normalize_heading(title)
    if "inspect" in heading or "objects of" in heading or "object of attention" in heading:
        return "inspected"
    if (
        "counted as good" in heading
        or "counted as bad" in heading
        or "good or bad" in heading
        or "good and bad" in heading
        or re.search(r"\bgood\s*=", heading)
    ):
        return "good_bad_standard"
    if (
        "evaluative logic" in heading
        or "evaluation logic" in heading
        or "logic connecting" in heading
        or "observation to evaluation" in heading
        or "observation to judgment" in heading
        or "decision logic" in heading
        or "reasoning logic" in heading
    ):
        return "logic"
    return None


def relevant_sections(markdown: str) -> list[tuple[str, Section]]:
    return [
        (role, section)
        for section in parse_sections(markdown)
        if (role := section_role(section.title)) is not None and section.body
    ]


def render_excerpt(markdown: str) -> tuple[str, int, int]:
    """Render compact logic-bearing sections, preserving source references."""
    selected = relevant_sections(markdown)
    chunks: list[str] = []
    used = 0
    for role, section in selected:
        body = section.body[:MAX_SECTION_CHARS].rstrip()
        path = " > ".join(section.path)
        chunk = f"### [{role}] {path}\n\n{body}"
        if chunks and used + len(chunk) > MAX_PAPER_CHARS:
            break
        chunks.append(chunk)
        used += len(chunk)
    return "\n\n".join(chunks), len(selected), len(chunks)


def load_candidates(connection: sqlite3.Connection) -> tuple[list[Candidate], dict[str, str]]:
    candidates: list[Candidate] = []
    excerpts: dict[str, str] = {}
    rows = connection.execute(
        """
        SELECT s.paper_id, s.decision_bucket, s.review_count, s.comment_count,
               s.rating_variance, LENGTH(m.memo), m.memo
        FROM sample_papers AS s
        JOIN memos AS m
          ON m.paper_id=s.paper_id AND m.stage='paper_synthesis'
        ORDER BY s.paper_id
        """
    )
    for paper_id, bucket, reviews, comments, variance, memo_chars, memo in rows:
        excerpt, detected_count, included_count = render_excerpt(memo)
        if not excerpt:
            continue
        excerpts[paper_id] = excerpt
        candidates.append(
            Candidate(
                paper_id=paper_id,
                decision_bucket=bucket,
                review_count=reviews,
                comment_count=comments,
                rating_variance=variance,
                memo_chars=memo_chars,
                detected_section_count=detected_count,
                included_section_count=included_count,
            )
        )
    return candidates, excerpts


def select_candidates(candidates: list[Candidate], *, seed: int) -> list[Candidate]:
    """Select a stable, outcome-balanced sample with broad process variation."""
    selected: list[Candidate] = []
    for bucket_index, bucket in enumerate(BUCKET_ORDER):
        pool = [item for item in candidates if item.decision_bucket == bucket]
        if len(pool) < PAPERS_PER_BUCKET:
            raise ValueError(
                f"need {PAPERS_PER_BUCKET} candidates in {bucket}, found {len(pool)}"
            )
        # Sort first by stable pseudo-random key, then divide the ordered pool
        # across papers with different disagreement and discussion levels.
        pool.sort(
            key=lambda item: (
                item.rating_variance,
                item.comment_count,
                stable_key(seed + bucket_index, item.paper_id),
            )
        )
        step = len(pool) / PAPERS_PER_BUCKET
        for index in range(PAPERS_PER_BUCKET):
            start = int(index * step)
            end = max(start + 1, int((index + 1) * step))
            window = pool[start:end]
            selected.append(
                min(
                    window,
                    key=lambda item: stable_key(seed + bucket_index, item.paper_id),
                )
            )
    return selected


def assign_shards(selected: list[Candidate]) -> list[list[Candidate]]:
    shards: list[list[Candidate]] = [[] for _ in range(SHARD_COUNT)]
    by_bucket = {
        bucket: [item for item in selected if item.decision_bucket == bucket]
        for bucket in BUCKET_ORDER
    }
    for bucket_index, bucket in enumerate(BUCKET_ORDER):
        for item_index, item in enumerate(by_bucket[bucket]):
            shards[(item_index + bucket_index) % SHARD_COUNT].append(item)
    return shards


def shard_header(shard_number: int) -> str:
    return f"""# Reviewer evaluation-logic discovery shard {shard_number}

This shard contains excerpts from machine-produced free analyses of public ICLR
2026 review forums. Study HUMAN REVIEWER EVALUATION LOGIC. The central question
is: what did reviewers inspect, what did they observe, what explicit or inferred
standard/comparison/assumption made the observation matter, and how did they
reason from it to a positive, negative, conditional, or uncertain judgment?

Do not analyze paper topics, paper quality, or memo writing style. Do not impose
a fixed taxonomy. Inductively discover recurring reasoning patterns while
preserving differences, incomplete chains, counterexamples, and unusual logic.
Distinguish explicit reviewer reasoning from the memo analyst's inference.
References in brackets must be retained with the paper_id when citing evidence.

Return:
1. 8–15 recurring evaluation-logic patterns, each expressed as an abstract
   reasoning chain and supported by at least two papers when possible.
2. Important variants that look similar superficially but use different logic.
3. Rare, contradictory, or hard-to-classify cases that a taxonomy might erase.
4. Candidate dimensions for comparing evaluation logics without freezing them
   into final categories.
5. A short account of what this shard suggests human reviewers are actually
   doing when they evaluate a paper.
"""


def render_shard(
    shard_number: int, candidates: list[Candidate], excerpts: dict[str, str]
) -> str:
    parts = [shard_header(shard_number)]
    for index, item in enumerate(candidates, 1):
        parts.extend(
            [
                f"## Case {index}: paper_id={item.paper_id}",
                (
                    f"Decision bucket: {item.decision_bucket}; reviews: "
                    f"{item.review_count}; comments: {item.comment_count}; "
                    f"rating variance: {item.rating_variance:.3f}; included "
                    f"logic-bearing sections: {item.included_section_count} of "
                    f"{item.detected_section_count} detected"
                ),
                excerpts[item.paper_id],
            ]
        )
    return "\n\n".join(parts).rstrip() + "\n"


def prepare(database: Path, output_dir: Path, *, seed: int) -> None:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        candidates, excerpts = load_candidates(connection)
    finally:
        connection.close()

    selected = select_candidates(candidates, seed=seed)
    shards = assign_shards(selected)
    if len(selected) != PAPERS_PER_BUCKET * len(BUCKET_ORDER):
        raise RuntimeError(f"unexpected selection size: {len(selected)}")
    if max(len(shard) for shard in shards) - min(len(shard) for shard in shards) > 1:
        raise RuntimeError(f"unbalanced shards: {[len(shard) for shard in shards]}")

    rendered = [
        render_shard(index, shard, excerpts)
        for index, shard in enumerate(shards, 1)
    ]
    manifest = {
        "database": str(database.resolve()),
        "seed": seed,
        "papers_per_bucket": PAPERS_PER_BUCKET,
        "shard_count": SHARD_COUNT,
        "selection_count": len(selected),
        "shard_sizes": [len(shard) for shard in shards],
        "papers": [
            {**asdict(item), "shard": shard_index}
            for shard_index, shard in enumerate(shards, 1)
            for item in shard
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    for index, text in enumerate(rendered, 1):
        (output_dir / f"shard-{index:02d}.md").write_text(text, encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    prepare(args.database, args.output_dir, seed=args.seed)


if __name__ == "__main__":
    main()
