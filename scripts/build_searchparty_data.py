"""The search party: the panel as a collective search system.

For every 2026 forum with 3-4 rated official reviewers (review-level
track), count for each of the 12 objects how many of the panel's
reviewers inspected it (any unit, any valence). Panel-level: how many
objects were covered by at least one reviewer, by everyone, by no one.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/searchparty-data.json:
  by_object: object -> [share of panels where 0,1,2,3+ reviewers touched it]
  panel: distribution of objects-covered per panel + means
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"


def main() -> None:
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    seen = defaultdict(lambda: defaultdict(set))  # paper -> object -> {review_id}
    reviewers = defaultdict(set)
    for pid, rid, obj in uc.execute(
        "SELECT u.paper_id, u.review_id, l.object_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        seen[pid][obj].add(rid)
        reviewers[pid].add(rid)
    uc.close()

    objects = sorted({o for d in seen.values() for o in d})
    obj_touch = {o: Counter() for o in objects}   # object -> touched-by-k -> n panels
    cov_hist = Counter()
    all_hist = Counter()
    none_hist = Counter()
    n_panels = 0
    for pid, revs in reviewers.items():
        k = len(revs)
        if not (3 <= k <= 4):
            continue
        n_panels += 1
        covered = allcov = 0
        for o in objects:
            t = len(seen[pid].get(o, ()))
            obj_touch[o][min(t, 3)] += 1
            if t >= 1:
                covered += 1
            if t == k:
                allcov += 1
        cov_hist[covered] += 1
        all_hist[allcov] += 1
        none_hist[len(objects) - covered] += 1

    def dist(c):
        return [round(c.get(i, 0) / n_panels, 4) for i in range(4)]

    mean_cov = sum(k * v for k, v in cov_hist.items()) / n_panels
    mean_all = sum(k * v for k, v in all_hist.items()) / n_panels
    mean_none = sum(k * v for k, v in none_hist.items()) / n_panels

    out = {
        "n_panels": n_panels,
        "objects": objects,
        "by_object": {o: dist(obj_touch[o]) for o in objects},
        "mean_covered": round(mean_cov, 2),
        "mean_allcov": round(mean_all, 2),
        "mean_unseen": round(mean_none, 2),
        "cov_hist": {str(k): v for k, v in sorted(cov_hist.items())},
    }
    (V / "searchparty-data.json").write_text(json.dumps(out))
    print(f"{n_panels:,} panels of 3-4 · mean objects covered {mean_cov:.2f}/12"
          f" · inspected by all {mean_all:.2f} · seen by no one {mean_none:.2f}")
    print(f"{'object':>26s} {'none':>6s} {'one':>6s} {'two':>6s} {'3+':>6s}")
    for o in sorted(objects, key=lambda o: -out['by_object'][o][0]):
        d = out["by_object"][o]
        print(f"{o:>26s} {d[0]:>6.1%} {d[1]:>6.1%} {d[2]:>6.1%} {d[3]:>6.1%}")


if __name__ == "__main__":
    main()
