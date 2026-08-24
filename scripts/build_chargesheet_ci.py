"""Charge-sheet rigor pass 2 (declared 2026-08-22).

1. Paper-resampling 95% CIs for every displayed pair's within-review
   lift (forums resampled with replacement, B=400) — the displayed
   extremes were selected from many pairs, so intervals guard against
   winner's-curse readings.
2. Year-adjusted cross-reviewer lift: the two reviewers of one paper
   share a year, so the raw cross lift folds year-level fashion in a
   law's base rate into the "paper's part". Re-basing chance within
   each year removes it.

Merges ci_lo / ci_hi / lift_cross_yr into chargesheet-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from build_lawtariff_data import load_laws_of

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
B = 400
SEED = 46


def main() -> None:
    laws_of, _names = load_laws_of()
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    review_laws: dict[tuple, set] = defaultdict(set)
    review_year: dict[tuple, int] = {}
    pks = sorted(laws_of)
    CH = 900
    for i in range(0, len(pks), CH):
        chunk = pks[i:i + CH]
        qs = ",".join("?" * len(chunk))
        for pk, fid, rk, yr in dc.execute(
            f"SELECT unit_pk, forum_id, reviewer_key, year FROM units WHERE unit_pk IN ({qs})"
            " AND temporal_position='initial_review' AND reviewer_role='official_reviewer'",
            chunk,
        ):
            review_laws[(fid, rk)].update(laws_of[pk])
            review_year[(fid, rk)] = yr
    total = dc.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT forum_id, reviewer_key FROM units"
        " WHERE reviewer_role='official_reviewer' AND temporal_position='initial_review')"
    ).fetchone()[0]
    forum_total = dict(dc.execute(
        "SELECT forum_id, COUNT(*) FROM (SELECT DISTINCT forum_id, reviewer_key, year FROM units"
        " WHERE reviewer_role='official_reviewer' AND temporal_position='initial_review')"
        " GROUP BY forum_id"))
    total_by_year = dict(dc.execute(
        "SELECT year, COUNT(*) FROM (SELECT DISTINCT forum_id, reviewer_key, year FROM units"
        " WHERE reviewer_role='official_reviewer' AND temporal_position='initial_review')"
        " GROUP BY year"))
    dc.close()

    by_forum = defaultdict(list)
    forum_year = {}
    for (fid, rk), laws in review_laws.items():
        by_forum[fid].append(laws)
        forum_year[fid] = review_year[(fid, rk)]
    forums = sorted(by_forum)
    F = len(forums)

    marg = Counter()
    marg_yr = defaultdict(Counter)
    for (fid, rk), laws in review_laws.items():
        yr = review_year[(fid, rk)]
        for law in laws:
            marg[law] += 1
            marg_yr[yr][law] += 1

    cs = json.loads((V / "chargesheet-data.json").read_text())
    shown = cs["top_cross"] + cs["bottom_cross"]
    pairs = [((p["a"]["docket"], p["a"]["key"]), (p["b"]["docket"], p["b"]["key"])) for p in shown]

    # per-forum arrays for CI bootstrap (within-review lift)
    Nf = np.array([forum_total.get(f, len(by_forum[f])) for f in forums], dtype=np.float64)
    arrs = []
    for A, Bp in pairs:
        af = np.zeros(F); bf = np.zeros(F); jf = np.zeros(F)
        for i, f in enumerate(forums):
            for laws in by_forum[f]:
                a = A in laws; b = Bp in laws
                if a: af[i] += 1
                if b: bf[i] += 1
                if a and b: jf[i] += 1
        arrs.append((af, bf, jf))

    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, F, size=(B, F))
    Nsum = Nf[idx].sum(axis=1)
    out_ci = []
    for (af, bf, jf) in arrs:
        js = jf[idx].sum(axis=1)
        as_ = af[idx].sum(axis=1)
        bs = bf[idx].sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            lifts = js * Nsum / (as_ * bs)
        lifts = lifts[np.isfinite(lifts)]
        out_ci.append((float(np.percentile(lifts, 2.5)), float(np.percentile(lifts, 97.5))))

    # year-adjusted cross-reviewer lift
    def cross_yr(X, Y):
        hit = exp = 0.0
        for f in forums:
            revs = by_forum[f]
            ft = forum_total.get(f, len(revs))
            if ft < 2:
                continue
            yr = forum_year[f]
            base = marg_yr[yr][Y] / total_by_year[yr]
            ny = sum(1 for laws in revs if Y in laws)
            for laws in revs:
                if X in laws:
                    hit += ny - (1 if Y in laws else 0)
                    exp += (ft - 1) * base
        return hit / exp if exp else float("nan")

    for p, (A, Bp), (lo, hi) in zip(shown, pairs, out_ci):
        p["ci_lo"] = round(lo, 3)
        p["ci_hi"] = round(hi, 3)
        cy = (cross_yr(A, Bp) + cross_yr(Bp, A)) / 2
        p["lift_cross_yr"] = round(cy, 3)

    (V / "chargesheet-data.json").write_text(json.dumps(cs))
    import statistics as st
    for name, lst in (("top", cs["top_cross"]), ("bottom", cs["bottom_cross"])):
        print(f"{name}: median within {st.median(p['lift'] for p in lst):.2f}"
              f" · CI lo median {st.median(p['ci_lo'] for p in lst):.2f}"
              f" · cross raw {st.median(p['lift_cross'] for p in lst):.2f}"
              f" · cross year-adj {st.median(p['lift_cross_yr'] for p in lst):.2f}")
    for p in shown:
        print(f"  {p['lift']:.2f} [{p['ci_lo']:.2f},{p['ci_hi']:.2f}] cross {p['lift_cross']:.2f}→{p['lift_cross_yr']:.2f}"
              f"  {p['a']['docket']}/{p['a']['key']} × {p['b']['docket']}/{p['b']['key']}")


if __name__ == "__main__":
    main()
