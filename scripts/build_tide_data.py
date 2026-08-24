"""The tide: does when a review is filed travel with what it is like?

No strong hypothesis, deliberately: bin official reviews by how many
days before the year's filing peak (the de-facto deadline) they were
first posted, and report per bin, with bootstrap CIs: mean rating,
median word count, mean units of logic, and share filed on the peak
day itself. Run for 2026 and replicated on 2025; only patterns that
hold in both years deserve prose.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/tide-data.json.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

rng = np.random.default_rng(46)
BINS = [(-9999, -14, "≥2 weeks early"), (-13, -7, "1–2 weeks early"), (-6, -3, "3–6 days early"),
        (-2, -1, "1–2 days early"), (0, 0, "deadline day"), (1, 9999, "after the deadline")]


def parse_rating(cj):
    try:
        v = json.loads(cj).get("rating")
    except json.JSONDecodeError:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"\s*(\d+)", v)
        return float(m.group(1)) if m else None
    return None


def year_tide(year, units_per_review):
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    rows = []
    for nid, cj, txt, cdate in ac.execute(
        "SELECT note_id, content_json, content_text, cdate FROM messages"
        " WHERE kind='official_review' AND year=?", (year,)
    ):
        r = parse_rating(cj)
        if r is None or not cdate:
            continue
        day = dt.datetime.utcfromtimestamp(cdate / 1000).date()
        words = (txt or "").count(" ") + 1
        rows.append((nid, day, r, words))
    ac.close()
    days = Counter(d for _, d, _, _ in rows)
    peak = max(days, key=days.get)
    out_bins = []
    for lo, hi, label in BINS:
        sel = [(nid, r, w) for nid, d, r, w in rows if lo <= (d - peak).days <= hi]
        if len(sel) < 100:
            continue
        rs = np.array([r for _, r, _ in sel])
        ws = np.array([w for _, _, w in sel])
        us = np.array([units_per_review[nid] for nid, _, _ in sel if nid in units_per_review])
        boot = [rng.choice(rs, len(rs)).mean() for _ in range(400)]
        out_bins.append({
            "label": label, "n": len(sel),
            "rating": round(float(rs.mean()), 3),
            "rating_lo": round(float(np.percentile(boot, 2.5)), 3),
            "rating_hi": round(float(np.percentile(boot, 97.5)), 3),
            "words": int(np.median(ws)),
            "units": round(float(us.mean()), 2) if len(us) else None,
        })
    return {"peak": str(peak), "n": len(rows), "bins": out_bins}


def main() -> None:
    # units per review: 2026 from the review-level track; 2025 from Direct
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    upr26 = dict(uc.execute("SELECT review_id, COUNT(*) FROM units GROUP BY review_id"))
    uc.close()
    out = {"2026": year_tide(2026, upr26), "2025": year_tide(2025, {})}
    (V / "tide-data.json").write_text(json.dumps(out))
    for y in ("2026", "2025"):
        d = out[y]
        print(f"{y} (peak {d['peak']}, n={d['n']:,}):")
        for b in d["bins"]:
            print(f"  {b['label']:>18s}  n={b['n']:>6,}  rating {b['rating']} [{b['rating_lo']},{b['rating_hi']}]"
                  f"  words {b['words']}  units {b['units']}")


if __name__ == "__main__":
    main()
