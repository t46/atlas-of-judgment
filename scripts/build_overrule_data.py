"""Overruled: when the decision disagrees with the panel's lean (ICLR 2026).

For every decided, non-withdrawn 2026 paper with >= 3 rated official reviews:
the panel lean is the mean rating; the empirical accept threshold is the mean-
rating quantile matching the venue's accept rate. A paper is "overruled" when
its decision falls on the opposite side of its panel lean by a clear margin
(mean >= thr + 0.5 rejected, or mean <= thr - 0.5 accepted). Also: agreement
as a function of distance from the threshold, and public exemplars.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/overrule-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def main() -> None:
    conn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    ratings: dict[str, list[float]] = defaultdict(list)
    for forum_id, cj in conn.execute(
        "SELECT forum_id, content_json FROM messages WHERE year = 2026 AND kind = 'official_review'"
    ):
        try:
            v = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(v, (int, float)):
            ratings[forum_id].append(float(v))

    papers = {}
    for forum_id, title, decision, withdrawn in conn.execute(
        "SELECT forum_id, title, decision, withdrawn FROM papers WHERE year = 2026"
    ):
        if not decision or withdrawn:
            continue
        rs = ratings.get(forum_id)
        if not rs or len(rs) < 3:
            continue
        acc = any(w in decision.lower() for w in ("accept", "oral", "poster", "spotlight"))
        papers[forum_id] = {"title": title, "mean": sum(rs) / len(rs), "n": len(rs),
                            "accepted": acc, "decision": decision}
    conn.close()

    means = sorted(p["mean"] for p in papers.values())
    acc_rate = sum(1 for p in papers.values() if p["accepted"]) / len(papers)
    thr = means[int(len(means) * (1 - acc_rate))]
    print(f"{len(papers)} decided papers, accept rate {acc_rate:.1%}, empirical threshold mean={thr:.2f}")

    over_rej = [(f, p) for f, p in papers.items() if p["mean"] >= thr + 0.5 and not p["accepted"]]
    over_acc = [(f, p) for f, p in papers.items() if p["mean"] <= thr - 0.5 and p["accepted"]]
    margins = []
    for mg in (0.25, 0.5, 0.75, 1.0):
        rej = sum(1 for p in papers.values() if p["mean"] >= thr + mg and not p["accepted"])
        acc = sum(1 for p in papers.values() if p["mean"] <= thr - mg and p["accepted"])
        margins.append({"margin": mg, "lifted": acc, "dropped": rej})
    print("margins:", margins)

    # agreement vs distance from threshold
    curve = []
    for lo in [x * 0.5 for x in range(0, 7)]:
        hi = lo + 0.5
        band = [p for p in papers.values() if lo <= abs(p["mean"] - thr) < hi]
        if len(band) < 50:
            continue
        agree = sum(1 for p in band if (p["mean"] >= thr) == p["accepted"]) / len(band)
        curve.append({"band": f"{lo:.1f}-{hi:.1f}", "n": len(band), "agree": round(agree, 4)})
        print(f"  |mean-thr| in [{lo},{hi}): n={len(band):5d} decision-matches-lean {agree:.1%}")

    def exemplars(lst, keyfun):
        return [{"forum": f, "title": p["title"][:110], "mean": round(p["mean"], 2),
                 "n": p["n"], "decision": p["decision"]}
                for f, p in sorted(lst, key=keyfun)[:8]]

    payload = {
        "n_papers": len(papers), "accept_rate": round(acc_rate, 4), "threshold": round(thr, 3),
        "n_overruled_rejected": len(over_rej), "n_overruled_accepted": len(over_acc),
        "margins": margins,
        "curve": curve,
        "ex_rejected_high": exemplars(over_rej, lambda kv: -kv[1]["mean"]),
        "ex_accepted_low": exemplars(over_acc, lambda kv: kv[1]["mean"]),
    }
    (V / "overrule-data.json").write_text(json.dumps(payload) + "\n")
    print(f"overruled: {len(over_rej)} rejected-above-threshold, {len(over_acc)} accepted-below")


if __name__ == "__main__":
    main()
