"""Plate XVI rigor pass (declared 2026-08-23 BEFORE results; published either way).

Two threats to Fig 16a's reading, addressed:
1. Criticism-volume confound: reviews invoking the evidence rule in one
   docket may simply carry more criticism overall than in another. Fix:
   regress the within-paper deficit d on (n negative units, n units) across
   all stated-rule reviews, and re-express each evidence-rule cell as the
   mean of centered residuals (an adjusted tariff on the same scale).
2. Understated SEs: reviews of one paper share the paper. Fix: resample
   whole forums with replacement (B=400), recomputing the volume regression
   inside every replicate, for 95% CIs on the raw and adjusted
   framing-vs-clarity gap and ratio.

Prints results; does not modify lawtariff-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

import numpy as np

from build_lawtariff_data import A, D, MIN_N, load_laws_of

B = 400
SEED = 47


def main() -> None:
    laws_of, _names = load_laws_of()

    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    ratings = defaultdict(dict)
    for fid, sig, cj in ac.execute(
        "SELECT forum_id, signature, content_json FROM messages WHERE kind='official_review'"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, (int, float)):
            ratings[fid][sig.rsplit("/", 1)[-1]] = float(r)
    ac.close()

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    review_laws: dict[tuple, set] = defaultdict(set)
    year_of: dict[tuple, int] = {}
    pks = sorted(laws_of)
    CH = 900
    for i in range(0, len(pks), CH):
        chunk = pks[i:i + CH]
        qs = ",".join("?" * len(chunk))
        for pk, fid, rk, yr in dc.execute(
            f"SELECT unit_pk, forum_id, reviewer_key, year FROM units WHERE unit_pk IN ({qs})", chunk
        ):
            review_laws[(fid, rk)].update(laws_of[pk])
            year_of[(fid, rk)] = yr
    vol = {}
    for fid, rk, n, nn in dc.execute(
        "SELECT forum_id, reviewer_key, COUNT(*), SUM(valence='negative') FROM units"
        " WHERE temporal_position='initial_review' AND reviewer_role='official_reviewer'"
        " GROUP BY forum_id, reviewer_key"
    ):
        vol[(fid, rk)] = (float(n), float(nn or 0))
    dc.close()

    def find_rating(rmap, rk):
        if rk in rmap:
            return rmap[rk]
        if f"Reviewer_{rk}" in rmap:
            return rmap[f"Reviewer_{rk}"]
        hits = [v for t, v in rmap.items() if t.endswith(f"_{rk}")]
        return hits[0] if len(hits) == 1 else None

    year_ratings = defaultdict(list)
    for (fid, rk), _laws in review_laws.items():
        r = find_rating(ratings.get(fid, {}), rk)
        if r is not None:
            year_ratings[year_of[(fid, rk)]].append(r)
    year_std = {y: max(0.5, float(np.std(v))) for y, v in year_ratings.items() if len(v) > 50}

    # evidence-rule cell index per docket
    EV = {}
    reviews = []  # (forum, d, nneg, nu, [cell indices])
    for (fid, rk), laws in review_laws.items():
        rmap = ratings.get(fid, {})
        mine = find_rating(rmap, rk)
        if mine is None or len(rmap) < 2:
            continue
        yr = year_of[(fid, rk)]
        if yr not in year_std:
            continue
        others = [v for t, v in rmap.items()
                  if not (t == rk or t == f"Reviewer_{rk}" or t.endswith(f"_{rk}"))]
        if not others:
            continue
        d = (mine - float(np.mean(others))) / year_std[yr]
        nu, nn = vol.get((fid, rk), (np.nan, np.nan))
        if not np.isfinite(nu):
            continue
        cells = []
        for (obj, key) in laws:
            if key == "evidence_imported" or obj == "novelty":
                cid = (obj, key)
                if cid not in EV:
                    EV[cid] = len(EV)
                cells.append(EV[cid])
        reviews.append((fid, d, nn, nu, cells))
    print(f"{len(reviews):,} stated-rule reviews with rating, co-reviews and volume counts")

    forums = sorted({r[0] for r in reviews})
    fidx = {f: i, } if False else {f: i for i, f in enumerate(forums)}
    F, C = len(forums), len(EV)
    # per-forum sufficient statistics
    P = np.zeros((F, 9))  # n, Sd, S1, S2, S11, S22, S12, S1d, S2d  (1=nneg, 2=nu)
    CN = np.zeros((F, C)); CD = np.zeros((F, C)); C1 = np.zeros((F, C)); C2 = np.zeros((F, C))
    for f, d, nn, nu, cells in reviews:
        i = fidx[f]
        P[i] += [1, d, nn, nu, nn * nn, nu * nu, nn * nu, nn * d, nu * d]
        for c in cells:
            CN[i, c] += 1; CD[i, c] += d; C1[i, c] += nn; C2[i, c] += nu

    def estimates(w):
        """w = forum multiplicities; returns raw and adjusted cell means."""
        p = w @ P
        n, Sd, S1, S2, S11, S22, S12, S1d, S2d = p
        m1, m2, md = S1 / n, S2 / n, Sd / n
        Sxx = np.array([[S11 - n * m1 * m1, S12 - n * m1 * m2],
                        [S12 - n * m1 * m2, S22 - n * m2 * m2]])
        Sxy = np.array([S1d - n * m1 * md, S2d - n * m2 * md])
        b1, b2 = np.linalg.solve(Sxx, Sxy)
        cn = w @ CN
        raw = (w @ CD) / cn
        adj = raw - b1 * ((w @ C1) / cn - m1) - b2 * ((w @ C2) / cn - m2)
        return raw, adj, (b1, b2)

    w0 = np.ones(F)
    raw0, adj0, (b1, b2) = estimates(w0)
    cn0 = w0 @ CN
    print(f"volume slopes: per negative unit {b1:+.4f}σ · per unit {b2:+.4f}σ")
    print(f"{'cell':>38s} {'raw':>7s} {'adj':>7s} {'n':>6s}")
    order = np.argsort(adj0)
    inv = {v: k for k, v in EV.items()}
    for c in order:
        if cn0[c] >= MIN_N:
            print(f"{str(inv[c]):>38s} {raw0[c]:+.3f} {adj0[c]:+.3f} {int(cn0[c]):6d}")

    # merge adjusted tariffs into lawtariff-data.json
    from build_lawtariff_data import V
    data = json.loads((V / "lawtariff-data.json").read_text())
    for (obj, key), c in EV.items():
        if cn0[c] < MIN_N:
            continue
        for L in data["dockets"].get(obj, []):
            if L["key"] == key:
                L["adj"] = round(float(adj0[c]), 4)
    data["volume_slopes"] = {"per_neg_unit": round(float(b1), 4), "per_unit": round(float(b2), 4)}

    fr, cl = EV.get(("problem_framing", "evidence_imported")), EV.get(("clarity", "evidence_imported"))
    rng = np.random.default_rng(SEED)
    stats = []
    for _ in range(B):
        idx = rng.integers(0, F, size=F)
        w = np.bincount(idx, minlength=F).astype(float)
        r, a, _ = estimates(w)
        stats.append((r[fr] - r[cl], a[fr] - a[cl], r[fr] / r[cl], a[fr] / a[cl]))
    st = np.array(stats)
    lab = ["raw gap  ", "adj gap  ", "raw ratio", "adj ratio"]
    full = [raw0[fr] - raw0[cl], adj0[fr] - adj0[cl], raw0[fr] / raw0[cl], adj0[fr] / adj0[cl]]
    for k in range(4):
        lo, hi = np.percentile(st[:, k], [2.5, 97.5])
        print(f"framing vs clarity {lab[k]} {full[k]:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]")
    glo, ghi = np.percentile(st[:, 1], [2.5, 97.5])
    data["evidence_check"] = {
        "adj_gap": round(float(full[1]), 3),
        "adj_gap_ci": [round(float(glo), 3), round(float(ghi), 3)],
        "raw_gap": round(float(full[0]), 3),
        "B": B,
    }
    (V / "lawtariff-data.json").write_text(json.dumps(data))
    print("merged adj/evidence_check into lawtariff-data.json")


if __name__ == "__main__":
    main()
