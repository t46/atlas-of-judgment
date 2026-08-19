"""The repair manual: what reviewers actually ask authors to do (ICLR 2026).

Samples up to 80k non-trivial suggested_improvement texts from the 2026
review-level units, embeds them locally (bge-small-en-v1.5, MPS), clusters
with k-means (k=22, seed 7), and prints c-TF-IDF terms plus exemplars per
cluster for human naming. Saves cluster assignments and per-object mixes to
data/analysis/iclr/unit-taxonomy-2026-v1/repair-raw.json for the naming pass.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
K = 22
SEED = 7
CAP = 80_000


def main() -> None:
    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT u.unit_pk, u.suggested_improvement, l.object_key, u.valence FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.suggested_improvement IS NOT NULL AND LENGTH(u.suggested_improvement) > 15"
    ).fetchall()
    conn.close()
    print(f"{len(rows)} units with improvements")
    rng = np.random.default_rng(SEED)
    idx = rng.permutation(len(rows))[:CAP]
    sample = [rows[i] for i in idx]
    texts = [r[1] for r in sample]

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    emb = model.encode(texts, batch_size=256, show_progress_bar=True, normalize_embeddings=True)

    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=K, random_state=SEED, n_init=4)
    labels = km.fit_predict(emb)

    # c-TF-IDF per cluster
    def toks(t):
        return re.findall(r"[a-z][a-z\-]{2,}", t.lower())
    STOP = set("the and for with that this from are was were been being have has had not all "
               "should would could paper authors reviewer results method model provide adding "
               "include more additional further clarify explain add".split())
    cl_tf = [Counter() for _ in range(K)]
    for t, l in zip(texts, labels):
        cl_tf[l].update(w for w in set(toks(t)) if w not in STOP)
    df = Counter()
    for c in cl_tf:
        df.update(c.keys())
    out = []
    for k in range(K):
        n = int((labels == k).sum())
        terms = sorted(cl_tf[k].items(), key=lambda kv: -kv[1] * np.log(K / max(1, df[kv[0]])))[:8]
        ex_idx = np.where(labels == k)[0]
        d = ((emb[ex_idx] - km.cluster_centers_[k]) ** 2).sum(1)
        exemplars = [texts[ex_idx[i]][:130] for i in np.argsort(d)[:3]]
        obj_mix = Counter(sample[i][2] for i in ex_idx).most_common(3)
        out.append({"k": k, "n": n, "share": round(n / len(texts), 4),
                    "terms": [t for t, _ in terms], "exemplars": exemplars,
                    "top_objects": obj_mix})
        print(f"[{k:2d}] n={n:5d} ({n/len(texts):5.1%}) terms={', '.join(t for t,_ in terms[:6])}")
        for e in exemplars:
            print(f"      · {e}")
    (V / "repair-raw.json").write_text(json.dumps({
        "n_total_with_improvement": len(rows), "n_sampled": len(texts),
        "clusters": out,
        "assignments_sample": [[int(sample[i][0]), int(labels[i])] for i in range(len(sample))],
    }) + "\n")
    print("saved repair-raw.json")


if __name__ == "__main__":
    main()
