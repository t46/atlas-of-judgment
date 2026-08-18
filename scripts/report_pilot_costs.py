"""Report pilot usage and corpus-scale DeepSeek cost projections."""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from pathlib import Path


DEFAULT_SOURCE = Path("data/processed/iclr/analysis.sqlite3")
DEFAULT_PILOT = Path("data/analysis/iclr/pilot.sqlite3")
PRODUCTION_STAGES = ("initial_blind", "trajectory", "paper_synthesis", "forum_direct")


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = fraction * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def stage_stats(pilot: sqlite3.Connection, stage: str) -> dict[str, float]:
    rows = pilot.execute(
        """
        SELECT a.actual_microusd, a.prompt_tokens, a.completion_tokens,
               a.finish_reason
        FROM api_calls a JOIN jobs j USING(job_id)
        WHERE j.stage=? AND a.status='complete'
        """,
        (stage,),
    ).fetchall()
    costs = [row[0] / 1_000_000 for row in rows]
    return {
        "count": len(rows),
        "usd": sum(costs),
        "mean": statistics.mean(costs) if costs else 0.0,
        "p25": percentile(costs, 0.25),
        "p50": percentile(costs, 0.50),
        "p75": percentile(costs, 0.75),
        "avg_in": statistics.mean(row[1] for row in rows) if rows else 0.0,
        "avg_out": statistics.mean(row[2] for row in rows) if rows else 0.0,
        "length": sum(row[3] == "length" for row in rows),
    }


def population(source: sqlite3.Connection, years: tuple[int, ...]) -> dict[str, int]:
    placeholders = ",".join("?" for _ in years)
    reviews = source.execute(
        f"SELECT COUNT(*) FROM messages WHERE kind='official_review' AND year IN ({placeholders})",
        years,
    ).fetchone()[0]
    papers = source.execute(
        f"SELECT COUNT(*) FROM papers WHERE review_count > 0 AND year IN ({placeholders})",
        years,
    ).fetchone()[0]
    exchanges = source.execute(
        f"""
        SELECT COUNT(*) FROM messages r
        WHERE r.kind='official_review' AND r.year IN ({placeholders})
          AND EXISTS (
              SELECT 1 FROM messages c
              WHERE c.year=r.year AND c.replyto=r.note_id
          )
        """,
        years,
    ).fetchone()[0]
    return {"reviews": reviews, "papers": papers, "exchanges": exchanges}


def project(stats: dict[str, dict[str, float]], counts: dict[str, int], key: str) -> dict[str, float]:
    multipliers = {
        "initial_blind": counts["reviews"],
        "trajectory": counts["exchanges"],
        "paper_synthesis": counts["papers"],
        "forum_direct": counts["papers"],
    }
    stages = ("forum_direct",) if key == "direct" else (
        "initial_blind",
        "trajectory",
        "paper_synthesis",
    )
    return {
        quantile: sum(stats[stage][quantile] * multipliers[stage] for stage in stages)
        for quantile in ("p25", "p50", "mean", "p75")
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    args = parser.parse_args()

    source = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    pilot = sqlite3.connect(f"file:{args.pilot}?mode=ro", uri=True)
    try:
        stats = {stage: stage_stats(pilot, stage) for stage in PRODUCTION_STAGES}
        comparison = stage_stats(pilot, "method_comparison")
        print("Final successful calls (excluding superseded attempts)")
        print("stage\tjobs\tUSD\tmean/job\tavg input\tavg output\tlength")
        for stage in (*PRODUCTION_STAGES, "method_comparison"):
            item = comparison if stage == "method_comparison" else stats[stage]
            print(
                f"{stage}\t{int(item['count'])}\t${item['usd']:.6f}\t"
                f"${item['mean']:.6f}\t{item['avg_in']:.1f}\t"
                f"{item['avg_out']:.1f}\t{int(item['length'])}"
            )

        state = pilot.execute(
            "SELECT budget_microusd, spent_microusd FROM budget_state"
        ).fetchone()
        archived = pilot.execute(
            "SELECT COUNT(*), COALESCE(SUM(actual_microusd),0) FROM api_call_attempts"
        ).fetchone()
        print(
            f"\nHard-cap ledger: ${state[1] / 1_000_000:.6f} spent / "
            f"${state[0] / 1_000_000:.2f}; {archived[0]} superseded attempts "
            f"charged ${archived[1] / 1_000_000:.6f}."
        )

        for label, years in (("ICLR 2026", (2026,)), ("ICLR 2018-2026", tuple(range(2018, 2027)))):
            counts = population(source, years)
            direct = project(stats, counts, "direct")
            layered = project(stats, counts, "layered")
            print(
                f"\n{label}: {counts['papers']:,} review-bearing papers, "
                f"{counts['reviews']:,} reviews, {counts['exchanges']:,} review exchanges"
            )
            print(
                "  direct forum memos: "
                f"mean ${direct['mean']:.2f} "
                f"(per-job p25/p50/p75 projection ${direct['p25']:.2f}/"
                f"${direct['p50']:.2f}/${direct['p75']:.2f})"
            )
            print(
                "  layered review+trajectory+paper synthesis: "
                f"mean ${layered['mean']:.2f} "
                f"(per-job p25/p50/p75 projection ${layered['p25']:.2f}/"
                f"${layered['p50']:.2f}/${layered['p75']:.2f})"
            )
    finally:
        pilot.close()
        source.close()


if __name__ == "__main__":
    main()
