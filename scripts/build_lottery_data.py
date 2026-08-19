"""The measurement: how reproducible is a paper's score across reviewers?

Per year (2018-2026): one-way random-effects decomposition of official review
ratings into a paper component and a residual reviewer-draw component --
ICC(1,1) via the standard ANOVA estimator -- plus the within/between-forum
spread and the expected absolute gap between two reviewers of the same paper.

For 2026 additionally: leave-one-out conditional score distributions,
P(reviewer's score | mean of the other reviewers' scores), which power the
interactive "draw another reviewer" instrument.

Framing note: this measures a property of the system (measurement noise),
not of individual reviewers.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/lottery-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def parse_rating(c: dict) -> float | None:
    for k in ("rating", "recommendation"):
        v = c.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            m = re.match(r"\s*(\d+)", v)
            if m:
                return float(m.group(1))
    return None


def icc_oneway(groups: list[list[float]]) -> dict:
    groups = [g for g in groups if len(g) >= 2]
    J = len(groups)
    N = sum(len(g) for g in groups)
    grand = sum(sum(g) for g in groups) / N
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    msb = ssb / (J - 1)
    msw = ssw / (N - J)
    n0 = (N - sum(len(g) ** 2 for g in groups) / N) / (J - 1)
    icc = (msb - msw) / (msb + (n0 - 1) * msw)
    # expected |gap| between two reviewers of one paper
    gaps, npairs = 0.0, 0
    for g in groups:
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                gaps += abs(g[i] - g[j]); npairs += 1
    return {
        "n_forums": J, "n_reviews": N, "kbar": round(N / J, 2),
        "icc": round(icc, 4),
        "sd_within": round(msw ** 0.5, 3),
        "sd_between_papers": round(max(0.0, (msb - msw) / n0) ** 0.5, 3),
        "mean": round(grand, 3),
        "expected_gap": round(gaps / npairs, 3),
    }


def main() -> None:
    conn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    by_year: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for year, forum_id, cj in conn.execute(
        "SELECT year, forum_id, content_json FROM messages WHERE kind = 'official_review'"
    ):
        try:
            r = parse_rating(json.loads(cj))
        except json.JSONDecodeError:
            continue
        if r is not None:
            by_year[year][forum_id].append(r)
    conn.close()

    import math
    years = {}
    for year in sorted(by_year):
        stats = icc_oneway(list(by_year[year].values()))
        # normalized score entropy: how spread out are this year's scores over its own levels
        counts = {}
        for g in by_year[year].values():
            for r in g:
                counts[r] = counts.get(r, 0) + 1
        tot = sum(counts.values())
        ent = -sum(c / tot * math.log(c / tot) for c in counts.values())
        stats["entropy_norm"] = round(ent / math.log(len(counts)), 4) if len(counts) > 1 else 0
        if stats["n_forums"] >= 200:
            years[year] = stats
        print(year, stats["entropy_norm"], stats)

    # 2026 leave-one-out conditional distributions (even scale 0..10)
    cond: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g in by_year[2026].values():
        if len(g) < 3:
            continue
        for i, r in enumerate(g):
            others = [x for j, x in enumerate(g) if j != i]
            om = sum(others) / len(others)
            b = round(om)          # other-mean bin, integer 0..10
            cond[str(b)][str(int(r))] += 1
    cond = {b: dict(d) for b, d in cond.items() if sum(d.values()) >= 400}

    # 2026 split-gap context: how common is each max-min gap, and how do such panels fare
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    accepted = {}
    for fid, decision, withdrawn in aconn.execute(
        "SELECT forum_id, decision, withdrawn FROM papers WHERE year = 2026"
    ):
        if decision and not withdrawn:
            accepted[fid] = any(w in decision.lower() for w in ("accept", "oral", "poster", "spotlight"))
    aconn.close()
    gaps: dict[int, list[int]] = {}
    for fid, g in by_year[2026].items():
        if len(g) < 3:
            continue
        gap = int(max(g) - min(g))
        acc = accepted.get(fid)
        gaps.setdefault(gap, [0, 0, 0])
        gaps[gap][0] += 1
        if acc is not None:
            gaps[gap][2] += 1
            gaps[gap][1] += acc
    gap_stats = {str(k): {"n": v[0], "accept": round(v[1] / v[2], 4) if v[2] > 50 else None}
                 for k, v in sorted(gaps.items())}
    payload = {"years": years, "cond2026": cond, "gaps2026": gap_stats}
    (V / "lottery-data.json").write_text(json.dumps(payload) + "\n")
    print("bins:", {b: sum(d.values()) for b, d in cond.items()})


if __name__ == "__main__":
    main()
