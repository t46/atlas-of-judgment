"""The season: daily event counts for the ICLR 2026 review cycle.

From message cdate timestamps (ms): daily counts of official reviews,
discussion comments (official_comment), meta reviews, withdrawals, and
decisions; submission days from the raw forum records. Trimmed to the
active window (first submission day .. last decision day + 3).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/season-data.json.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
RAW_DB = PROJECT_ROOT / "data/raw/iclr/openreview.sqlite3"

KINDS = {
    "official_review": "reviews",
    "official_comment": "discussion",
    "meta_review": "metas",
    "withdrawal": "withdrawals",
    "decision": "decisions",
}


def day(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def main() -> None:
    series: dict[str, Counter] = {v: Counter() for v in KINDS.values()}
    series["submissions"] = Counter()

    conn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    for kind, cdate in conn.execute(
        "SELECT kind, cdate FROM messages WHERE year = 2026 AND cdate IS NOT NULL"
    ):
        if kind in KINDS:
            series[KINDS[kind]][day(cdate)] += 1
    conn.close()

    raw = sqlite3.connect(f"file:{RAW_DB}?mode=ro", uri=True)
    for (sj,) in raw.execute("SELECT submission_json FROM forums WHERE year = 2026"):
        try:
            c = json.loads(sj)
            ms = c.get("cdate") or c.get("tcdate")
            if ms:
                series["submissions"][day(ms)] += 1
        except json.JSONDecodeError:
            continue
    raw.close()

    all_days = sorted(set().union(*[set(c) for c in series.values()]))
    # trim to the dense window: drop days before submissions begin in earnest
    start = next(d for d in all_days if series["submissions"][d] >= 5 or series["reviews"][d] >= 5)
    end = max(d for d in all_days if series["decisions"][d] >= 5 or series["withdrawals"][d] >= 5)
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end) + dt.timedelta(days=3)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    payload = {
        "days": days,
        "series": {k: [series[k][d] for d in days] for k in series},
        "totals": {k: sum(series[k].values()) for k in series},
    }
    (V / "season-data.json").write_text(json.dumps(payload) + "\n")
    print(f"{days[0]} .. {days[-1]} ({len(days)} days)")
    for k, tot in payload["totals"].items():
        peak_i = max(range(len(days)), key=lambda i: payload["series"][k][i])
        print(f"  {k:12s} total={tot:7d} peak={payload['series'][k][peak_i]:6d} on {days[peak_i]}")


if __name__ == "__main__":
    main()
