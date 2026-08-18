"""Nine years of drift: per-year aggregates over the Direct track (2018-2026).

For each year: object-category shares, reasoning-standard shares, valence mix,
units per reviewer, and mean assignment similarity (an honesty metric for the
cross-track taxonomy transfer).

Writes data/analysis/iclr/unit-taxonomy-direct-v1/drift-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"


def main() -> None:
    conn = sqlite3.connect(f"file:{OUTPUT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    years = [r["year"] for r in conn.execute("SELECT DISTINCT year FROM units ORDER BY year")]

    def by_year(sql: str) -> dict[int, dict[str, int]]:
        out: dict[int, dict[str, int]] = {y: {} for y in years}
        for row in conn.execute(sql):
            out[row["y"]][row["k"]] = row["n"]
        return out

    obj = by_year(
        "SELECT u.year y, l.object_key k, COUNT(*) n FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk GROUP BY 1, 2"
    )
    rea = by_year(
        "SELECT u.year y, l.reasoning_key k, COUNT(*) n FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk GROUP BY 1, 2"
    )
    val = by_year("SELECT year y, valence k, COUNT(*) n FROM units GROUP BY 1, 2")

    meta = {}
    for row in conn.execute(
        "SELECT u.year y, COUNT(*) n,"
        " COUNT(DISTINCT u.custom_id || '|' || u.reviewer_key) reviewers,"
        " COUNT(DISTINCT u.custom_id) forums,"
        " AVG(l.object_sim) o_sim, AVG(l.reasoning_sim) r_sim,"
        " SUM(u.judgment_change = 'reversed') rev,"
        " SUM(u.judgment_change = 'weakened') weak,"
        " SUM(u.judgment_change = 'strengthened') strong,"
        " SUM(u.temporal_position = 'post_author_response') post"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk GROUP BY 1"
    ):
        meta[row["y"]] = {
            "units": row["n"],
            "reviewers": row["reviewers"],
            "forums": row["forums"],
            "units_per_reviewer": round(row["n"] / row["reviewers"], 2),
            "object_mean_sim": round(row["o_sim"], 4),
            "reasoning_mean_sim": round(row["r_sim"], 4),
            "reversed": row["rev"],
            "weakened": row["weak"],
            "strengthened": row["strong"],
            "post_response_units": row["post"],
        }
    conn.close()

    def shares(counts: dict[int, dict[str, int]]) -> dict[str, list[float]]:
        keys = sorted({k for d in counts.values() for k in d})
        return {
            k: [round(counts[y].get(k, 0) / max(1, sum(counts[y].values())), 4) for y in years]
            for k in keys
        }

    payload = {
        "years": years,
        "object_share": shares(obj),
        "reasoning_share": shares(rea),
        "valence_share": shares(val),
        "meta": {str(y): meta[y] for y in years},
    }
    out = OUTPUT_DIR / "drift-data.json"
    out.write_text(json.dumps(payload) + "\n")
    print(f"{out} written")
    for y in years:
        m = meta[y]
        neg = val[y].get("negative", 0) / m["units"]
        print(f"{y}: units={m['units']:>7} u/rev={m['units_per_reviewer']:>5} neg={neg:.1%} sim={m['object_mean_sim']:.3f}/{m['reasoning_mean_sim']:.3f}")


if __name__ == "__main__":
    main()
