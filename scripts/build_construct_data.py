"""Construct validity of the instrument: do ICLR sub-scores track unit content?

For each object of scrutiny o and each sub-score s in {soundness, presentation,
contribution} (1-4 scale, ICLR 2026): the rating-adjusted difference in mean
sub-score between reviews with >=1 negative unit on o and reviews without —
computed within each overall-rating level, then weighted-averaged, so the
overall harshness of the review is held fixed. If the taxonomy and the venue's
own decomposition agree, clarity-negatives should depress `presentation`
specifically, theory/stats-negatives `soundness`, novelty-negatives
`contribution`.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/construct-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
SUBS = ("soundness", "presentation", "contribution")


def main() -> None:
    taxonomy = json.loads((V / "taxonomy-v1.json").read_text())
    obj_keys = [c["key"] for c in taxonomy["inspected_object"]]

    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    meta: dict[str, dict] = {}
    for note_id, cj in aconn.execute(
        "SELECT note_id, content_json FROM messages WHERE year = 2026 AND kind = 'official_review'"
    ):
        try:
            c = json.loads(cj)
            row = {}
            r = c.get("rating")
            if not isinstance(r, int):
                continue
            row["rating"] = r
            ok = True
            for s in SUBS:
                v = c.get(s)
                if isinstance(v, str):
                    v = int(str(v).split(":")[0].strip())
                if not isinstance(v, int):
                    ok = False
                    break
                row[s] = v
            if ok:
                meta[note_id] = row
        except (json.JSONDecodeError, ValueError):
            continue
    aconn.close()
    print(f"{len(meta)} reviews with rating + all three sub-scores")

    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    neg_objs: dict[str, set] = defaultdict(set)
    for review_id, obj in conn.execute(
        "SELECT DISTINCT u.review_id, l.object_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk WHERE u.valence = 'negative'"
    ):
        if review_id in meta:
            neg_objs[review_id].add(obj)
    all_reviews = [r[0] for r in conn.execute("SELECT review_id FROM reviews") if r[0] in meta]
    conn.close()

    # bucket reviews by rating
    by_rating: dict[int, list[str]] = defaultdict(list)
    for rid in all_reviews:
        by_rating[meta[rid]["rating"]].append(rid)

    matrix = {}
    for o in obj_keys:
        deltas = {s: 0.0 for s in SUBS}
        weight = 0
        for rating, rids in by_rating.items():
            with_o = [r for r in rids if o in neg_objs[r]]
            without = [r for r in rids if o not in neg_objs[r]]
            if len(with_o) < 30 or len(without) < 30:
                continue
            w = len(with_o)
            weight += w
            for s in SUBS:
                mw = sum(meta[r][s] for r in with_o) / len(with_o)
                mo = sum(meta[r][s] for r in without) / len(without)
                deltas[s] += (mw - mo) * w
        matrix[o] = {s: round(deltas[s] / weight, 4) for s in SUBS} if weight else {}
    payload = {"n_reviews": len(all_reviews), "matrix": matrix}
    (V / "construct-data.json").write_text(json.dumps(payload) + "\n")
    for o in obj_keys:
        m = matrix[o]
        print(f"{o:26s} " + "  ".join(f"{s[:5]}:{m.get(s, 0):+.3f}" for s in SUBS))


if __name__ == "__main__":
    main()
