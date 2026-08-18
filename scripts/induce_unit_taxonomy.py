"""Induce taxonomy candidates for inspected_object and reasoning via clustering.

Samples units from unit-taxonomy-2026-v1/units.sqlite3, embeds the two free-text
fields locally (sentence-transformers, no API cost), clusters with UMAP+HDBSCAN,
and writes cluster digests (size + exemplars nearest to centroid) for human review.

Writes into data/analysis/iclr/unit-taxonomy-2026-v1/:
  sample-units.json                 sampled unit_pk list (seeded, reproducible)
  embeddings-<field>-sample.npy     sample embeddings
  clusters-<field>.json             cluster digests for taxonomy review
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
FIELDS = ("inspected_object", "reasoning")


def load_sample(sample_size: int, seed: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{OUTPUT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        rng = np.random.default_rng(seed)
        picks = sorted(rng.choice(total, size=min(sample_size, total), replace=False) + 1)
        rows = []
        for chunk_start in range(0, len(picks), 900):
            chunk = [int(p) for p in picks[chunk_start : chunk_start + 900]]
            marks = ",".join("?" * len(chunk))
            rows.extend(
                dict(row)
                for row in conn.execute(
                    f"SELECT unit_pk, inspected_object, reasoning, valence"
                    f" FROM units WHERE unit_pk IN ({marks})",
                    chunk,
                )
            )
    finally:
        conn.close()
    return rows


def embed(texts: list[str], cache_path: Path) -> np.ndarray:
    if cache_path.exists():
        return np.load(cache_path)
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode(
        texts, batch_size=256, show_progress_bar=True, normalize_embeddings=True
    )
    np.save(cache_path, vectors)
    return vectors


def cluster_field(
    field: str, units: list[dict], min_cluster_size: int, seed: int
) -> dict:
    import hdbscan
    import umap

    texts = [u[field] for u in units]
    vectors = embed(texts, OUTPUT_DIR / f"embeddings-{field}-sample.npy")

    reducer = umap.UMAP(
        n_components=15, n_neighbors=30, min_dist=0.0, metric="cosine", random_state=seed
    )
    reduced = reducer.fit_transform(vectors)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size, min_samples=10, cluster_selection_method="eom"
    )
    labels = clusterer.fit_predict(reduced)

    digests = []
    for label in sorted(set(labels)):
        if label == -1:
            continue
        member_idx = np.where(labels == label)[0]
        centroid = vectors[member_idx].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        sims = vectors[member_idx] @ centroid
        exemplar_idx = member_idx[np.argsort(-sims)[:12]]
        valences = [units[i]["valence"] for i in member_idx]
        digests.append(
            {
                "cluster": int(label),
                "size": int(len(member_idx)),
                "share": round(len(member_idx) / len(units), 4),
                "valence_counts": {v: valences.count(v) for v in sorted(set(valences))},
                "exemplars": [texts[i] for i in exemplar_idx],
            }
        )
    digests.sort(key=lambda d: -d["size"])
    noise = int((labels == -1).sum())
    return {
        "field": field,
        "model": MODEL_NAME,
        "sample_size": len(units),
        "min_cluster_size": min_cluster_size,
        "n_clusters": len(digests),
        "noise_points": noise,
        "noise_share": round(noise / len(units), 4),
        "clusters": digests,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=12000)
    parser.add_argument("--min-cluster-size", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    units = load_sample(args.sample_size, args.seed)
    (OUTPUT_DIR / "sample-units.json").write_text(
        json.dumps({"seed": args.seed, "unit_pks": [u["unit_pk"] for u in units]}) + "\n"
    )
    for field in FIELDS:
        result = cluster_field(field, units, args.min_cluster_size, args.seed)
        out = OUTPUT_DIR / f"clusters-{field}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
        print(
            f"{field}: {result['n_clusters']} clusters, "
            f"noise {result['noise_share']:.1%} -> {out}"
        )


if __name__ == "__main__":
    main()
