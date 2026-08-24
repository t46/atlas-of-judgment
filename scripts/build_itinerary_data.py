"""The itinerary: which field of the review each thought lives in, what
reviewers put first when they list faults, and what thought follows what.

The naive whole-text position washes out (every object's median ~0.50)
because the 2026 review form dominates: summary, then strengths, then
weaknesses, then questions. So the analysis respects the form:
  - field residency: object -> share of units anchored in each field
  - the bill of faults: within the weaknesses field only, where each
    object sits (0 = first line of weaknesses, 1 = last) and how often
    it is the FIRST fault listed, against its base rate (lead-off lift)
  - succession: object -> object transitions between consecutive units
    (unit_index order, form-independent)

Sources:
  review-logic-qwen-2026-full/outputs/*.jsonl — per-unit evidence_refs
    (R-<review_id>:L###); the pipeline's line numbering is exactly
    content_text.splitlines(), 1-indexed (validated 300/300)
  unit-taxonomy-2026-v1/units.sqlite3 — unit order, valence, object_key
  processed/iclr/analysis.sqlite3 — content_text with [field] header lines

Writes data/analysis/iclr/unit-taxonomy-2026-v1/itinerary-data.json.
"""

from __future__ import annotations

import glob
import json
import re
import sqlite3
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
OUT_DIR = PROJECT_ROOT / "data/analysis/iclr/review-logic-qwen-2026-full/outputs"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

NBINS = 12
REF = re.compile(r"^R-([A-Za-z0-9_-]+):L(\d{3,4})$")
HEADER = re.compile(r"^\[([a-z_ ]+)\]$")
KEEP_FIELDS = ("summary", "strengths", "weaknesses", "questions")


def field_spans(text: str):
    """[(start_line, field_name)] sorted; lines are 1-indexed."""
    spans = []
    for i, line in enumerate(text.splitlines(), 1):
        m = HEADER.match(line.strip())
        if m:
            spans.append((i, m.group(1)))
    return spans


def main() -> None:
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    reviews = {}  # rid -> (starts[], names[], nlines)
    for rid, txt in ac.execute(
        "SELECT note_id, content_text FROM messages WHERE kind='official_review' AND year=2026"
    ):
        txt = txt or ""
        spans = field_spans(txt)
        if spans:
            starts = [s for s, _ in spans]
            names = [n for _, n in spans]
            reviews[rid] = (starts, names, len(txt.splitlines() or [""]))
    ac.close()
    print(f"{len(reviews):,} reviews with field headers")

    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    units = {}
    for rid, uid, idx, val, obj in uc.execute(
        "SELECT u.review_id, u.unit_id, u.unit_index, u.valence, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        units[(rid, uid)] = (obj, val, idx)
    uc.close()

    field_res = defaultdict(Counter)          # obj -> field -> n
    wk_hist = defaultdict(lambda: [0] * NBINS)  # obj -> within-weaknesses hist
    wk_pos = defaultdict(list)
    val_wk = defaultdict(lambda: [0] * NBINS)
    lead = Counter()                          # obj -> times listed first in weaknesses
    wk_any = Counter()                        # obj -> weakness-anchored units (in eligible reviews)
    n_lead_reviews = 0
    trans = Counter()
    exp_ctrl = defaultdict(float)  # within-review-shuffle expected adjacent pairs
    mention_once = Counter()       # obj -> reviews mentioning it exactly once
    mention_any = Counter()        # obj -> reviews mentioning it at all
    obj_tot = Counter()
    n_units = n_anchored = 0

    files = sorted(glob.glob(str(OUT_DIR / "*.jsonl")))
    for fi, fp in enumerate(files):
        if fi % 15000 == 0:
            print(f"  shard {fi}/{len(files)}")
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = d.get("review_id")
                meta = reviews.get(rid)
                lus = d.get("logic_units") or []
                if not lus:
                    continue
                starts, names, nlines = meta if meta else (None, None, None)
                seq = []
                wk_units = []  # (line, obj)
                for u in lus:
                    um = units.get((rid, u.get("unit_id")))
                    if um is None:
                        continue
                    obj, val, idx = um
                    n_units += 1
                    obj_tot[obj] += 1
                    seq.append((idx, obj))
                    if meta is None:
                        continue
                    lines = [
                        int(m.group(2))
                        for ref in (u.get("evidence_refs") or [])
                        if (m := REF.match(ref)) and m.group(1) == rid
                    ]
                    if not lines:
                        continue
                    ln = min(lines)
                    fi_ = bisect_right(starts, ln) - 1
                    if fi_ < 0:
                        continue
                    fname = names[fi_]
                    fkey = fname if fname in KEEP_FIELDS else "other"
                    field_res[obj][fkey] += 1
                    n_anchored += 1
                    if fname == "weaknesses":
                        f_start = starts[fi_]
                        f_end = starts[fi_ + 1] - 1 if fi_ + 1 < len(starts) else nlines
                        span = max(1, f_end - f_start - 1)
                        p = min(1.0, max(0.0, (ln - f_start - 1) / span))
                        b = min(NBINS - 1, int(p * NBINS))
                        wk_hist[obj][b] += 1
                        wk_pos[obj].append(p)
                        val_wk[val][b] += 1
                        wk_units.append((ln, obj))
                if len(wk_units) >= 2:
                    n_lead_reviews += 1
                    wk_units.sort()
                    lead[wk_units[0][1]] += 1
                    for _, o in wk_units:
                        wk_any[o] += 1
                seq.sort()
                adj = 0
                for (i1, a), (i2, b) in zip(seq, seq[1:]):
                    if i2 == i1 + 1:
                        trans[(a, b)] += 1
                        adj += 1
                cnt = Counter(o for _, o in seq)
                for o_, c_ in cnt.items():
                    mention_any[o_] += 1
                    if c_ == 1:
                        mention_once[o_] += 1
                T = len(seq)
                if adj and T >= 2:
                    # null of Fig 4a: permute this review's labels across its positions
                    denom = T * (T - 1)
                    for a_, na in cnt.items():
                        for b_, nb in cnt.items():
                            pr = (na * (na - 1) if a_ == b_ else na * nb) / denom
                            if pr:
                                exp_ctrl[(a_, b_)] += adj * pr

    objs = [o for o, _ in obj_tot.most_common()]
    tot_wk = sum(wk_any.values())
    bill = {}
    for o in objs:
        h = wk_hist[o]
        n = sum(h)
        ps = sorted(wk_pos[o])
        base = wk_any[o] / tot_wk if tot_wk else 0
        lead_share = lead[o] / n_lead_reviews if n_lead_reviews else 0
        bill[o] = {
            "n": n,
            "median": round(ps[len(ps) // 2], 4) if ps else None,
            "hist": [round(x / n, 5) if n else 0 for x in h],
            "lead_share": round(lead_share, 4),
            "base_share": round(base, 4),
            "lift": round(lead_share / base, 3) if base else None,
        }
    bins_tot = [sum(val_wk[v][b] for v in val_wk) for b in range(NBINS)]
    valwk = {
        v: [round(val_wk[v][b] / bins_tot[b], 4) if bins_tot[b] else 0 for b in range(NBINS)]
        for v in val_wk
    }
    fields = {
        o: {f: field_res[o].get(f, 0) for f in (*KEEP_FIELDS, "other")} for o in objs
    }
    matrix = [[trans.get((a, b), 0) for b in objs] for a in objs]

    out = {
        "objects": objs,
        "fields": fields,
        "bill": bill,
        "valwk": valwk,
        "transitions": {"objects": objs, "matrix": matrix, "totals": [obj_tot[o] for o in objs]},
        "ctrl_lift": {
            "objects": objs,
            "lift": [
                [
                    round(trans.get((a, b), 0) / exp_ctrl[(a, b)], 3)
                    if exp_ctrl[(a, b)] >= 5 else None
                    for b in objs
                ]
                for a in objs
            ],
        },
        "mention": {
            o: {
                "reviews": mention_any[o],
                "once_share": round(mention_once[o] / mention_any[o], 4) if mention_any[o] else None,
            }
            for o in objs
        },
        "meta": {
            "n_units": n_units,
            "n_anchored": n_anchored,
            "n_lead_reviews": n_lead_reviews,
            "n_transitions": sum(trans.values()),
            "nbins": NBINS,
        },
    }
    path = V / "itinerary-data.json"
    path.write_text(json.dumps(out))
    print(f"{path} ({path.stat().st_size/1024:.0f} KB)")
    print(f"units {n_units:,} · anchored {n_anchored:,} · lead-eligible reviews {n_lead_reviews:,}")
    print(f"{'object':>28s} {'wk-med':>7s} {'lead':>6s} {'base':>6s} {'lift':>5s}")
    for o in objs:
        b = bill[o]
        print(f"{o:>28s} {str(b['median']):>7s} {b['lead_share']:>6.3f} {b['base_share']:>6.3f} {str(b['lift']):>5s}")


if __name__ == "__main__":
    main()
