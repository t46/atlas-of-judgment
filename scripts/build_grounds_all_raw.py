"""Grounds for the remaining 11 objects: cluster each object's negative
OBSERVATIONS alone (the ground slot, not the warrant), k=10, capped at
40k sampled units per object, storing per-unit assignments so the
ground -> law -> remedy circuit can be joined for every docket.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/grounds-all-raw.json.
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
K = 10
CAP = 40000


def main() -> None:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import KMeans
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    rng = random.Random(46)

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    out = {}
    for obj in OBJECTS:
        rows = []
        for pk, yr, fid, obs in dc.execute(
            "SELECT u.unit_pk, u.year, u.forum_id, u.observation"
            " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE l.object_key = ? AND u.valence IN ('negative','mixed')"
            " AND u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'"
            " AND length(u.observation) >= 40",
            (obj,),
        ):
            rows.append((pk, yr, fid, obs.strip()))
        n_total = len(rows)
        if n_total > CAP:
            rows = rng.sample(rows, CAP)
        emb = model.encode([r[3] for r in rows], batch_size=256,
                           normalize_embeddings=True).astype(np.float32)
        km = KMeans(n_clusters=K, n_init=6, random_state=46).fit(emb)
        sizes = np.bincount(km.labels_, minlength=K)
        order = np.argsort(-sizes)
        rank_of = {int(c): i for i, c in enumerate(order)}
        clusters = []
        for c in order:
            mem = np.where(km.labels_ == c)[0]
            sims = emb[mem] @ km.cluster_centers_[c] / (np.linalg.norm(km.cluster_centers_[c]) + 1e-9)
            om = mem[np.argsort(-sims)]
            clusters.append({"n": int(len(mem)),
                             "exemplars": [{"text": rows[i][3][:220], "year": rows[i][1], "forum": rows[i][2]}
                                           for i in om[:6]]})
        out[obj] = {
            "n_total": n_total, "n_clustered": len(rows),
            "clusters": clusters,
            "assign": [[r[0], rank_of[int(lb)]] for r, lb in zip(rows, km.labels_)],
        }
        print(f"{obj}: {n_total:,} obs, clustered {len(rows):,}")
        for i, c in enumerate(clusters[:4]):
            print(f"   [g{i}] n={c['n']:>6,}  {c['exemplars'][0]['text'][:95]}")

    dc.close()
    p = V / "grounds-all-raw.json"
    p.write_text(json.dumps(out))
    print(f"written ({p.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
