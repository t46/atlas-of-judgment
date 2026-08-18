"""Export the two unit databases + taxonomy to a HuggingFace dataset staging dir.

Layout (staged under --out, default data/analysis/iclr/hf-dataset):
  review_level_2026/units-*.parquet    410,586 units JOIN taxonomy labels
  review_level_2026/reviews.parquet    74,380 review summaries
  forum_level_2018_2026/units-*.parquet  1,009,592 units JOIN taxonomy labels
  taxonomy/taxonomy_v1.json            12 objects x 12 standards, definitions
  taxonomy/rhetoric_labels_analyst.csv 600 gold labels for the 6 inference forms
  README.md                            dataset card (written separately)

Chunked parquet (zstd) so nothing needs the full table in memory.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V2026 = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
VDIRECT = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
CHUNK = 250_000

UNITS_2026_SQL = """
SELECT u.unit_pk, u.paper_id, u.review_id, u.unit_index,
       u.inspected_object, u.observation, u.reasoning, u.judgment,
       u.valence, u.suggested_improvement, u.support_status, u.confidence,
       u.n_evidence_refs, u.n_missing_links,
       l.object_key, l.object_sim, l.reasoning_key AS standard_key, l.reasoning_sim AS standard_sim
FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk ORDER BY u.unit_pk
"""

UNITS_DIRECT_SQL = """
SELECT u.unit_pk, u.year, u.forum_id, u.reviewer_key, u.reviewer_role, u.unit_index,
       u.temporal_position, u.inspected_object, u.observation, u.reasoning, u.judgment,
       u.valence, u.suggested_improvement, u.update_trigger, u.judgment_change,
       u.support_status, u.confidence,
       l.object_key, l.object_sim, l.reasoning_key AS standard_key, l.reasoning_sim AS standard_sim
FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk ORDER BY u.unit_pk
"""


def dump_chunked(db: Path, sql: str, out_dir: Path, stem: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    total = 0
    for i, chunk in enumerate(pd.read_sql_query(sql, conn, chunksize=CHUNK)):
        table = pa.Table.from_pandas(chunk, preserve_index=False)
        pq.write_table(table, out_dir / f"{stem}-{i:05d}.parquet", compression="zstd")
        total += len(chunk)
        print(f"  {stem}-{i:05d}.parquet  {len(chunk):,} rows")
    conn.close()
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data/analysis/iclr/hf-dataset"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("review_level_2026/units")
    n = dump_chunked(V2026 / "units.sqlite3", UNITS_2026_SQL, out / "review_level_2026", "units")
    print(f"  total {n:,}")

    print("review_level_2026/reviews")
    conn = sqlite3.connect(f"file:{V2026 / 'units.sqlite3'}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT review_id, paper_id, n_units, n_unresolved_tensions, review_logic_summary"
        " FROM reviews ORDER BY review_id", conn)
    conn.close()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                   out / "review_level_2026/reviews.parquet", compression="zstd")
    print(f"  {len(df):,} rows")

    print("forum_level_2018_2026/units")
    n = dump_chunked(VDIRECT / "units.sqlite3", UNITS_DIRECT_SQL, out / "forum_level_2018_2026", "units")
    print(f"  total {n:,}")

    tax = out / "taxonomy"
    tax.mkdir(exist_ok=True)
    shutil.copy(V2026 / "taxonomy-v1.json", tax / "taxonomy_v1.json")
    gold = VDIRECT / "rhetoric-labels-analyst.csv"
    if gold.exists():
        shutil.copy(gold, tax / "rhetoric_labels_analyst.csv")
    print("taxonomy files copied")


if __name__ == "__main__":
    main()
