"""Assign taxonomy-v1 categories to all logic units by nearest category centroid.

Steps:
  1. Recover sample cluster labels deterministically (same seed/params as
     induce_unit_taxonomy.py, reusing cached sample embeddings).
  2. Build one centroid per taxonomy category = normalized mean of the sample
     embeddings belonging to that category's member clusters.
  3. Embed the full corpus (410k units x 2 fields) locally and assign each unit
     the nearest centroid; store cosine similarity as assignment confidence.

Writes into data/analysis/iclr/unit-taxonomy-2026-v1/:
  units.sqlite3 (new table unit_labels)
  centroids-<field>.npz
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
SEED = 7

LABELS_DDL = """
CREATE TABLE IF NOT EXISTS unit_labels (
    unit_pk INTEGER PRIMARY KEY REFERENCES units(unit_pk),
    object_key TEXT NOT NULL,
    object_sim REAL NOT NULL,
    reasoning_key TEXT NOT NULL,
    reasoning_sim REAL NOT NULL
);
"""


def recover_sample_labels(field: str) -> np.ndarray:
    """Re-run the seeded UMAP+HDBSCAN pipeline on cached sample embeddings."""
    import hdbscan
    import umap

    vectors = np.load(OUTPUT_DIR / f"embeddings-{field}-sample.npy")
    reducer = umap.UMAP(
        n_components=15, n_neighbors=30, min_dist=0.0, metric="cosine", random_state=SEED
    )
    reduced = reducer.fit_transform(vectors)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=60, min_samples=10, cluster_selection_method="eom"
    )
    return clusterer.fit_predict(reduced)


def build_centroids(field: str, taxonomy: dict) -> tuple[list[str], np.ndarray]:
    vectors = np.load(OUTPUT_DIR / f"embeddings-{field}-sample.npy")
    labels = recover_sample_labels(field)
    keys, centroids = [], []
    for category in taxonomy[field]:
        mask = np.isin(labels, category["clusters"])
        if not mask.any():
            raise RuntimeError(f"{field}/{category['key']}: no sample members")
        centroid = vectors[mask].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        keys.append(category["key"])
        centroids.append(centroid)
    matrix = np.stack(centroids)
    np.savez(
        OUTPUT_DIR / f"centroids-{field}.npz",
        keys=np.array(keys),
        centroids=matrix,
        member_counts=np.array(
            [int(np.isin(labels, c["clusters"]).sum()) for c in taxonomy[field]]
        ),
    )
    return keys, matrix


def assign_all(centroid_sets: dict, batch_size: int = 20000) -> int:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    db_path = OUTPUT_DIR / "units.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(LABELS_DDL)
    total = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]

    done = 0
    while True:
        rows = conn.execute(
            "SELECT u.unit_pk, u.inspected_object, u.reasoning FROM units u"
            " LEFT JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE l.unit_pk IS NULL ORDER BY u.unit_pk LIMIT ?",
            (batch_size,),
        ).fetchall()
        if not rows:
            break
        pks = [r[0] for r in rows]
        out = {}
        for field_index, field in enumerate(FIELDS):
            texts = [r[1 + field_index] for r in rows]
            vectors = model.encode(
                texts, batch_size=256, normalize_embeddings=True, show_progress_bar=False
            )
            keys, matrix = centroid_sets[field]
            sims = vectors @ matrix.T
            best = sims.argmax(axis=1)
            out[field] = [(keys[b], float(s[b])) for b, s in zip(best, sims)]
        conn.executemany(
            "INSERT INTO unit_labels VALUES (?,?,?,?,?)",
            [
                (pk, *out["inspected_object"][i], *out["reasoning"][i])
                for i, pk in enumerate(pks)
            ],
        )
        conn.commit()
        done += len(rows)
        print(f"assigned {done} (+{len(rows)}) / remaining of {total}", flush=True)
    labeled = conn.execute("SELECT COUNT(*) FROM unit_labels").fetchone()[0]
    conn.close()
    return labeled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=20000)
    args = parser.parse_args()

    taxonomy = json.loads((OUTPUT_DIR / "taxonomy-v1.json").read_text())
    taxonomy_by_field = {
        "inspected_object": taxonomy["inspected_object"],
        "reasoning": taxonomy["reasoning"],
    }
    centroid_sets = {}
    for field in FIELDS:
        keys, matrix = build_centroids(field, taxonomy_by_field)
        centroid_sets[field] = (keys, matrix)
        print(f"{field}: {len(keys)} centroids built", flush=True)
    labeled = assign_all(centroid_sets, batch_size=args.batch_size)
    print(f"done: {labeled} units labeled")


if __name__ == "__main__":
    main()
