#!/usr/bin/env python3
"""Addendum E+F to the Watermark plate (plan: notes/llm-era-analysis-plan.md).

E: concentration of the 12-object / 12-standard / 144-cell category mix
   per year, plus per-reviewer attention entropy.
F: dispersion and within-forum convergence of the reasoning component at
   the sub-unit grain, over the precomputed bge-small embeddings
   (row i = unit_pk i+1, float16, L2-normalized at build time).

Appends a "mix" section to llmtrace-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DIRECT = ROOT / "data" / "analysis" / "iclr" / "unit-taxonomy-direct-v1"
OUT = ROOT / "data" / "analysis" / "iclr" / "unit-taxonomy-2026-v1" / "llmtrace-data.json"
YEARS = list(range(2018, 2027))
RNG = np.random.default_rng(20260827)


def norm_entropy(counts: np.ndarray, k: int) -> float:
    p = counts / counts.sum()
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum() / np.log2(k))


def hhi(counts: np.ndarray) -> float:
    p = counts / counts.sum()
    return float((p ** 2).sum())


def main() -> None:
    con = sqlite3.connect(f"file:{DIRECT / 'units.sqlite3'}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        "SELECT u.year, u.forum_id, u.reviewer_key, u.unit_pk, l.object_key, l.reasoning_key "
        "FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk "
        "WHERE u.reviewer_role = 'official_reviewer'"
    ).fetchall()
    con.close()
    print(f"{len(rows)} official_reviewer units")

    by_year: dict[int, list] = defaultdict(list)
    for r in rows:
        by_year[r[0]].append(r)

    objs = sorted({r[4] for r in rows})
    reas = sorted({r[5] for r in rows})
    oi = {o: i for i, o in enumerate(objs)}
    ri = {s: i for i, s in enumerate(reas)}

    # ---------------- E: mix concentration ----------------
    mix = {}
    for y in YEARS:
        yr = by_year[y]
        oc = np.zeros(len(objs))
        rc = np.zeros(len(reas))
        jc = np.zeros(len(objs) * len(reas))
        per_rev: dict[tuple, Counter] = defaultdict(Counter)
        for _, forum, rk, _, ok, sk in yr:
            oc[oi[ok]] += 1
            rc[ri[sk]] += 1
            jc[oi[ok] * len(reas) + ri[sk]] += 1
            per_rev[(forum, rk)][ok] += 1
        rev_H = []
        for cnt in per_rev.values():
            n = sum(cnt.values())
            if n >= 5:
                arr = np.array(list(cnt.values()), float)
                ceil = np.log2(min(12, n))
                p = arr / n
                rev_H.append(float(-(p * np.log2(p)).sum() / ceil))
        mix[str(y)] = {
            "n_units": len(yr),
            "object_entropy": round(norm_entropy(oc, 12), 4),
            "object_hhi": round(hhi(oc), 4),
            "standard_entropy": round(norm_entropy(rc, 12), 4),
            "joint_entropy": round(norm_entropy(jc, 144), 4),
            "reviewer_attention_entropy_mean": round(float(np.mean(rev_H)), 4),
            "n_reviewers_ge5": len(rev_H),
        }
        print(y, mix[str(y)])

    # ---------------- F: reasoning-embedding dispersion & convergence ----------------
    emb = np.load(DIRECT / "reasoning-embeddings.npy", mmap_mode="r")
    print("emb", emb.shape, emb.dtype)

    disp = {}
    within = {}
    cross = {}
    for y in YEARS:
        yr = by_year[y]
        pks = np.array([r[3] for r in yr])
        # dispersion: mean cosine distance to the year centroid, 20k sample
        sample = RNG.choice(pks, size=min(20_000, len(pks)), replace=False)
        E = np.asarray(emb[sample - 1], dtype=np.float32)
        E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
        c = E.mean(axis=0)
        c /= np.linalg.norm(c) + 1e-9
        disp[str(y)] = {
            "mean_dist_to_centroid": round(float(1 - (E @ c).mean()), 4),
            "n_sample": int(len(sample)),
        }

        # within-forum cross-reviewer pairs (≤20 per forum)
        by_forum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for _, forum, rk, pk, _, _ in yr:
            by_forum[forum][rk].append(pk)
        sims = []
        for forum, revd in by_forum.items():
            rlist = list(revd.items())
            if len(rlist) < 2:
                continue
            pairs = []
            for i in range(len(rlist)):
                for j in range(i + 1, len(rlist)):
                    for a in rlist[i][1]:
                        for b in rlist[j][1]:
                            pairs.append((a, b))
            if len(pairs) > 20:
                idx = RNG.choice(len(pairs), 20, replace=False)
                pairs = [pairs[k] for k in idx]
            A = np.asarray(emb[np.array([p[0] for p in pairs]) - 1], dtype=np.float32)
            B = np.asarray(emb[np.array([p[1] for p in pairs]) - 1], dtype=np.float32)
            A /= np.linalg.norm(A, axis=1, keepdims=True) + 1e-9
            B /= np.linalg.norm(B, axis=1, keepdims=True) + 1e-9
            sims.append(float((A * B).sum(axis=1).mean()))
        sims = np.array(sims)
        within[str(y)] = {
            "median": round(float(np.median(sims)), 4),
            "q25": round(float(np.percentile(sims, 25)), 4),
            "q75": round(float(np.percentile(sims, 75)), 4),
            "n_forums": int(len(sims)),
        }

        # cross-forum baseline: 50k random same-year unit pairs, different forums
        forums_arr = np.array([r[1] for r in yr])
        a = RNG.integers(0, len(yr), 80_000)
        b = RNG.integers(0, len(yr), 80_000)
        m = (a != b) & (forums_arr[a] != forums_arr[b])
        a, b = a[m][:50_000], b[m][:50_000]
        A = np.asarray(emb[pks[a] - 1], dtype=np.float32)
        B = np.asarray(emb[pks[b] - 1], dtype=np.float32)
        A /= np.linalg.norm(A, axis=1, keepdims=True) + 1e-9
        B /= np.linalg.norm(B, axis=1, keepdims=True) + 1e-9
        cs = (A * B).sum(axis=1)
        cross[str(y)] = {"median": round(float(np.median(cs)), 4), "n_pairs": int(len(cs))}
        print(y, "disp", disp[str(y)]["mean_dist_to_centroid"],
              "within", within[str(y)]["median"], "cross", cross[str(y)]["median"])

    # ---------------- size-matched null for the co-reviewer object Jaccard ----------------
    # Reviewers produce more objects per review in later years (mean set size
    # 3.38 -> 3.82), which raises Jaccard mechanically. Null: same forums, same
    # set sizes, contents drawn without replacement weighted by the year's mix.
    jaccard_null = {}
    for y in YEARS:
        yr = by_year[y]
        freq = Counter(r[4] for r in yr)
        objs_y = sorted(freq)
        p = np.array([freq[o] for o in objs_y], float)
        p /= p.sum()
        K = len(objs_y)
        by_forum2: dict[str, dict[str, set]] = defaultdict(dict)
        for _, forum, rk, _, ok, _ in yr:
            by_forum2[forum].setdefault(rk, set()).add(ok)
        obs = []
        forum_sizes = []
        set_sizes = []
        for revd in by_forum2.values():
            sets_ = [s for s in revd.values() if s]
            if len(sets_) < 2:
                continue
            pj = [len(a & b) / len(a | b)
                  for i, a in enumerate(sets_) for b in sets_[i + 1:]]
            obs.append(float(np.mean(pj)))
            forum_sizes.append([len(s) for s in sets_])
            set_sizes.extend(len(s) for s in sets_)
        null_meds = []
        for _ in range(20):
            nl = []
            for fs in forum_sizes:
                sets_ = [set(RNG.choice(K, size=min(s, K), replace=False, p=p))
                         for s in fs]
                pj = [len(a & b) / len(a | b)
                      for i, a in enumerate(sets_) for b in sets_[i + 1:]]
                nl.append(float(np.mean(pj)))
            null_meds.append(float(np.median(nl)))
        jaccard_null[str(y)] = {
            "observed_median": round(float(np.median(obs)), 4),
            "null_median": round(float(np.mean(null_meds)), 4),
            "excess": round(float(np.median(obs)) - float(np.mean(null_meds)), 4),
            "mean_set_size": round(float(np.mean(set_sizes)), 2),
        }
        print(y, "jacnull", jaccard_null[str(y)])

    d = json.loads(OUT.read_text())
    d["mix"] = {
        "plan": "notes/llm-era-analysis-plan.md addendum E+F (frozen before computation)",
        "concentration": mix,
        "reasoning_dispersion": disp,
        "reasoning_within_forum": within,
        "reasoning_cross_forum": cross,
        "jaccard_size_null": jaccard_null,
    }
    OUT.write_text(json.dumps(d, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
