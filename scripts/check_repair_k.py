"""k-robustness pass for Plate XI (repair manual). Reuses the exact shipped
sample (pks from repair-raw.json assignments_sample), re-embeds with
bge-small, re-clusters at k in {11, 17, 22, 28, 44} (22 = shipped repro
check, reproduces the shipped partition at ARI 1.000), maps alt clusters to
the shipped 16 merged groups by member plurality, and reports headline
shares, family sums, and ARI vs the shipped partition. Output:
repair-k-robustness.json (verification record, not a rendered island).
First run 2026-08-27 (adversarial-audit cycle); touches no shipped island.
"""
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path("/Users/s30825/unktok/dev/ml-top-conf-review-analysis")
V = ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
OUT = V / "repair-k-robustness.json"

GROUPS = {
    "Extend the evidence — more datasets, domains, models, scale": [1, 2, 19, 20],
    "Compare against stronger baselines": [14, 18],
    "Fix the surface — typos, notation, figures": [7, 13],
    "Measure the true cost — runtime, memory, GPU-hours": [9, 15],
    "Strengthen the theory — proofs, bounds, guarantees": [5],
    "Justify assumptions & scope the claims": [4],
    "Articulate the contribution": [8],
    "Release & document — code, data, repro details": [3],
    "Situate in the literature": [12],
    "Validate against humans & the real world": [16],
    "Show it on real visual data": [10],
    "Report error bars & significance": [17],
    "Ablate the components": [6],
    "Stress-test under attack & shift": [11],
    "Analyze the failures": [21],
    "Probe hyperparameter sensitivity": [0],
}
K2G = {k: g for g, ks in GROUPS.items() for k in ks}


def fam_of(name: str) -> str:
    if "Release" in name:
        return "disclose"
    if any(w in name for w in ("surface", "Justify", "Articulate", "Situate")):
        return "articulate"
    return "substantiate"


def main() -> None:
    raw = json.loads((V / "repair-raw.json").read_text())
    assign = raw["assignments_sample"]
    pks = [int(pk) for pk, _ in assign]
    shipped_raw = np.array([int(k) for _, k in assign])
    shipped_group = np.array([K2G[k] for k in shipped_raw])
    print(f"{len(pks)} sampled pks; shipped raw k = {len(set(shipped_raw.tolist()))}")

    # shipped merged shares (repro of repair-manual.json)
    ship_shares = Counter(shipped_group.tolist())
    top3 = ship_shares.most_common(3)
    print("shipped merged top3:", [(g[:20], round(n / len(pks), 4)) for g, n in top3])

    text_of = {}
    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    for i in range(0, len(pks), 900):
        chunk = pks[i:i + 900]
        for pk, t in conn.execute(
            f"SELECT unit_pk, suggested_improvement FROM units WHERE unit_pk IN ({','.join('?' * len(chunk))})",
            chunk,
        ):
            text_of[pk] = t
    conn.close()
    texts = [text_of[pk] for pk in pks]
    assert all(t for t in texts)

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    emb = model.encode(texts, batch_size=256, show_progress_bar=True, normalize_embeddings=True)

    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score

    results = {}
    for k in (11, 17, 22, 28, 44):
        km = KMeans(n_clusters=k, random_state=7, n_init=4)
        labels = km.fit_predict(emb)
        ari = adjusted_rand_score(shipped_raw, labels)

        # plurality-map each new cluster to a shipped merged group
        merged = Counter()
        purity_extend_num = purity_extend_den = 0
        clus_map = {}
        for c in range(k):
            m = labels == c
            comp = Counter(shipped_group[m].tolist())
            g, gn = comp.most_common(1)[0]
            clus_map[c] = g
            merged[g] += int(m.sum())
            if g.startswith("Extend the evidence"):
                purity_extend_num += gn
                purity_extend_den += int(m.sum())
        n = len(pks)
        shares = {g: cnt / n for g, cnt in merged.items()}
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])
        fam = Counter()
        for g, s in shares.items():
            fam[fam_of(g)] += s
        results[k] = {
            "ari_vs_shipped": round(float(ari), 4),
            "n_groups_recovered": len(merged),
            "top": [ranked[0][0], round(ranked[0][1], 4)],
            "second": [ranked[1][0], round(ranked[1][1], 4)],
            "extend_share": round(shares.get(
                "Extend the evidence — more datasets, domains, models, scale", 0.0), 4),
            "extend_rank": next((i + 1 for i, (g, _) in enumerate(ranked)
                                 if g.startswith("Extend the evidence")), None),
            "extend_mapped_purity": round(purity_extend_num / purity_extend_den, 4)
            if purity_extend_den else None,
            "families": {f: round(s, 4) for f, s in fam.items()},
        }
        print(k, json.dumps(results[k]))

    OUT.write_text(json.dumps(results, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
