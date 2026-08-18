"""Assign taxonomy-v1 categories to all Direct-track (2018-2026) logic units.

Reuses the category centroids induced on the 2026 compact sample
(unit-taxonomy-2026-v1/centroids-*.npz) so both tracks share one taxonomy.
Embeds inspected_object and reasoning locally and stores nearest-centroid
labels + cosine similarity in unit_labels (resumable).
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CENTROID_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
FIELDS = ("inspected_object", "reasoning")

LABELS_DDL = """
CREATE TABLE IF NOT EXISTS unit_labels (
    unit_pk INTEGER PRIMARY KEY REFERENCES units(unit_pk),
    object_key TEXT NOT NULL,
    object_sim REAL NOT NULL,
    reasoning_key TEXT NOT NULL,
    reasoning_sim REAL NOT NULL
);
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=20000)
    args = parser.parse_args()

    centroid_sets = {}
    for field in FIELDS:
        z = np.load(CENTROID_DIR / f"centroids-{field}.npz")
        centroid_sets[field] = ([str(k) for k in z["keys"]], z["centroids"])
        print(f"{field}: {len(z['keys'])} centroids loaded from 2026 taxonomy", flush=True)

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    conn = sqlite3.connect(OUTPUT_DIR / "units.sqlite3")
    conn.executescript(LABELS_DDL)
    total = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]

    done = 0
    while True:
        rows = conn.execute(
            "SELECT u.unit_pk, u.inspected_object, u.reasoning FROM units u"
            " LEFT JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE l.unit_pk IS NULL ORDER BY u.unit_pk LIMIT ?",
            (args.batch_size,),
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
        print(f"assigned {done} this run / total {total}", flush=True)
    labeled = conn.execute("SELECT COUNT(*) FROM unit_labels").fetchone()[0]
    conn.close()
    print(f"done: {labeled}/{total} units labeled")


if __name__ == "__main__":
    main()
