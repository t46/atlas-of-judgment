"""Extract logic units from review-logic-qwen-2026-full into a flat working DB.

Reads (read-only):
  data/analysis/iclr/review-logic-qwen-2026-full/state.sqlite3   (complete shard set)
  data/analysis/iclr/review-logic-qwen-2026-full/outputs/*.jsonl (structured records)

Writes (new derived artifact, never touches sources):
  data/analysis/iclr/unit-taxonomy-2026-v1/units.sqlite3
  data/analysis/iclr/unit-taxonomy-2026-v1/manifest.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_RUN = PROJECT_ROOT / "data/analysis/iclr/review-logic-qwen-2026-full"
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"

UNITS_DDL = """
CREATE TABLE IF NOT EXISTS units (
    unit_pk INTEGER PRIMARY KEY,
    shard INTEGER NOT NULL,
    paper_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    inspected_object TEXT NOT NULL,
    observation TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    judgment TEXT NOT NULL,
    valence TEXT NOT NULL,
    suggested_improvement TEXT,
    support_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    n_evidence_refs INTEGER NOT NULL,
    n_missing_links INTEGER NOT NULL,
    UNIQUE (review_id, unit_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    shard INTEGER NOT NULL,
    paper_id TEXT NOT NULL,
    n_units INTEGER NOT NULL,
    n_unresolved_tensions INTEGER NOT NULL,
    review_logic_summary TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_units_paper ON units(paper_id);
CREATE INDEX IF NOT EXISTS idx_units_valence ON units(valence);
"""


def complete_shards(state_path: Path) -> list[int]:
    conn = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT shard FROM requests WHERE status='complete' ORDER BY shard"
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def extract(limit: int | None = None) -> dict:
    shards = complete_shards(SOURCE_RUN / "state.sqlite3")
    if limit is not None:
        shards = shards[:limit]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db_path = OUTPUT_DIR / "units.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(UNITS_DDL)

    stats = {"shards": 0, "missing_output_files": 0, "reviews": 0, "units": 0}
    unit_rows: list[tuple] = []
    review_rows: list[tuple] = []
    for shard in shards:
        path = SOURCE_RUN / "outputs" / f"compact-shard-{shard:05d}.jsonl"
        if not path.exists():
            stats["missing_output_files"] += 1
            continue
        stats["shards"] += 1
        with path.open() as handle:
            for line in handle:
                record = json.loads(line)
                review_id = record["review_id"]
                review_rows.append(
                    (
                        review_id,
                        shard,
                        record["paper_id"],
                        len(record["logic_units"]),
                        len(record["unresolved_tensions"]),
                        record["review_logic_summary"],
                    )
                )
                stats["reviews"] += 1
                for index, unit in enumerate(record["logic_units"]):
                    unit_rows.append(
                        (
                            shard,
                            record["paper_id"],
                            review_id,
                            unit["unit_id"],
                            index,
                            unit["inspected_object"],
                            unit["observation"],
                            unit["reasoning"],
                            unit["judgment"],
                            unit["valence"],
                            unit["suggested_improvement"],
                            unit["support_status"],
                            unit["confidence"],
                            len(unit["evidence_refs"]),
                            len(unit["missing_links"]),
                        )
                    )
                    stats["units"] += 1
        if len(unit_rows) >= 50000:
            flush(conn, unit_rows, review_rows)
            unit_rows, review_rows = [], []
    flush(conn, unit_rows, review_rows)
    conn.commit()
    conn.close()

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run": str(SOURCE_RUN),
        "selection": "requests.status='complete' in source state.sqlite3",
        "source_complete_shards": len(shards),
        **stats,
    }
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def flush(conn: sqlite3.Connection, unit_rows: list[tuple], review_rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?)", review_rows
    )
    conn.executemany(
        "INSERT OR REPLACE INTO units (shard, paper_id, review_id, unit_id, unit_index,"
        " inspected_object, observation, reasoning, judgment, valence,"
        " suggested_improvement, support_status, confidence, n_evidence_refs,"
        " n_missing_links) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        unit_rows,
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="max shards (smoke test)")
    args = parser.parse_args()
    manifest = extract(limit=args.limit)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
