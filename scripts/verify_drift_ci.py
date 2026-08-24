"""Reviewer-clustered bootstrap CIs for Plate XXIX's four drift movers.

Verifies the caption claim that the 2018→2026 share changes for clarity,
theory, compute_cost and stats_metrics are far outside sampling noise.
Units within a reviewer are correlated, so reviewers (reviewer_key within
forum) are the resampling cluster, 1,000 draws.

Run: uv run python scripts/verify_drift_ci.py
Result (2026-08-24): z = -15.6 / -9.0 / +15.2 / +8.1 — all four moves at
eight to sixteen times their clustered standard errors.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
MOVERS = ("clarity", "theory", "compute_cost", "stats_metrics")


def main() -> None:
    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT u.year, u.reviewer_key || '|' || u.forum_id, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.year IN (2018, 2026) AND u.reviewer_role = 'official_reviewer'"
    ).fetchall()
    conn.close()

    agg = {2018: defaultdict(lambda: np.zeros(len(MOVERS) + 1)),
           2026: defaultdict(lambda: np.zeros(len(MOVERS) + 1))}
    for yr, rk, obj in rows:
        a = agg[yr][rk]
        a[-1] += 1
        if obj in MOVERS:
            a[MOVERS.index(obj)] += 1

    rng = np.random.default_rng(7)
    res = {}
    for yr in (2018, 2026):
        M = np.array(list(agg[yr].values()))
        n = len(M)
        boots = np.empty((1000, len(MOVERS)))
        for b in range(1000):
            S = M[rng.integers(0, n, n)].sum(0)
            boots[b] = S[:-1] / S[-1]
        res[yr] = (M.sum(0)[:-1] / M.sum(0)[-1], boots, n)

    (p18, b18, n18), (p26, b26, n26) = res[2018], res[2026]
    print(f"reviewers: 2018={n18:,} 2026={n26:,}")
    for i, m in enumerate(MOVERS):
        d = (p26[i] - p18[i]) * 100
        db = (b26[:, i] - b18[:, i]) * 100
        lo, hi = np.percentile(db, [2.5, 97.5])
        print(f"{m:14s} {p18[i]*100:5.1f} -> {p26[i]*100:5.1f}"
              f"  d{d:+5.2f}pp  95%CI[{lo:+.2f},{hi:+.2f}]  z={d/db.std():5.1f}")


if __name__ == "__main__":
    main()
