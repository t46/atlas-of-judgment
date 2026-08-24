"""Fig 7c's length-quartile robustness check, recomputed from units.sqlite3.

The caption claims novelty's no-remedy gap is not an artifact of terser units:
the share of denials arriving WITH a suggested fix should stay depressed for
novelty inside every quartile of unit length. Universe matches the elements
pipeline: negative units, official reviewers, initial reviews, observation and
reasoning both >= 40 chars; "has a fix" = non-empty, non-"none"
suggested_improvement; quartiles of len(observation)+len(reasoning) taken
within each object.

Run: uv run python scripts/verify_remedy_quartiles.py
Result (2026-08-24): novelty 59.6–61.1% in all four quartiles against
76–94% for the other eleven objects; novelty units are also slightly longer
than the pooled rest (221.5 vs 203.0 chars), so terseness is ruled out.
(The caption's earlier "63–66% against 80–92%" came from a pre-rebuild
pipeline and was corrected to this recomputation.)
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    rows = dc.execute(
        "SELECT l.object_key, coalesce(u.observation,''), coalesce(u.reasoning,''),"
        " coalesce(u.suggested_improvement,'')"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.valence = 'negative' AND u.reviewer_role = 'official_reviewer'"
        " AND u.temporal_position = 'initial_review'"
    ).fetchall()
    dc.close()

    byobj = defaultdict(list)
    for o, ob, re_, fx in rows:
        if len(ob.strip()) >= 40 and len(re_.strip()) >= 40:
            byobj[o].append((len(ob) + len(re_), fx.strip() not in ("",) and fx.strip().lower() != "none"))

    res = {}
    for o, lst in byobj.items():
        ls = np.array([x[0] for x in lst])
        fs = np.array([x[1] for x in lst], dtype=float)
        qs = np.percentile(ls, [25, 50, 75])
        idx = np.searchsorted(qs, ls, side="right")
        res[o] = [100 * fs[idx == q].mean() for q in range(4)]

    nov = res["novelty"]
    print("novelty has-fix % by quartile:", " ".join(f"{v:5.1f}" for v in nov))
    for q in range(4):
        others = [res[o][q] for o in res if o != "novelty"]
        print(f"Q{q + 1}: others {min(others):4.1f}–{max(others):4.1f}%")


if __name__ == "__main__":
    main()
