"""The law of novelty, stage 1: cluster every novelty unit's reasoning.

What exactly does a reviewer say when they judge novelty? Direct track,
official reviewers, initial reviews, object = novelty, all nine years.
Embeds observation + reasoning locally (bge-small, MPS), k-means on
negative units (k=16) and positive units (k=8) separately, and writes
exemplars per cluster for hand-naming.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/noveltylaw-raw.json.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    rows = []
    for pk, yr, fid, val, obs, rea, jud in dc.execute(
        "SELECT u.unit_pk, u.year, u.forum_id, u.valence, u.observation, u.reasoning, u.judgment"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE l.object_key = 'novelty' AND u.reviewer_role = 'official_reviewer'"
        " AND u.temporal_position = 'initial_review'"
    ):
        txt = ((obs or "") + " " + (rea or "")).strip()
        if len(txt) >= 50:
            rows.append((pk, yr, fid, val, txt, (jud or "")[:160]))
    dc.close()
    print(f"{len(rows):,} novelty units")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    emb = model.encode([r[4] for r in rows], batch_size=256, show_progress_bar=True,
                       normalize_embeddings=True).astype(np.float32)

    from sklearn.cluster import KMeans

    out = {}
    for tag, sel_vals, k in (("negative", ("negative", "mixed"), 16), ("positive", ("positive",), 8)):
        idx = [i for i, r in enumerate(rows) if r[3] in sel_vals]
        X = emb[idx]
        km = KMeans(n_clusters=k, n_init=8, random_state=46).fit(X)
        clusters = []
        for c in range(k):
            members = [idx[i] for i in np.where(km.labels_ == c)[0]]
            sims = X[km.labels_ == c] @ km.cluster_centers_[c] / (np.linalg.norm(km.cluster_centers_[c]) + 1e-9)
            order = np.argsort(-sims)
            ex = []
            for oi in order[:8]:
                r = rows[members[oi]]
                ex.append({"year": r[1], "forum": r[2], "text": r[4][:260], "judgment": r[5]})
            # year distribution
            ys = {}
            for m in members:
                ys[rows[m][1]] = ys.get(rows[m][1], 0) + 1
            clusters.append({"n": len(members), "exemplars": ex, "years": ys,
                             "member_pks": [rows[m][0] for m in members]})
        clusters.sort(key=lambda c: -c["n"])
        out[tag] = {"k": k, "n": len(idx), "clusters": clusters}
        print(f"{tag}: n={len(idx):,}")
        for ci, c in enumerate(clusters):
            print(f"  [{tag[:3]}-{ci:02d}] n={c['n']:>6,}  {c['exemplars'][0]['text'][:110]}")

    (V / "noveltylaw-raw.json").write_text(json.dumps(out))
    print("written noveltylaw-raw.json")


if __name__ == "__main__":
    main()
