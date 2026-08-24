"""The interrogative: does an objection ASKED fare differently from one
ASSERTED, once the authors have answered?

The asked/asserted signal comes from the review's own form: a negative
unit anchored (by its cited line) in the [questions] field was posed as
a question; one anchored in [weaknesses] was asserted. ICLR 2026 only,
where line anchors exist. The outcome comes from the Direct forum-level
track, joined by forum + reviewer-signature suffix: did the same
reviewer produce a post-response unit on the same object, and did it
weaken/reverse (soften) or strengthen (harden)?

Writes data/analysis/iclr/unit-taxonomy-2026-v1/interrogative-data.json.
"""

from __future__ import annotations

import glob
import json
import re
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
OUT_DIR = PROJECT_ROOT / "data/analysis/iclr/review-logic-qwen-2026-full/outputs"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

REF = re.compile(r"^R-([A-Za-z0-9_-]+):L(\d{3,4})$")
HEADER = re.compile(r"^\[([a-z_ ]+)\]$")


def suffix(k: str) -> str:
    return k.split("_")[-1]


def main() -> None:
    # ---- review metadata: field spans + forum + reviewer suffix ----
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    meta = {}  # review_id -> (starts, names, forum, suffix)
    for rid, fid, sig, txt in ac.execute(
        "SELECT note_id, forum_id, signature, content_text FROM messages"
        " WHERE kind='official_review' AND year=2026"
    ):
        spans = []
        for i, line in enumerate((txt or "").splitlines(), 1):
            m = HEADER.match(line.strip())
            if m:
                spans.append((i, m.group(1)))
        if spans and sig:
            meta[rid] = ([s for s, _ in spans], [n for _, n in spans],
                         fid, suffix(sig.rsplit("/", 1)[-1]))
    ac.close()

    # ---- unit object labels (review-level track) ----
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    lab = {}
    for rid, uid, val, obj in uc.execute(
        "SELECT u.review_id, u.unit_id, u.valence, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        lab[(rid, uid)] = (obj, val)
    uc.close()

    # ---- pass over shards: classify each negative objection asked/asserted ----
    # (forum, suffix, obj) -> {"asked": bool, "asserted": bool}
    objections = defaultdict(lambda: [False, False])
    files = sorted(glob.glob(str(OUT_DIR / "*.jsonl")))
    for fi, fp in enumerate(files):
        if fi % 20000 == 0:
            print(f"  shard {fi}/{len(files)}")
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = d.get("review_id")
                m = meta.get(rid)
                if m is None:
                    continue
                starts, names, fid, sfx = m
                for u in d.get("logic_units") or []:
                    lb = lab.get((rid, u.get("unit_id")))
                    if lb is None or lb[1] != "negative":
                        continue
                    lines = [int(g.group(2)) for ref in (u.get("evidence_refs") or [])
                             if (g := REF.match(ref)) and g.group(1) == rid]
                    if not lines:
                        continue
                    fi_ = bisect_right(starts, min(lines)) - 1
                    if fi_ < 0:
                        continue
                    fname = names[fi_]
                    rec = objections[(fid, sfx, lb[0])]
                    if fname == "questions":
                        rec[0] = True
                    elif fname == "weaknesses":
                        rec[1] = True
    print(f"{len(objections):,} (forum, reviewer, object) objections anchored")

    # ---- outcomes from the Direct track ----
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    outcome = defaultdict(lambda: [0, 0, 0])  # key -> [revisit, soften, harden]
    for fid, rk, obj, chg in dc.execute(
        "SELECT u.forum_id, u.reviewer_key, l.object_key, u.judgment_change"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role='official_reviewer' AND u.year=2026"
        " AND u.temporal_position='post_author_response'"
    ):
        p = outcome[(fid, suffix(rk), obj)]
        p[0] = 1
        if chg in ("weakened", "reversed"):
            p[1] = 1
        elif chg == "strengthened":
            p[2] = 1
    dc.close()

    agg = defaultdict(lambda: {"asked": [0, 0, 0, 0], "asserted": [0, 0, 0, 0], "both": [0, 0, 0, 0]})
    n_matched = 0
    for key, (asked, asserted) in objections.items():
        cls = "both" if (asked and asserted) else ("asked" if asked else "asserted" if asserted else None)
        if cls is None:
            continue
        p = outcome.get(key)
        if p:
            n_matched += 1
        rev, soft, hard = (p if p else (0, 0, 0))
        obj = key[2]
        for scope in (obj, "__all__"):
            a = agg[scope][cls]
            a[0] += 1
            a[1] += rev
            a[2] += soft
            a[3] += hard

    def pack(v):
        return {
            k: {"n": x[0],
                "revisit": round(x[1] / x[0], 4) if x[0] else None,
                "soften": round(x[2] / x[0], 4) if x[0] else None,
                "harden": round(x[3] / x[0], 4) if x[0] else None}
            for k, x in v.items()
        }

    out = {"overall": pack(agg["__all__"]),
           "objects": {o: pack(v) for o, v in agg.items() if o != "__all__"}}
    (V / "interrogative-data.json").write_text(json.dumps(out))
    ov = out["overall"]
    for cls in ("asked", "asserted", "both"):
        c = ov[cls]
        print(f"{cls:>9s}: n={c['n']:>8,}  revisit {c['revisit']:.2%}  soften {c['soften']:.2%}  harden {c['harden']:.2%}")


if __name__ == "__main__":
    main()
