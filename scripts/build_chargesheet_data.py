"""The charge sheet: which laws are filed together.

Reviews (forum x reviewer, initial round) are re-assembled from the
per-unit law assignments of all twelve dockets, and every law PAIR's
co-filing count is compared against independence (lift). Because each
docket's sentences were sampled independently, a pair's joint rate is
thinned by the product of two inclusion rates while each marginal is
thinned by one — the ratio (lift) is unbiased to first order; absolute
co-filing rates are therefore NOT reported.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/chargesheet-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from build_lawtariff_data import load_laws_of

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
MIN_JOINT = 60
MIN_MARGINAL = 800


def main() -> None:
    laws_of, names = load_laws_of()

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    total = dc.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT forum_id, reviewer_key FROM units"
        " WHERE reviewer_role='official_reviewer' AND temporal_position='initial_review')"
    ).fetchone()[0]
    review_laws: dict[tuple, set] = defaultdict(set)
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
            review_laws[(fid, rk)].update(laws_of[pk])
    dc.close()
    print(f"{len(review_laws):,} of {total:,} reviews carry >=1 assigned law")

    marg = Counter()
    joint = Counter()
    for laws in review_laws.values():
        ll = sorted(laws)
        for law in ll:
            marg[law] += 1
        for a, b in combinations(ll, 2):
            joint[(a, b)] += 1

    keep = {law for law, n in marg.items() if n >= MIN_MARGINAL}
    pairs = []
    for (a, b), nj in joint.items():
        if a not in keep or b not in keep or nj < MIN_JOINT:
            continue
        lift = nj * total / (marg[a] * marg[b])
        pairs.append({
            "a": {"docket": a[0], "key": a[1], "name": names.get(a, a[1])},
            "b": {"docket": b[0], "key": b[1], "name": names.get(b, b[1])},
            "joint": nj, "lift": round(lift, 3),
            "cross": a[0] != b[0],
        })
    pairs.sort(key=lambda p: -p["lift"])
    cross = [p for p in pairs if p["cross"]]
    same = [p for p in pairs if not p["cross"]]

    # full cross-docket matrix for the overview figure: laws ordered by
    # docket (then by marginal), cells = lift where joint >= MIN_JOINT
    mlaws = sorted(keep, key=lambda L: (L[0], -marg[L]))
    lidx = {L: i for i, L in enumerate(mlaws)}
    cells = []
    for (a, b), nj in joint.items():
        if a in lidx and b in lidx and nj >= MIN_JOINT and a[0] != b[0]:
            lift = nj * total / (marg[a] * marg[b])
            cells.append([lidx[a], lidx[b], round(lift, 3), nj])

    payload = {
        "n_reviews_universe": total,
        "n_reviews_with_law": len(review_laws),
        "laws": [{"docket": d, "key": k, "name": names.get((d, k), k), "n": marg[(d, k)]}
                 for d, k in sorted(keep, key=lambda L: -marg[L])],
        "matrix_laws": [{"docket": d, "key": k, "name": names.get((d, k), k), "n": marg[(d, k)]}
                        for d, k in mlaws],
        "matrix_cells": cells,
        "top_cross": cross[:30],
        "bottom_cross": sorted(cross, key=lambda p: p["lift"])[:15],
        "top_same": same[:15],
        "min_joint": MIN_JOINT,
    }
    (V / "chargesheet-data.json").write_text(json.dumps(payload))
    print("top cross-docket pairs:")
    for p in cross[:12]:
        print(f"  {p['lift']:>6.2f}  {p['a']['docket']}/{p['a']['name'][:32]}  ×  {p['b']['docket']}/{p['b']['name'][:32]}  (n={p['joint']})")
    print("most avoided cross pairs:")
    for p in sorted(cross, key=lambda q: q["lift"])[:6]:
        print(f"  {p['lift']:>6.2f}  {p['a']['docket']}/{p['a']['name'][:32]}  ×  {p['b']['docket']}/{p['b']['name'][:32]}  (n={p['joint']})")


if __name__ == "__main__":
    main()
