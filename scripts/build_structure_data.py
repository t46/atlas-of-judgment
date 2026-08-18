"""Within-review logic structure: how a review unfolds, unit by unit.

Computed over unit-taxonomy-2026-v1 (compact track, review-level order):
  - opening moves: what category a review STARTS with, vs its overall share
  - position curves: category share and negativity by relative position (5 bins)
  - transitions: consecutive-unit object-category pairs, observed/expected lift
  - arcs: first-unit x last-unit valence combinations (reviews with >= 3 units)

Writes data/analysis/iclr/unit-taxonomy-2026-v1/structure-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
BINS = 5


def valence_group(v: str) -> str:
    if v == "positive":
        return "positive"
    if v == "negative":
        return "negative"
    return "hedged"  # conditional / uncertain / mixed


def main() -> None:
    conn = sqlite3.connect(f"file:{OUTPUT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT u.review_id, u.unit_index, u.valence, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " ORDER BY u.review_id, u.unit_index"
    ).fetchall()
    conn.close()

    reviews: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for review_id, _idx, valence, obj in rows:
        reviews[review_id].append((obj, valence))

    overall = Counter()
    first = Counter()
    last = Counter()
    pos_cat = [Counter() for _ in range(BINS)]
    pos_neg = [0] * BINS
    pos_tot = [0] * BINS
    pos_posv = [0] * BINS
    trans = Counter()
    arcs = Counter()
    lengths = Counter()

    for units in reviews.values():
        n = len(units)
        lengths[min(n, 15)] += 1
        for obj, _v in units:
            overall[obj] += 1
        first[units[0][0]] += 1
        last[units[-1][0]] += 1
        for (a, _), (b, _) in zip(units, units[1:]):
            trans[(a, b)] += 1
        if n >= 3:
            arcs[(valence_group(units[0][1]), valence_group(units[-1][1]))] += 1
            for i, (obj, v) in enumerate(units):
                b = min(BINS - 1, int(i / (n - 1) * BINS)) if n > 1 else 0
                pos_cat[b][obj] += 1
                pos_tot[b] += 1
                if v == "negative":
                    pos_neg[b] += 1
                if v == "positive":
                    pos_posv[b] += 1

    total_units = sum(overall.values())
    n_reviews = len(reviews)
    opening = []
    for key in overall:
        share_first = first[key] / n_reviews
        share_overall = overall[key] / total_units
        share_last = last[key] / n_reviews
        opening.append(
            {
                "key": key,
                "first_share": round(share_first, 4),
                "last_share": round(share_last, 4),
                "overall_share": round(share_overall, 4),
                "open_lift": round(share_first / share_overall, 3),
                "close_lift": round(share_last / share_overall, 3),
            }
        )
    opening.sort(key=lambda d: -d["open_lift"])

    total_trans = sum(trans.values())
    row_tot = Counter()
    col_tot = Counter()
    for (a, b), c in trans.items():
        row_tot[a] += c
        col_tot[b] += c
    transitions = [
        {
            "a": a,
            "b": b,
            "n": c,
            "lift": round(c / (row_tot[a] * col_tot[b] / total_trans), 3),
        }
        for (a, b), c in trans.items()
        if c >= 200
    ]

    payload = {
        "n_reviews": n_reviews,
        "n_reviews_ge3": sum(arcs.values()),
        "opening": opening,
        "position": {
            "bins": BINS,
            "neg_rate": [round(pos_neg[i] / pos_tot[i], 4) for i in range(BINS)],
            "pos_rate": [round(pos_posv[i] / pos_tot[i], 4) for i in range(BINS)],
            "cat_share": [
                {k: round(v / pos_tot[i], 4) for k, v in pos_cat[i].items()}
                for i in range(BINS)
            ],
        },
        "transitions": transitions,
        "arcs": [
            {"first": a, "last": b, "n": c, "share": round(c / sum(arcs.values()), 4)}
            for (a, b), c in arcs.most_common()
        ],
        "lengths": dict(sorted(lengths.items())),
    }
    out = OUTPUT_DIR / "structure-data.json"
    out.write_text(json.dumps(payload) + "\n")
    print(f"{out} written; reviews={n_reviews}, ge3={payload['n_reviews_ge3']}")


if __name__ == "__main__":
    main()
