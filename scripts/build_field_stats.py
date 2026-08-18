"""Basic ICLR field statistics per year, from the normalized layer (Layer I).

Per year: submissions, withdrawn/desk-rejected, decided, acceptance rate,
official reviews, reviews per reviewed paper, median review length (words).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/field-stats.json.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
OUT = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/field-stats.json"


def main() -> None:
    conn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    years = list(range(2018, 2027))
    stats = {}
    for y in years:
        p = conn.execute(
            "SELECT COUNT(*) subs, SUM(withdrawn) wd, SUM(desk_rejected) dr,"
            " SUM(decision IS NOT NULL AND withdrawn = 0) decided,"
            " SUM(decision IS NOT NULL AND withdrawn = 0 AND LOWER(decision) LIKE '%accept%') accepted,"
            " SUM(review_count > 0) reviewed, SUM(review_count) reviews"
            " FROM papers WHERE year = ?",
            (y,),
        ).fetchone()
        lengths = [
            len(row[0].split())
            for row in conn.execute(
                "SELECT content_text FROM messages WHERE year = ? AND kind = 'official_review'",
                (y,),
            )
            if row[0]
        ]
        stats[str(y)] = {
            "submissions": p["subs"],
            "withdrawn": p["wd"],
            "desk_rejected": p["dr"],
            "decided": p["decided"],
            "accept_rate": round(p["accepted"] / p["decided"], 4) if p["decided"] else None,
            "reviews": p["reviews"],
            "reviews_per_paper": round(p["reviews"] / p["reviewed"], 2) if p["reviewed"] else None,
            "median_review_words": int(statistics.median(lengths)) if lengths else None,
        }
    conn.close()
    OUT.write_text(json.dumps({"years": [str(y) for y in years], "stats": stats}) + "\n")
    print(f"{OUT} written")
    for y in years:
        s = stats[str(y)]
        print(y, s)


if __name__ == "__main__":
    main()
