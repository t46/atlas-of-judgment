"""The rubber stamp: how much of a review could have been written
without reading the paper?

Every negative unit's observation (what the reviewer actually found) is
embedded locally (bge-small-en-v1.5, MPS). For each unit we find its
nearest neighbor among units of OTHER papers; if a near-verbatim twin of
your criticism exists in someone else's review, the criticism is
interchangeable — boilerplate. We report the full similarity
distribution plus interchangeability shares at fixed thresholds, per
object, per rating, and exemplar twin pairs for the figure.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/boilerplate-data.json
and caches embeddings at boilerplate-emb.npy.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
EMB_CACHE = V / "boilerplate-emb.npy"
META_CACHE = V / "boilerplate-meta.json"

THRESHOLDS = [0.85, 0.90, 0.95]


def load_units():
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    rows = []
    for pk, pid, rid, obj, val, obs in uc.execute(
        "SELECT u.unit_pk, u.paper_id, u.review_id, l.object_key, u.valence, u.observation"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.valence = 'negative' AND u.observation IS NOT NULL"
    ):
        t = (obs or "").strip()
        if len(t) >= 40:
            rows.append((pk, pid, rid, obj, t))
    uc.close()
    print(f"{len(rows):,} negative units with observations")
    return rows


def ratings_2026():
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    out = {}
    for rid, cj in ac.execute(
        "SELECT note_id, content_json FROM messages WHERE kind='official_review' AND year=2026"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, int):
            out[rid] = r
    ac.close()
    return out


def main() -> None:
    rows = load_units()
    texts = [r[4] for r in rows]

    if EMB_CACHE.exists():
        emb = np.load(EMB_CACHE)
        assert len(emb) == len(rows)
        print("embeddings loaded from cache")
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
        emb = model.encode(texts, batch_size=256, show_progress_bar=True,
                           normalize_embeddings=True).astype(np.float32)
        np.save(EMB_CACHE, emb)
        print("embeddings computed and cached")

    n = len(rows)
    papers = np.array([hash(r[1]) for r in rows], dtype=np.int64)
    emb = np.ascontiguousarray(emb, dtype=np.float32)

    # blockwise top-K, then drop same-paper hits
    K = 8
    B = 1024
    best_sim = np.zeros(n, dtype=np.float32)
    best_idx = np.zeros(n, dtype=np.int64)
    for s0 in range(0, n, B):
        s1 = min(n, s0 + B)
        sims = emb[s0:s1] @ emb.T                       # (b, n)
        part = np.argpartition(sims, -K - 1, axis=1)[:, -K - 1:]
        for bi in range(s1 - s0):
            i = s0 + bi
            cand = part[bi]
            order = cand[np.argsort(sims[bi, cand])[::-1]]
            for j in order:
                if j != i and papers[j] != papers[i]:
                    best_sim[i] = sims[bi, j]
                    best_idx[i] = j
                    break
        if (s0 // B) % 40 == 0:
            print(f"  nn {s0:,}/{n:,}")

    rat = ratings_2026()
    hist_bins = np.arange(0.5, 1.001, 0.01)
    hist = np.histogram(best_sim, bins=hist_bins)[0]

    by_obj = defaultdict(lambda: [0, [0] * len(THRESHOLDS)])
    by_rat = defaultdict(lambda: [0, [0] * len(THRESHOLDS)])
    for i, (pk, pid, rid, obj, t) in enumerate(rows):
        o = by_obj[obj]
        o[0] += 1
        r = rat.get(rid)
        rr = by_rat[r] if r is not None else None
        if rr is not None:
            rr[0] += 1
        for ti, th in enumerate(THRESHOLDS):
            if best_sim[i] >= th:
                o[1][ti] += 1
                if rr is not None:
                    rr[1][ti] += 1

    overall = [round(float((best_sim >= th).mean()), 4) for th in THRESHOLDS]

    # exemplar twin pairs: high-sim pairs across papers, diverse objects
    pairs = []
    seen_obj = defaultdict(int)
    for i in np.argsort(best_sim)[::-1][:4000]:
        obj = rows[i][3]
        if seen_obj[obj] >= 3:
            continue
        j = int(best_idx[i])
        a, b = texts[i], texts[j]
        if a[:60].lower() == b[:60].lower():
            continue  # skip near-verbatim dupes for display variety
        seen_obj[obj] += 1
        pairs.append({"sim": round(float(best_sim[i]), 4), "obj": obj,
                      "a": a[:220], "b": b[:220],
                      "pa": rows[i][1], "pb": rows[j][1],
                      "ra": rows[i][2], "rb": rows[j][2]})
        if len(pairs) >= 24:
            break

    out = {
        "n": n,
        "thresholds": THRESHOLDS,
        "overall": overall,
        "median_sim": round(float(np.median(best_sim)), 4),
        "hist": {"bins": [round(float(b), 2) for b in hist_bins[:-1]],
                 "counts": [int(c) for c in hist]},
        "by_object": {o: {"n": v[0], "shares": [round(x / v[0], 4) for x in v[1]]}
                      for o, v in by_obj.items()},
        "by_rating": {str(r): {"n": v[0], "shares": [round(x / v[0], 4) for x in v[1]]}
                      for r, v in sorted(by_rat.items())},
        "pairs": pairs,
    }
    (V / "boilerplate-data.json").write_text(json.dumps(out))
    print("overall interchangeable shares", dict(zip(THRESHOLDS, overall)),
          "median nn-sim", out["median_sim"])
    for o, v in sorted(by_obj.items(), key=lambda kv: -kv[1][1][1] / kv[1][0]):
        print(f"  {o:>26s} n={v[0]:>7,}  @0.90={v[1][1]/v[0]:.1%}")


if __name__ == "__main__":
    main()
