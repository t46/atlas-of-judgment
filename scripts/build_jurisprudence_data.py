"""The jurisprudence: what each objection costs, how each verdict is
pronounced, and whether the choice of standard is where judgment forks.

Three instruments:

A. THE TARIFF (2026 review-level track, 75,859 rated reviews)
   Within-paper design: demean rating and regressors inside each forum,
   then OLS of rating on 12 "raised a negative unit on object O"
   indicators plus criticism volume (n negative units) and review size
   (n units). The paper itself — and anything constant within it — drops
   out. Coefficients read: holding the paper and the volume of criticism
   fixed, a review that raises objection O sits beta_O points lower.
   Cluster bootstrap (resample forums, 200 reps) for intervals.

B. THE SENTENCE (2026 track, 294,971 negative units)
   Per object: how often the fault comes with a prescribed repair
   (suggested_improvement present), and how often it is pronounced with
   high confidence.

C. SAME BENCH, DIFFERENT LAW (Direct 2018-2026 track, initial reviews)
   For every forum x object inspected by >=2 reviewers: each reviewer's
   stance (majority-negative or not) and dominant reasoning standard.
   Pairs of reviewers on the same object of the same paper: do they
   agree more when they applied the same standard?

Writes data/analysis/iclr/unit-taxonomy-2026-v1/jurisprudence-data.json.
"""

from __future__ import annotations

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


def ratings_2026() -> dict[str, float]:
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    out = {}
    for rid, cj in ac.execute(
        "SELECT note_id, content_json FROM messages WHERE kind='official_review' AND year=2026"
    ):
        try:
            v = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(v, (int, float)):
            out[rid] = float(v)
        elif isinstance(v, str):
            m = re.match(r"\s*(\d+)", v)
            if m:
                out[rid] = float(m.group(1))
    ac.close()
    return out


def tariff_and_sentence():
    ratings = ratings_2026()
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    per_review = {}  # rid -> [paper, n_units, n_neg, set(neg objects)]
    sentence = defaultdict(lambda: [0, 0, 0])  # obj -> [n_neg, with_repair, high_conf]
    for rid, pid, obj, val, sug, conf in uc.execute(
        "SELECT u.review_id, u.paper_id, l.object_key, u.valence, u.suggested_improvement, u.confidence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        r = per_review.setdefault(rid, [pid, 0, 0, set()])
        r[1] += 1
        if val == "negative":
            r[2] += 1
            r[3].add(obj)
            s = sentence[obj]
            s[0] += 1
            if sug and sug.strip().lower() not in ("", "none"):
                s[1] += 1
            if conf == "high":
                s[2] += 1
    uc.close()

    objs = sorted(sentence, key=lambda o: -sentence[o][0])
    # keep forums with >=2 rated reviews
    by_forum = defaultdict(list)
    for rid, (pid, nu, nn, negset) in per_review.items():
        if rid in ratings:
            by_forum[pid].append((rid, nu, nn, negset))
    forums = [f for f, rows in by_forum.items() if len(rows) >= 2]
    fidx = {f: i for i, f in enumerate(forums)}
    rows = [(f, *r) for f in forums for r in by_forum[f]]
    n = len(rows)
    k = len(objs)
    X = np.zeros((n, k + 2))
    y = np.zeros(n)
    fvec = np.zeros(n, dtype=np.int64)
    n_with = Counter()
    for i, (f, rid, nu, nn, negset) in enumerate(rows):
        y[i] = ratings[rid]
        fvec[i] = fidx[f]
        for j, o in enumerate(objs):
            if o in negset:
                X[i, j] = 1.0
                n_with[o] += 1
        X[i, k] = nn
        X[i, k + 1] = nu

    # naive between-review deltas (no controls)
    naive = {}
    for j, o in enumerate(objs):
        m1 = y[X[:, j] == 1].mean()
        m0 = y[X[:, j] == 0].mean()
        naive[o] = round(m1 - m0, 3)

    # demean within forum
    nf = len(forums)
    cnt = np.bincount(fvec, minlength=nf).astype(float)

    def demean(A):
        if A.ndim == 1:
            s = np.bincount(fvec, weights=A, minlength=nf)
            return A - (s / cnt)[fvec]
        out = np.empty_like(A)
        for c in range(A.shape[1]):
            out[:, c] = demean(A[:, c])
        return out

    Xd, yd = demean(X), demean(y)

    def ols(Xa, ya):
        beta, *_ = np.linalg.lstsq(Xa, ya, rcond=None)
        return beta

    beta = ols(Xd, yd)

    # cluster bootstrap over forums
    B = 200
    boots = np.zeros((B, k + 2))
    forum_rows = defaultdict(list)
    for i in range(n):
        forum_rows[fvec[i]].append(i)
    forum_rows = {f: np.array(ix) for f, ix in forum_rows.items()}
    for b in range(B):
        pick = rng.integers(0, nf, nf)
        ix = np.concatenate([forum_rows[f] for f in pick])
        boots[b] = ols(Xd[ix], yd[ix])
    lo = np.percentile(boots, 2.5, axis=0)
    hi = np.percentile(boots, 97.5, axis=0)

    tariff = {
        o: {
            "coef": round(float(beta[j]), 3),
            "lo": round(float(lo[j]), 3),
            "hi": round(float(hi[j]), 3),
            "naive": naive[o],
            "n_with": n_with[o],
        }
        for j, o in enumerate(objs)
    }
    tariff_meta = {
        "n_reviews": n,
        "n_forums": nf,
        "coef_n_neg": round(float(beta[k]), 3),
        "coef_n_units": round(float(beta[k + 1]), 3),
    }
    sent = {
        o: {
            "n": sentence[o][0],
            "repair": round(sentence[o][1] / sentence[o][0], 4),
            "certain": round(sentence[o][2] / sentence[o][0], 4),
        }
        for o in objs
    }
    return objs, tariff, tariff_meta, sent


def same_bench():
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    # (year, forum, object, reviewer) -> [n_neg, n_all, Counter(standards)]
    cell = {}
    for yr, fid, rk, obj, rea, val in dc.execute(
        "SELECT u.year, u.forum_id, u.reviewer_key, l.object_key, l.reasoning_key, u.valence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'"
    ):
        c = cell.setdefault((yr, fid, obj, rk), [0, 0, Counter()])
        c[1] += 1
        if val == "negative":
            c[0] += 1
        c[2][rea] += 1
    dc.close()

    groups = defaultdict(list)  # (yr, fid, obj) -> [(stance, dom_std, set_std)]
    for (yr, fid, obj, rk), (nn, na, stds) in cell.items():
        stance = 1 if nn * 2 > na else 0
        dom = stds.most_common(1)[0][0]
        groups[(yr, fid, obj)].append((stance, dom, frozenset(stds)))

    overall = {"same": [0, 0], "diff": [0, 0]}       # dominant-standard match
    overlap = {"same": [0, 0], "diff": [0, 0]}       # any shared standard
    by_object = defaultdict(lambda: {"same": [0, 0], "diff": [0, 0]})
    for (yr, fid, obj), members in groups.items():
        if len(members) < 2:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                s1, d1, f1 = members[a]
                s2, d2, f2 = members[b]
                agree = 1 if s1 == s2 else 0
                key = "same" if d1 == d2 else "diff"
                overall[key][0] += 1
                overall[key][1] += agree
                by_object[obj][key][0] += 1
                by_object[obj][key][1] += agree
                key2 = "same" if f1 & f2 else "diff"
                overlap[key2][0] += 1
                overlap[key2][1] += agree

    def pack(d):
        return {
            k: {"n": v[0], "agree": round(v[1] / v[0], 4) if v[0] else None}
            for k, v in d.items()
        }

    return {
        "dominant": pack(overall),
        "overlap": pack(overlap),
        "by_object": {o: pack(v) for o, v in by_object.items()},
    }


def main() -> None:
    objs, tariff, tmeta, sent = tariff_and_sentence()
    law = same_bench()
    out = {"objects": objs, "tariff": tariff, "tariff_meta": tmeta, "sentence": sent, "law": law}
    path = V / "jurisprudence-data.json"
    path.write_text(json.dumps(out))
    print(f"{path} ({path.stat().st_size/1024:.0f} KB)")
    print(f"tariff over {tmeta['n_reviews']:,} reviews in {tmeta['n_forums']:,} forums;"
          f" per-negative-unit slope {tmeta['coef_n_neg']}")
    print(f"{'object':>26s} {'tariff':>7s} {'95% CI':>16s} {'naive':>7s} {'repair':>7s} {'certain':>8s}")
    for o in objs:
        t, s = tariff[o], sent[o]
        print(f"{o:>26s} {t['coef']:>7.3f} [{t['lo']:>6.3f},{t['hi']:>6.3f}] {t['naive']:>7.3f} {s['repair']:>7.1%} {s['certain']:>8.1%}")
    d = law["dominant"]
    print(f"law (dominant std): same {d['same']['agree']} (n={d['same']['n']:,}) vs diff {d['diff']['agree']} (n={d['diff']['n']:,})")
    o = law["overlap"]
    print(f"law (any overlap):  same {o['same']['agree']} (n={o['same']['n']:,}) vs diff {o['diff']['agree']} (n={o['diff']['n']:,})")


if __name__ == "__main__":
    main()
