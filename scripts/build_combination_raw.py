"""Inside the combination clause: what "mere combination" consists of.

Two decompositions of the novelty docket's combination rule:
(a) THE EXCEPTION — sub-cluster the combination-law sentences
    themselves (cached embeddings): the rule is almost always stated
    with a redemption condition ("...unless/without X"); what are the
    X's? Also counts how often the rule is stated with no way out.
(b) THE REFERENT — embed and cluster the OBSERVATIONS of the units
    that invoke the rule: what, concretely, is being called a
    combination (two methods? a loss + an architecture? a pipeline?).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/combination-raw.json.
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
SCRATCH = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/naming"
KA, KB = 8, 8
COND = re.compile(r"\bunless\b|\bwithout\b|\bif\b|\bmerely\b|\bonly if\b|\babsent\b|\blacks?\b|\bno (?:new|novel)\b", re.I)


def export(texts, X, km, k, per=6):
    sizes = np.bincount(km.labels_, minlength=k)
    order = np.argsort(-sizes)
    clusters = []
    for c in order:
        mem = np.where(km.labels_ == c)[0]
        ctr = km.cluster_centers_[c]
        sims = X[mem] @ ctr / (np.linalg.norm(ctr) + 1e-9)
        om = mem[np.argsort(-sims)]
        clusters.append({"n": int(len(mem)),
                         "exemplars": [texts[i][:230] for i in om[:per]]})
    return clusters


def main() -> None:
    from sklearn.cluster import KMeans
    nov = json.loads((V / "novelty-direct-raw.json").read_text())
    nmap = json.loads((SCRATCH / "novname.json").read_text())["rule_merge"]
    X = np.load(V / "novelty-direct-emb.npy")
    combo_ranks = {int(r) for r, k in nmap.items() if k == "combination"}

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)

    # (a) exception clauses — need the sentence text; re-mine reasoning
    # deterministically in the same order as build_novelty_direct_raw
    NORMRX = re.compile(r"requires?|must |constitutes?|is not sufficient|does not (?:equate|constitute|amount)|merely|alone (?:is|does)|threshold|criterion|necessary", re.I)
    sents = []
    for pk, yr, fid, rea in dc.execute(
        "SELECT u.unit_pk, u.year, u.custom_id, u.reasoning"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE l.object_key = 'novelty' AND u.valence IN ('negative','mixed')"
        " AND u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'",
    ):
        for sent in re.split(r"(?<=[.;])\s+", (rea or "").strip()):
            if 40 <= len(sent) <= 220 and NORMRX.search(sent):
                sents.append((pk, sent.strip()))
    assert len(sents) == nov["n_sents"], (len(sents), nov["n_sents"])

    mask = np.array([a[2] in combo_ranks for a in nov["assign"]])
    combo_idx = np.where(mask)[0]
    combo_texts = [sents[i][1] for i in combo_idx]
    combo_pks = sorted({sents[i][0] for i in combo_idx})
    Xc = X[combo_idx]
    kmA = KMeans(n_clusters=KA, n_init=6, random_state=46).fit(Xc)
    exceptions = export(combo_texts, Xc, kmA, KA)
    cond_share = sum(1 for t in combo_texts if COND.search(t)) / len(combo_texts)

    # (b) referents — observations of the invoking units (CPU embed)
    obs = []
    CHUNK = 900
    for i in range(0, len(combo_pks), CHUNK):
        chunk = combo_pks[i:i + CHUNK]
        qs = ",".join("?" * len(chunk))
        for pk, o in dc.execute(
            f"SELECT unit_pk, observation FROM units WHERE unit_pk IN ({qs})", chunk
        ):
            o = (o or "").strip()
            if len(o) >= 40:
                obs.append(o[:400])
    dc.close()

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
    Xo = model.encode(obs, batch_size=128, normalize_embeddings=True).astype(np.float32)
    kmB = KMeans(n_clusters=KB, n_init=6, random_state=46).fit(Xo)
    referents = export(obs, Xo, kmB, KB)

    payload = {
        "n_combo_sentences": len(combo_texts),
        "n_combo_units": len(combo_pks),
        "conditional_share": round(cond_share, 4),
        "exceptions": exceptions,
        "referents": referents,
    }
    (V / "combination-raw.json").write_text(json.dumps(payload))
    print(f"{len(combo_texts):,} combination-law sentences from {len(combo_pks):,} units;"
          f" {cond_share:.1%} state a condition")
    for i, c in enumerate(exceptions):
        print(f"[exc {i}] n={c['n']:>5,}  {c['exemplars'][0][:100]}")
    for i, c in enumerate(referents):
        print(f"[ref {i}] n={c['n']:>5,}  {c['exemplars'][0][:100]}")


if __name__ == "__main__":
    main()
