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
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DIRECT = ROOT / "data" / "analysis" / "iclr" / "unit-taxonomy-direct-v1"
CORPUS = ROOT / "data" / "processed" / "iclr" / "analysis.sqlite3"
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


def attention_null(by_year: dict[int, list]) -> dict:
    """Addendum H: n-matched null for the per-reviewer attention entropy.

    Same reviewers, same unit counts n, units drawn iid (with replacement)
    from the year's own object mix. If the null rises like the observed
    series, the "reviewer spread widened" reading is mechanical (n-driven);
    only the observed-minus-null excess is interpretable. Fresh RNG so the
    numbers are reproducible independently of the sections above.
    """
    rng = np.random.default_rng(20260827)

    def norm_h(counts: np.ndarray) -> float:
        n = counts.sum()
        p = counts[counts > 0] / n
        return float(-(p * np.log2(p)).sum() / np.log2(min(12, n)))

    out = {}
    for y in YEARS:
        yr = by_year[y]
        objs = sorted({r[4] for r in yr})
        mixc = Counter(r[4] for r in yr)
        p = np.array([mixc[o] for o in objs], float)
        p /= p.sum()
        per_rev: dict[tuple, Counter] = defaultdict(Counter)
        for _, forum, rk, _, ok, _ in yr:
            per_rev[(forum, rk)][ok] += 1
        ns, obs = [], []
        for cnt in per_rev.values():
            n = sum(cnt.values())
            if n >= 5:
                ns.append(n)
                obs.append(norm_h(np.array(list(cnt.values()), float)))
        obs_mean = float(np.mean(obs))
        null_means = []
        for _ in range(5):
            vals = []
            for n in ns:
                draw = rng.choice(len(objs), size=n, replace=True, p=p)
                c = np.bincount(draw, minlength=len(objs)).astype(float)
                vals.append(norm_h(c))
            null_means.append(float(np.mean(vals)))
        null_mean = float(np.mean(null_means))
        out[str(y)] = {
            "observed": round(obs_mean, 4),
            "null_mean": round(null_mean, 4),
            "excess": round(obs_mean - null_mean, 4),
            "mean_n": round(float(np.mean(ns)), 2),
        }
        print(y, "attnull", out[str(y)])
    return out


HEADER = re.compile(r"^\[([a-z0-9_,:. ]+)\]$")


def _free_text(ct: str, keep: set[str]) -> str:
    out, keeping = [], False
    for line in ct.splitlines():
        m = HEADER.match(line.strip())
        if m:
            keeping = m.group(1) in keep
            continue
        if keeping:
            out.append(line)
    return "\n".join(out)


def section_convergence() -> dict:
    """Addendum H: the fixed-vocab within-paper cosine (Fig 29c) decomposed by
    review section — summary vs weaknesses+questions — over the constant-form
    window 2024-2026. Same pipeline as the headline series: one vocabulary per
    variant fit across all three years, sublinear tf, min_df=5, max 100k."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    years = (2024, 2025, 2026)
    con = sqlite3.connect(f"file:{CORPUS}?mode=ro&immutable=1", uri=True)
    data: dict[int, list] = {}
    for year in years:
        rows = con.execute(
            "SELECT forum_id, content_text FROM messages "
            "WHERE year=? AND kind='official_review'", (year,)).fetchall()
        data[year] = [
            {"forum": forum,
             "summ": _free_text(ct or "", {"summary"}),
             "crit": _free_text(ct or "", {"weaknesses", "questions"})}
            for forum, ct in rows]
    con.close()

    out = {str(y): {} for y in years}
    for variant, label in (("summ", "summary"), ("crit", "criticism")):
        vec = TfidfVectorizer(lowercase=True, sublinear_tf=True, min_df=5,
                              max_features=100_000, token_pattern=r"[a-z]{2,}")
        vec.fit([r[variant] for y in years for r in data[y]])
        for y in years:
            recs = data[y]
            X = normalize(vec.transform([r[variant] for r in recs]))
            by_forum = defaultdict(list)
            for i, r in enumerate(recs):
                by_forum[r["forum"]].append(i)
            forum_means = []
            for idxs in by_forum.values():
                if len(idxs) < 2:
                    continue
                g = (X[idxs] @ X[idxs].T).toarray()
                forum_means.append(float(np.mean(
                    [g[i, j] for i in range(len(idxs))
                     for j in range(i + 1, len(idxs))])))
            out[str(y)][f"{label}_within"] = round(float(np.median(forum_means)), 4)
            out[str(y)][f"{label}_mean_words"] = round(float(np.mean(
                [len(r[variant].split()) for r in recs])), 1)
        print(label, {y: out[str(y)][f"{label}_within"] for y in years})
    return out



def fade_length_check() -> dict:
    """Audit-cleared channel, builder-reproduced (plan addendum H): is the
    2026 marker fade a length artifact? Marker share inside fixed word-count
    bands, 2023-2026, plus the mean review length. Deterministic, no RNG."""
    from build_llmtrace_data import MARKER_SET, TOKEN, load_reviews

    bands = [(0, 200), (200, 350), (350, 500), (500, 700), (700, 10 ** 9)]
    out = {}
    for y in (2023, 2024, 2025, 2026):
        lens, hits = [], []
        for r in load_reviews(y):
            tl = TOKEN.findall(r["text"].lower())
            lens.append(len(tl))
            hits.append(bool(set(tl) & MARKER_SET))
        lens = np.array(lens); hits = np.array(hits)
        bd = {}
        for lo, hi in bands:
            m = (lens >= lo) & (lens < hi)
            key = f"{lo}-{hi - 1 if hi < 10 ** 9 else 'up'}"
            bd[key] = {"share": round(float(hits[m].mean()), 4) if m.any() else None,
                       "n": int(m.sum())}
        out[str(y)] = {"mean_words": round(float(lens.mean()), 1), "bands": bd}
        print(y, "fadelen", out[str(y)]["mean_words"],
              {k: v["share"] for k, v in bd.items()})
    return out


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

    att_null = attention_null(by_year)
    sect = section_convergence()
    fade_len = fade_length_check()

    d = json.loads(OUT.read_text())
    d["mix"] = {
        "plan": "notes/llm-era-analysis-plan.md addendum E+F (frozen before computation) + addendum H (audit nulls)",
        "concentration": mix,
        "reasoning_dispersion": disp,
        "reasoning_within_forum": within,
        "reasoning_cross_forum": cross,
        "jaccard_size_null": jaccard_null,
        "attention_null": att_null,
        "section_convergence": sect,
        "fade_length_check": fade_len,
    }
    OUT.write_text(json.dumps(d, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
