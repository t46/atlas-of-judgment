"""Extract logic units from reviewer-logic-direct-qwen-full-v1 (2018-2026).

Same shape as extract_logic_units_2026.py but for the Direct track's
forum-level schema (reviewer_records nesting, temporal fields, year).
Only requests with status='complete' in the source state.sqlite3 are read
(outputs also exist for some failed rows — those are excluded).

Writes (new derived artifact, never touches sources):
  data/analysis/iclr/unit-taxonomy-direct-v1/units.sqlite3
  data/analysis/iclr/unit-taxonomy-direct-v1/manifest.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_RUN = PROJECT_ROOT / "data/analysis/iclr/reviewer-logic-direct-qwen-full-v1"
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

UNITS_DDL = """
CREATE TABLE IF NOT EXISTS units (
    unit_pk INTEGER PRIMARY KEY,
    custom_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    forum_id TEXT NOT NULL,
    reviewer_key TEXT NOT NULL,
    reviewer_role TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    temporal_position TEXT NOT NULL,
    inspected_object TEXT NOT NULL,
    observation TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    judgment TEXT NOT NULL,
    valence TEXT NOT NULL,
    suggested_improvement TEXT,
    update_trigger TEXT,
    judgment_change TEXT NOT NULL,
    support_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    UNIQUE (custom_id, reviewer_key, unit_id)
);
CREATE INDEX IF NOT EXISTS idx_dunits_year ON units(year);
"""


def complete_rows(state_path: Path) -> list[tuple[str, int]]:
    conn = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT custom_id, year FROM requests WHERE status='complete' ORDER BY custom_id"
        ).fetchall()
    finally:
        conn.close()
    return rows


def extract(limit: int | None = None) -> dict:
    rows = complete_rows(SOURCE_RUN / "state.sqlite3")
    if limit is not None:
        rows = rows[:limit]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUTPUT_DIR / "units.sqlite3")
    conn.executescript(UNITS_DDL)

    stats = {"forums": 0, "missing_output_files": 0, "reviewers": 0, "units": 0}
    batch: list[tuple] = []
    for custom_id, year in rows:
        path = SOURCE_RUN / "outputs" / f"{custom_id}.json"
        if not path.exists():
            stats["missing_output_files"] += 1
            continue
        record = json.loads(path.read_text())
        stats["forums"] += 1
        for reviewer in record["reviewer_records"]:
            stats["reviewers"] += 1
            for index, unit in enumerate(reviewer["logic_units"]):
                batch.append(
                    (
                        custom_id,
                        year,
                        record["forum_id"],
                        reviewer["reviewer_key"],
                        reviewer["reviewer_role"],
                        unit["unit_id"],
                        index,
                        unit["temporal_position"],
                        unit["inspected_object"],
                        unit["observation"],
                        unit["reasoning"],
                        unit["judgment"],
                        unit["valence"],
                        unit["suggested_improvement"],
                        unit["update_trigger"],
                        unit["judgment_change"],
                        unit["support_status"],
                        unit["confidence"],
                    )
                )
                stats["units"] += 1
        if len(batch) >= 50000:
            flush(conn, batch)
            batch = []
    flush(conn, batch)
    conn.close()

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run": str(SOURCE_RUN),
        "selection": "requests.status='complete' in source state.sqlite3",
        **stats,
    }
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def flush(conn: sqlite3.Connection, batch: list[tuple]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO units (custom_id, year, forum_id, reviewer_key,"
        " reviewer_role, unit_id, unit_index, temporal_position, inspected_object,"
        " observation, reasoning, judgment, valence, suggested_improvement,"
        " update_trigger, judgment_change, support_status, confidence)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        batch,
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="max forums (smoke test)")
    args = parser.parse_args()
    print(json.dumps(extract(limit=args.limit), indent=2))


if __name__ == "__main__":
    main()
