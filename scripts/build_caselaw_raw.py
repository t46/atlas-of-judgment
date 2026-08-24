"""The case law, stage 1 for all remaining objects: cluster each
object's negative reasoning (initial reviews, official reviewers,
sampled to 40k per object) exactly as done for novelty, and write
exemplars per cluster for hand-naming.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/caselaw-raw.json.
"""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

OBJECTS = ["empirical_scope", "method_design", "theory", "stats_metrics",
           "compute_cost", "clarity", "baselines_ablations",
           "robustness_sensitivity", "problem_framing", "related_work",
           "reproducibility"]
CAP = 40000
K = 12


def main() -> None:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import KMeans
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    rng = random.Random(46)

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    out = {}
    for obj in OBJECTS:
        rows = []
        for pk, yr, fid, obs, rea, jud in dc.execute(
            "SELECT u.unit_pk, u.year, u.forum_id, u.observation, u.reasoning, u.judgment"
            " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE l.object_key = ? AND u.reviewer_role = 'official_reviewer'"
            " AND u.temporal_position = 'initial_review' AND u.valence IN ('negative','mixed')",
            (obj,),
        ):
            txt = ((obs or "") + " " + (rea or "")).strip()
            if len(txt) >= 50:
                rows.append((pk, yr, fid, txt, (jud or "")[:150]))
        n_total = len(rows)
        if n_total > CAP:
            rows = rng.sample(rows, CAP)
        print(f"{obj}: {n_total:,} units, clustering {len(rows):,}")
        emb = model.encode([r[3] for r in rows], batch_size=256,
                           normalize_embeddings=True).astype(np.float32)
        km = KMeans(n_clusters=K, n_init=6, random_state=46).fit(emb)
        clusters = []
        for c in range(K):
            mem = np.where(km.labels_ == c)[0]
            sims = emb[mem] @ km.cluster_centers_[c] / (np.linalg.norm(km.cluster_centers_[c]) + 1e-9)
            order = mem[np.argsort(-sims)]
            ex = [{"year": rows[i][1], "forum": rows[i][2], "text": rows[i][3][:240]} for i in order[:6]]
            ys = {}
            for i in mem:
                ys[rows[i][1]] = ys.get(rows[i][1], 0) + 1
            clusters.append({"n": int(len(mem)), "n_scale": n_total / len(rows),
                             "years": ys, "exemplars": ex})
        clusters.sort(key=lambda c: -c["n"])
        out[obj] = {"n_total": n_total, "n_clustered": len(rows), "clusters": clusters}

    dc.close()
    (V / "caselaw-raw.json").write_text(json.dumps(out))
    print("written caselaw-raw.json")


if __name__ == "__main__":
    main()
