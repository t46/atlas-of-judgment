"""The anatomy of an argument, stage 1 (novelty pilot).

Treats each denial as a circuit — ground (observation) → warrant
(reasoning) → demand (suggested_improvement) — and clusters the three
spaces SEPARATELY, then records the transition structure between them.
Also mines the warrant fields for explicit normative sentences (the
unwritten rulebook) and clusters those for hand-naming.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/argument-raw-novelty.json.
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

OBJ = "novelty"
NORM = re.compile(r"requires?|must |constitutes?|is not sufficient|does not (?:equate|constitute|amount)|merely|alone (?:is|does)|threshold|criterion|necessary", re.I)


def kfit(X, k):
    from sklearn.cluster import KMeans
    return KMeans(n_clusters=k, n_init=6, random_state=46).fit(X)


def exemplars(rows_txt, X, km, k, per=6):
    out = []
    for c in range(k):
        mem = np.where(km.labels_ == c)[0]
        sims = X[mem] @ km.cluster_centers_[c] / (np.linalg.norm(km.cluster_centers_[c]) + 1e-9)
        order = mem[np.argsort(-sims)]
        out.append({"n": int(len(mem)),
                    "exemplars": [rows_txt[i][:230] for i in order[:per]]})
    return out


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    rows = []
    for pk, yr, fid, obs, rea, fix in dc.execute(
        "SELECT u.unit_pk, u.year, u.forum_id, u.observation, u.reasoning, u.suggested_improvement"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE l.object_key = ? AND u.valence = 'negative'"
        " AND u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'",
        (OBJ,),
    ):
        o = (obs or "").strip()
        r = (rea or "").strip()
        f = (fix or "").strip()
        if len(o) >= 40 and len(r) >= 40:
            rows.append((pk, yr, fid, o, r, "" if f.lower() in ("", "none") else f))
    dc.close()
    print(f"{len(rows):,} novelty denials with both slots")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")

    def embed(texts):
        return model.encode(texts, batch_size=256, normalize_embeddings=True).astype(np.float32)

    KG, KW, KD = 10, 10, 8
    Xg = embed([r[3] for r in rows])
    kg = kfit(Xg, KG)
    Xw = embed([r[4] for r in rows])
    kw = kfit(Xw, KW)
    fix_idx = [i for i, r in enumerate(rows) if r[5]]
    Xd = embed([rows[i][5] for i in fix_idx])
    kd = kfit(Xd, KD)
    dlabel = np.full(len(rows), -1)          # -1 = no demand
    for j, i in enumerate(fix_idx):
        dlabel[i] = kd.labels_[j]

    # transitions
    gw = np.zeros((KG, KW), dtype=int)
    wd = np.zeros((KW, KD + 1), dtype=int)   # last column = no demand
    for i in range(len(rows)):
        gw[kg.labels_[i], kw.labels_[i]] += 1
        wd[kw.labels_[i], dlabel[i] if dlabel[i] >= 0 else KD] += 1

    # rulebook: normative sentences from warrants
    sents = []
    for pk, yr, fid, o, r, f in rows:
        for sent in re.split(r"(?<=[.;])\s+", r):
            if 40 <= len(sent) <= 220 and NORM.search(sent):
                sents.append((sent.strip(), yr, fid))
    print(f"{len(sents):,} normative sentences mined")
    Xr = embed([t for t, _, _ in sents])
    KR = 14
    kr = kfit(Xr, KR)
    rules = []
    for c in range(KR):
        mem = np.where(kr.labels_ == c)[0]
        sims = Xr[mem] @ kr.cluster_centers_[c] / (np.linalg.norm(kr.cluster_centers_[c]) + 1e-9)
        order = mem[np.argsort(-sims)]
        rules.append({"n": int(len(mem)),
                      "exemplars": [{"text": sents[i][0][:220], "year": sents[i][1], "forum": sents[i][2]}
                                    for i in order[:8]]})
    rules.sort(key=lambda c: -c["n"])

    out = {
        "n": len(rows),
        "grounds": exemplars([r[3] for r in rows], Xg, kg, KG),
        "warrants": exemplars([r[4] for r in rows], Xw, kw, KW),
        "demands": exemplars([rows[i][5] for i in fix_idx], Xd, kd, KD),
        "no_demand": int((dlabel < 0).sum()),
        "gw": gw.tolist(), "wd": wd.tolist(),
        "rules": rules, "n_rule_sents": len(sents),
    }
    (V / f"argument-raw-{OBJ}.json").write_text(json.dumps(out))
    print("\nGROUNDS:")
    for i, c in enumerate(out["grounds"]):
        print(f"  [g{i}] n={c['n']:>6,}  {c['exemplars'][0][:105]}")
    print("WARRANTS:")
    for i, c in enumerate(out["warrants"]):
        print(f"  [w{i}] n={c['n']:>6,}  {c['exemplars'][0][:105]}")
    print("DEMANDS:")
    for i, c in enumerate(out["demands"]):
        print(f"  [d{i}] n={c['n']:>6,}  {c['exemplars'][0][:105]}")
    print("RULEBOOK (top clusters):")
    for i, c in enumerate(out["rules"][:8]):
        print(f"  [r{i}] n={c['n']:>6,}  {c['exemplars'][0]['text'][:105]}")


if __name__ == "__main__":
    main()
