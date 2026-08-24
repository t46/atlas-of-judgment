"""Charge-sheet controls (declared follow-up, 2026-08-21).

Two checks on Plate XIV's co-filing lifts, designed before results:

1. PAPER-VS-REVIEWER: the within-review lift cannot separate "a careful
   reviewer is careful everywhere" from "a sloppy paper draws both
   complaints". Control: the cross-reviewer lift — P(the OTHER reviewer
   of the same paper files B | this review files A) / P(B). If the
   attraction follows the paper, it survives the reviewer boundary; if
   it follows the reviewer, the cross-reviewer lift falls to ~1.

2. SAME-UNIT INFLATION: one unit can invoke several laws at once, so a
   pair can co-occur "within a review" without two separate criticisms.
   Control: recompute the within-review lift counting only reviews
   where the two laws come from DIFFERENT units.

Prints a table for the pairs the figure shows; writes
data/analysis/iclr/unit-taxonomy-2026-v1/chargesheet-control.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from build_lawtariff_data import load_laws_of

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"


def main() -> None:
    laws_of, names = load_laws_of()
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    total = dc.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT forum_id, reviewer_key FROM units"
        " WHERE reviewer_role='official_reviewer' AND temporal_position='initial_review')"
    ).fetchone()[0]

    review_units: dict[tuple, list] = defaultdict(list)   # (fid, rk) -> [set(laws) per unit]
    pks = sorted(laws_of)
    CH = 900
    for i in range(0, len(pks), CH):
        chunk = pks[i:i + CH]
        qs = ",".join("?" * len(chunk))
        for pk, fid, rk in dc.execute(
            f"SELECT unit_pk, forum_id, reviewer_key FROM units WHERE unit_pk IN ({qs})"
            " AND temporal_position='initial_review' AND reviewer_role='official_reviewer'",
            chunk,
        ):
            review_units[(fid, rk)].append(set(laws_of[pk]))
    # reviews with no assigned law are absent here but count in `total`
    n_forum_reviews = Counter()
    for fid, rk in review_units:
        n_forum_reviews[fid] += 1
    # how many reviews exist per forum in the full universe (law or not)
    forum_total = dict(dc.execute(
        "SELECT forum_id, COUNT(*) FROM (SELECT DISTINCT forum_id, reviewer_key FROM units"
        " WHERE reviewer_role='official_reviewer' AND temporal_position='initial_review')"
        " GROUP BY forum_id"))
    dc.close()

    review_laws = {k: set().union(*us) for k, us in review_units.items()}
    by_forum = defaultdict(list)
    for (fid, rk), laws in review_laws.items():
        by_forum[fid].append((rk, laws))

    marg = Counter()
    for laws in review_laws.values():
        for law in laws:
            marg[law] += 1
    base = {law: n / total for law, n in marg.items()}

    cs = json.loads((V / "chargesheet-data.json").read_text())
    shown = [(p, "top") for p in cs["top_cross"]] + [(p, "bottom") for p in cs["bottom_cross"]]
    pairs = [(((p["a"]["docket"], p["a"]["key"]), (p["b"]["docket"], p["b"]["key"])), p["lift"], tier)
             for p, tier in shown]

    out = []
    for (A, B), lift_within, tier in pairs:
        # 2) within-review lift from DIFFERENT units only
        nj_diff = 0
        for units in review_units.values():
            hasA = [A in u for u in units]
            hasB = [B in u for u in units]
            ok = any(a and b and ia != ib for ia, a in enumerate(hasA) for ib, b in enumerate(hasB) if b) \
                 if (any(hasA) and any(hasB)) else False
            if ok:
                nj_diff += 1
        lift_diffunit = nj_diff * total / (marg[A] * marg[B])

        # 1) cross-reviewer lift, symmetrized: P(other review in forum has B | this has A) / P(B)
        def cross(X, Y):
            hit = opp = 0
            for fid, revs in by_forum.items():
                ft = forum_total.get(fid, len(revs))
                if ft < 2:
                    continue
                nx = sum(1 for _rk, laws in revs if X in laws)
                ny = sum(1 for _rk, laws in revs if Y in laws)
                if nx == 0:
                    continue
                for _rk, laws in revs:
                    if X in laws:
                        opp += ft - 1
                        hit += ny - (1 if Y in laws else 0)
            return (hit / opp) / base[Y] if opp else float("nan")
        lift_cross = (cross(A, B) + cross(B, A)) / 2

        out.append({
            "a": f"{A[0]}/{A[1]}", "b": f"{B[0]}/{B[1]}", "tier": tier,
            "lift_within": lift_within,
            "lift_diffunit": round(lift_diffunit, 3),
            "lift_cross": round(lift_cross, 3),
        })

    (V / "chargesheet-control.json").write_text(json.dumps(out))
    print(f"{'tier':6s} {'within':>7s} {'diff-unit':>9s} {'cross-rev':>9s}  pair")
    for r in out:
        print(f"{r['tier']:6s} {r['lift_within']:7.2f} {r['lift_diffunit']:9.2f} {r['lift_cross']:9.2f}  {r['a']} × {r['b']}")
    import statistics as st
    for tier in ("top", "bottom"):
        rs = [r for r in out if r["tier"] == tier]
        print(f"{tier}: median within {st.median(r['lift_within'] for r in rs):.2f}"
              f" · diff-unit {st.median(r['lift_diffunit'] for r in rs):.2f}"
              f" · cross-reviewer {st.median(r['lift_cross'] for r in rs):.2f}")


if __name__ == "__main__":
    main()
