"""The currents: full distributions per year, for the morphing-histogram figure.

Three quantities, 2018-2026:
  scores      — share of official-review ratings at each level (0-10 grid;
                years use different sub-scales, shown on a common axis)
  words       — official review length in words, binned
  units       — logic units per official reviewer per forum (Direct track), binned

Writes data/analysis/iclr/unit-taxonomy-2026-v1/currents-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

WORD_BINS = [(0, 150), (150, 300), (300, 450), (450, 600), (600, 800), (800, 1100), (1100, 1500), (1500, 10**9)]
WORD_LABELS = ["<150", "150", "300", "450", "600", "800", "1100", "1500+"]
UNIT_BINS = [(1, 3), (3, 5), (5, 7), (7, 9), (9, 12), (12, 16), (16, 22), (22, 10**9)]
UNIT_LABELS = ["1-2", "3-4", "5-6", "7-8", "9-11", "12-15", "16-21", "22+"]


def parse_rating(c: dict):
    for k in ("rating", "recommendation"):
        v = c.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            m = re.match(r"\s*(\d+)", v)
            if m:
                return float(m.group(1))
    return None


def main() -> None:
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    scores = defaultdict(lambda: defaultdict(int))
    words = defaultdict(lambda: [0] * len(WORD_BINS))
    for year, cj, txt in aconn.execute(
        "SELECT year, content_json, content_text FROM messages WHERE kind = 'official_review'"
    ):
        try:
            r = parse_rating(json.loads(cj))
        except json.JSONDecodeError:
            r = None
        if r is not None and 0 <= r <= 10:
            scores[year][int(r)] += 1
        if txt:
            w = txt.count(" ") + 1
            for bi, (lo, hi) in enumerate(WORD_BINS):
                if lo <= w < hi:
                    words[year][bi] += 1
                    break
    aconn.close()

    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    units = defaultdict(lambda: [0] * len(UNIT_BINS))
    for year, n in conn.execute(
        "SELECT year, COUNT(*) FROM units WHERE reviewer_role = 'official_reviewer'"
        " GROUP BY year, custom_id, reviewer_key"
    ):
        for bi, (lo, hi) in enumerate(UNIT_BINS):
            if lo <= n < hi:
                units[year][bi] += 1
                break
    conn.close()

    def norm(counts):
        tot = sum(counts)
        return [round(c / tot, 4) for c in counts] if tot else counts

    years = [y for y in range(2018, 2027) if sum(scores[y].values()) > 500]
    payload = {
        "years": years,
        "scores": {str(y): {"levels": sorted(scores[y]), "shares": norm([scores[y][l] for l in sorted(scores[y])])} for y in years},
        "words": {"labels": WORD_LABELS, "by_year": {str(y): norm(words[y]) for y in years}},
        "units": {"labels": UNIT_LABELS, "by_year": {str(y): norm(units[y]) for y in years}},
    }
    (V / "currents-data.json").write_text(json.dumps(payload) + "\n")
    for y in years:
        print(y, "score levels:", payload["scores"][str(y)]["levels"],
              "| words peak:", WORD_LABELS[max(range(8), key=lambda i: words[y][i])],
              "| units peak:", UNIT_LABELS[max(range(8), key=lambda i: units[y][i])])


if __name__ == "__main__":
    main()
