"""Novelty's laws on the Direct track, all nine years — stage 1.

Same normative-sentence mining as the other eleven dockets (NORM regex,
40-220 chars), but embedded WITHOUT sampling so two questions become
answerable downstream: (a) the charge-sheet join — which laws co-occur
with the other dockets' laws inside one review — and (b) the
timelessness test — fit k-means on 2026 sentences alone, assign every
year to those centroids, and (reverse) fit on 2018-19, assign 2026.
Distances to the nearest centroid are stored per sentence so "the 2026
rulebook explains 2018 reasoning" can be quantified, not asserted.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/novelty-direct-raw.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
NORM = re.compile(r"requires?|must |constitutes?|is not sufficient|does not (?:equate|constitute|amount)|merely|alone (?:is|does)|threshold|criterion|necessary", re.I)
K = 12


def cluster_export(texts, meta, X, km, k, per=6):
    sizes = np.bincount(km.labels_, minlength=k)
    order = np.argsort(-sizes)
    rank_of = {int(c): i for i, c in enumerate(order)}
    clusters = []
    for c in order:
        mem = np.where(km.labels_ == c)[0]
        sims = X[mem] @ km.cluster_centers_[c] / (np.linalg.norm(km.cluster_centers_[c]) + 1e-9)
        om = mem[np.argsort(-sims)]
        clusters.append({"n": int(len(mem)),
                         "exemplars": [{"text": texts[i][:230], **meta[i]} for i in om[:per]]})
    return clusters, rank_of


def main() -> None:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import KMeans
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    sents = []
    n_units = 0
    for pk, yr, fid, rea in dc.execute(
        "SELECT u.unit_pk, u.year, u.custom_id, u.reasoning"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE l.object_key = 'novelty' AND u.valence IN ('negative','mixed')"
        " AND u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'",
    ):
        n_units += 1
        for sent in re.split(r"(?<=[.;])\s+", (rea or "").strip()):
            if 40 <= len(sent) <= 220 and NORM.search(sent):
                sents.append((pk, yr, fid, sent.strip()))
    dc.close()
    years = np.array([s[1] for s in sents])
    print(f"novelty: {n_units:,} units -> {len(sents):,} normative sentences "
          f"(2026: {(years == 2026).sum():,}, 2018-19: {((years <= 2019)).sum():,})")

    X = model.encode([s[3] for s in sents], batch_size=256,
                     normalize_embeddings=True).astype(np.float32)

    # fit A: the 2026 code
    m26 = years == 2026
    kmA = KMeans(n_clusters=K, n_init=6, random_state=46).fit(X[m26])
    idx26 = np.where(m26)[0]
    rulesA, rankA = cluster_export([sents[i][3] for i in idx26],
                                   [{"year": sents[i][1], "forum": sents[i][2]} for i in idx26],
                                   X[m26], kmA, K)
    C = kmA.cluster_centers_ / (np.linalg.norm(kmA.cluster_centers_, axis=1, keepdims=True) + 1e-9)
    simsA = X @ C.T
    nnA = simsA.argmax(1)
    dA = 1 - simsA.max(1)

    # fit B: the 2018-19 code, applied forward (only 417 sentences -> k=6)
    KB = 6
    mOld = years <= 2019
    kmB = KMeans(n_clusters=KB, n_init=6, random_state=46).fit(X[mOld])
    idxO = np.where(mOld)[0]
    rulesB, _ = cluster_export([sents[i][3] for i in idxO],
                               [{"year": sents[i][1], "forum": sents[i][2]} for i in idxO],
                               X[mOld], kmB, KB)
    CB = kmB.cluster_centers_ / (np.linalg.norm(kmB.cluster_centers_, axis=1, keepdims=True) + 1e-9)
    dB = 1 - (X @ CB.T).max(1)

    # calibration: fit on a random half of 2026, measure the held-out half
    rng = np.random.default_rng(46)
    half = rng.permutation(idx26)
    a_idx, b_idx = half[: len(half) // 2], half[len(half) // 2:]
    kmH = KMeans(n_clusters=K, n_init=6, random_state=46).fit(X[a_idx])
    CH = kmH.cluster_centers_ / (np.linalg.norm(kmH.cluster_centers_, axis=1, keepdims=True) + 1e-9)
    dH = 1 - (X @ CH.T).max(1)
    control = {
        "heldout_2026": round(float(dH[b_idx].mean()), 4),
        "y2018_19_vs_half2026_code": round(float(dH[mOld].mean()), 4),
        "n_half": int(len(a_idx)),
    }
    np.save(V / "novelty-direct-emb.npy", X)

    out = {
        "n_units": n_units, "n_sents": len(sents),
        "rules_2026": rulesA,
        "rules_2018_19": rulesB,
        "assign": [[int(s[0]), int(s[1]), int(rankA[int(c)]), round(float(d), 4)]
                   for s, c, d in zip(sents, nnA, dA)],
        "dist_to_2018_code": {str(y): round(float(dB[years == y].mean()), 4)
                              for y in range(2018, 2027) if (years == y).any()},
        "dist_to_2026_code": {str(y): round(float(dA[years == y].mean()), 4)
                              for y in range(2018, 2027) if (years == y).any()},
        "control": control,
    }
    p = V / "novelty-direct-raw.json"
    p.write_text(json.dumps(out))
    print(f"written ({p.stat().st_size/1e6:.1f} MB)")
    print("dist to 2026 code by year:", out["dist_to_2026_code"])
    print("dist to 2018 code by year:", out["dist_to_2018_code"])
    for i, c in enumerate(rulesA[:8]):
        print(f"  [A r{i}] n={c['n']:>6,}  {c['exemplars'][0]['text'][:95]}")


if __name__ == "__main__":
    main()
