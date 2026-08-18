"""Cache bge-small embeddings of every Direct-track reasoning text.

Writes data/analysis/iclr/unit-taxonomy-direct-v1/reasoning-embeddings.npy
(float16, row i = unit_pk i+1; unit_pk is contiguous 1..N) so downstream
classifiers can run without re-embedding. Resumable via a progress file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
CHUNK = 20000


def main() -> None:
    conn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    total, max_pk = conn.execute("SELECT COUNT(*), MAX(unit_pk) FROM units").fetchone()
    assert total == max_pk, "unit_pk must be contiguous"

    out = DIRECT_DIR / "reasoning-embeddings.npy"
    progress = DIRECT_DIR / "reasoning-embeddings.progress"
    start = int(progress.read_text()) if progress.exists() else 0
    if not out.exists():
        mm = np.lib.format.open_memmap(out, mode="w+", dtype=np.float16, shape=(total, DIM))
        del mm
        start = 0

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    mm = np.lib.format.open_memmap(out, mode="r+")
    while start < total:
        rows = conn.execute(
            "SELECT unit_pk, reasoning FROM units WHERE unit_pk > ? ORDER BY unit_pk LIMIT ?",
            (start, CHUNK),
        ).fetchall()
        vectors = model.encode(
            [r[1] for r in rows], batch_size=256, normalize_embeddings=True,
            show_progress_bar=False,
        )
        mm[rows[0][0] - 1 : rows[-1][0]] = vectors.astype(np.float16)
        start = rows[-1][0]
        progress.write_text(str(start))
        print(f"embedded {start}/{total}", flush=True)
    mm.flush()
    print("done")


if __name__ == "__main__":
    main()
