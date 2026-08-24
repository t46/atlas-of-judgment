"""The elements, extended: stage-1 raw for the remaining 11 objects.

For each object: mine normative sentences from the reasoning field
(same phrasing pattern as novelty — a prior about normative REGISTER,
not content), embed + k-means (k=12) for the laws; embed + k-means
(k=8) the suggested improvements for the remedies; export exemplars
for hand-naming, plus per-unit cluster assignments so the circuit
(ground x law x remedy) can be joined later. Grounds come from
caselaw-raw.json (already clustered).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/elements-all-raw.json.
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

OBJECTS = ["empirical_scope", "method_design", "theory", "stats_metrics",
           "compute_cost", "clarity", "baselines_ablations",
           "robustness_sensitivity", "problem_framing", "related_work",
           "reproducibility"]
NORM = re.compile(r"requires?|must |constitutes?|is not sufficient|does not (?:equate|constitute|amount)|merely|alone (?:is|does)|threshold|criterion|necessary", re.I)
KR, KD = 12, 8
SENT_CAP = 40000
FIX_CAP = 40000


def kfit(X, k):
    from sklearn.cluster import KMeans
    return KMeans(n_clusters=k, n_init=6, random_state=46).fit(X)


def cluster_export(texts, meta, X, km, k, per=6):
    """size-sorted clusters with exemplars; returns (clusters, rank_of)."""
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
    import random
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    rng = random.Random(46)

    def embed(texts):
        return model.encode(texts, batch_size=256, normalize_embeddings=True).astype(np.float32)

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    out = {}
    for obj in OBJECTS:
        rows = []
        for pk, yr, fid, rea, fix in dc.execute(
            "SELECT u.unit_pk, u.year, u.forum_id, u.reasoning, u.suggested_improvement"
            " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE l.object_key = ? AND u.valence IN ('negative','mixed')"
            " AND u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'",
            (obj,),
        ):
            r = (rea or "").strip()
            f = (fix or "").strip()
            rows.append((pk, yr, fid, r, "" if f.lower() in ("", "none") else f))
        n_units = len(rows)

        # ---- law sentences ----
        sents = []
        for pk, yr, fid, r, f in rows:
            for sent in re.split(r"(?<=[.;])\s+", r):
                if 40 <= len(sent) <= 220 and NORM.search(sent):
                    sents.append((pk, yr, fid, sent.strip()))
        n_sents_total = len(sents)
        if len(sents) > SENT_CAP:
            sents = rng.sample(sents, SENT_CAP)
        Xr = embed([t for _, _, _, t in sents])
        kr = kfit(Xr, KR)
        rules, rrank = cluster_export([t for _, _, _, t in sents],
                                      [{"year": y, "forum": f} for _, y, f, _ in sents], Xr, kr, KR)
        sent_assign = [[pk, rrank[int(lb)]] for (pk, _, _, _), lb in zip(sents, kr.labels_)]

        # ---- demands ----
        fixes = [(pk, f) for pk, yr, fid, r, f in rows if f]
        n_fix_total = len(fixes)
        if len(fixes) > FIX_CAP:
            fixes = rng.sample(fixes, FIX_CAP)
        Xd = embed([f for _, f in fixes])
        kd = kfit(Xd, KD)
        demands, drank = cluster_export([f for _, f in fixes],
                                        [{} for _ in fixes], Xd, kd, KD)
        fix_assign = [[pk, drank[int(lb)]] for (pk, _), lb in zip(fixes, kd.labels_)]

        out[obj] = {
            "n_units": n_units, "n_sents": n_sents_total, "n_fix": n_fix_total,
            "stated_share": round(len({pk for pk, *_ in sents}) / max(1, n_units), 4),
            "rules": rules, "demands": demands,
            "sent_assign": sent_assign, "fix_assign": fix_assign,
        }
        print(f"{obj}: units {n_units:,} · sentences {n_sents_total:,} · fixes {n_fix_total:,}")
        for i, c in enumerate(rules[:6]):
            print(f"   [r{i}] n={c['n']:>6,}  {c['exemplars'][0]['text'][:95]}")

    dc.close()
    p = V / "elements-all-raw.json"
    p.write_text(json.dumps(out))
    print(f"written ({p.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
