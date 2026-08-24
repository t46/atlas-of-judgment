"""Per-rating logic profiles: join compact-track units to review scores.

Scores come from Layer I (messages.content_json, ICLR 2026 official reviews);
they were never shown to the extraction pipeline and are joined here for the
first time (same leakage contract as decisions). review_id == OpenReview note id.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/score-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def main() -> None:
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    scores: dict[str, dict] = {}
    for note_id, cj in aconn.execute(
        "SELECT note_id, content_json FROM messages WHERE year = 2026 AND kind = 'official_review'"
    ):
        try:
            c = json.loads(cj)
            r = c.get("rating")
            if isinstance(r, str):
                r = int(str(r).split(":")[0].strip())
            if not isinstance(r, int):
                continue
            conf = c.get("confidence")
            if isinstance(conf, str):
                conf = int(str(conf).split(":")[0].strip())
            scores[note_id] = {"rating": r, "confidence": conf if isinstance(conf, int) else None}
        except (json.JSONDecodeError, ValueError):
            continue
    aconn.close()
    print(f"{len(scores)} reviews with parsed ratings")

    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    per: dict[int, dict] = defaultdict(lambda: {
        "reviews": set(), "units": 0, "val": Counter(), "std": Counter(),
        "improve": 0, "conf_sum": 0, "conf_n": 0,
    })
    matched_reviews = 0
    for review_id, valence, std, imp in conn.execute(
        "SELECT u.review_id, u.valence, l.reasoning_key,"
        " u.suggested_improvement IS NOT NULL"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        sc = scores.get(review_id)
        if not sc:
            continue
        b = per[sc["rating"]]
        if review_id not in b["reviews"]:
            b["reviews"].add(review_id)
            matched_reviews += 1
            if sc["confidence"] is not None:
                b["conf_sum"] += sc["confidence"]
                b["conf_n"] += 1
        b["units"] += 1
        b["val"][valence] += 1
        b["std"][std] += 1
        b["improve"] += imp
    conn.close()

    out = {}
    for rating, b in sorted(per.items()):
        n_rev = len(b["reviews"])
        if n_rev < 50:
            continue
        out[str(rating)] = {
            "n_reviews": n_rev,
            "units_per_review": round(b["units"] / n_rev, 2),
            "valence": {k: round(v / b["units"], 4) for k, v in b["val"].items()},
            "standards": {k: round(v / b["units"], 4) for k, v in b["std"].items()},
            "improve_rate": round(b["improve"] / b["units"], 4),
            "mean_confidence": round(b["conf_sum"] / b["conf_n"], 2) if b["conf_n"] else None,
        }
    payload = {"year": 2026, "matched_reviews": matched_reviews, "by_rating": out}
    (V / "score-data.json").write_text(json.dumps(payload) + "\n")
    print(f"matched {matched_reviews} reviews into ratings: {sorted(out)}")
    for r, d in out.items():
        print(f"  rating {r}: n={d['n_reviews']:6d} u/rev={d['units_per_review']} "
              f"neg={d['valence'].get('negative', 0):.0%} pos={d['valence'].get('positive', 0):.0%} "
              f"merit={d['standards'].get('merit_recognition', 0):.0%} conf={d['mean_confidence']}")


if __name__ == "__main__":
    main()
