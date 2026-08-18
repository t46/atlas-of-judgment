"""Build the Logic Unit Galaxy: 2D UMAP of a 40k-unit sample for canvas rendering.

Embeds "inspected_object — reasoning" per sampled unit (bge-small, local),
projects to 2D with seeded UMAP, quantizes coordinates to a 0..4095 grid and
writes galaxy.json with per-point category indices and a short hover text.

Writes into data/analysis/iclr/unit-taxonomy-2026-v1/:
  galaxy.json
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
SEED = 11
VAL_ORDER = ["negative", "uncertain", "mixed", "conditional", "positive"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=40000)
    args = parser.parse_args()

    taxonomy = json.loads((OUTPUT_DIR / "taxonomy-v1.json").read_text())
    obj_keys = [c["key"] for c in taxonomy["inspected_object"]]
    rea_keys = [c["key"] for c in taxonomy["reasoning"]]

    conn = sqlite3.connect(f"file:{OUTPUT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) c FROM units").fetchone()["c"]
    rng = np.random.default_rng(SEED)
    picks = sorted(rng.choice(total, size=min(args.sample_size, total), replace=False) + 1)
    rows: list[sqlite3.Row] = []
    for start in range(0, len(picks), 900):
        chunk = [int(p) for p in picks[start : start + 900]]
        marks = ",".join("?" * len(chunk))
        rows.extend(
            conn.execute(
                f"SELECT u.unit_pk, u.inspected_object, u.reasoning, u.valence,"
                f" l.object_key, l.reasoning_key"
                f" FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
                f" WHERE u.unit_pk IN ({marks})",
                chunk,
            )
        )
    conn.close()
    print(f"sampled {len(rows)} units", flush=True)

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    texts = [f"{r['inspected_object']} — {r['reasoning']}" for r in rows]
    vectors = model.encode(
        texts, batch_size=256, normalize_embeddings=True, show_progress_bar=True
    )

    import umap

    reducer = umap.UMAP(
        n_components=2, n_neighbors=30, min_dist=0.08, metric="cosine", random_state=SEED
    )
    xy = reducer.fit_transform(vectors)
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    q = np.round((xy - lo) / (hi - lo) * 4095).astype(int)

    points = []
    for row, (x, y) in zip(rows, q.tolist()):
        snippet = row["inspected_object"]
        if len(snippet) > 110:
            snippet = snippet[:107] + "..."
        points.append(
            [
                x,
                y,
                obj_keys.index(row["object_key"]),
                rea_keys.index(row["reasoning_key"]),
                VAL_ORDER.index(row["valence"]),
                snippet,
            ]
        )

    payload = {
        "seed": SEED,
        "sample_size": len(points),
        "obj_keys": obj_keys,
        "rea_keys": rea_keys,
        "val_order": VAL_ORDER,
        "columns": ["x", "y", "obj", "rea", "val", "snippet"],
        "points": points,
    }
    out = OUTPUT_DIR / "galaxy.json"
    out.write_text(json.dumps(payload, ensure_ascii=False) + "\n")
    print(f"{out} written ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
