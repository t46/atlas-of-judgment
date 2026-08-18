"""Build viz-data.json for the evaluation-logic visualization artifact.

Aggregates unit_labels over all 410k units:
  - KPI counts
  - (object, reasoning, valence) triple counts for Sankey / heatmap / profiles
  - assignment-similarity distribution
  - a stratified sample of full reviews (raw logic-unit flows) for the explorer

Reads units.sqlite3 (+ papers table in data/processed/iclr/analysis.sqlite3 for
titles/decisions). Writes data/analysis/iclr/unit-taxonomy-2026-v1/viz-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
REVIEWS_PER_CATEGORY = 20
SIM_FLOOR = 0.75  # explorer sampling only: prefer confidently-assigned anchor units


def main() -> None:
    taxonomy = json.loads((OUTPUT_DIR / "taxonomy-v1.json").read_text())
    conn = sqlite3.connect(f"file:{OUTPUT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    labeled = conn.execute("SELECT COUNT(*) c FROM unit_labels").fetchone()["c"]
    total = conn.execute("SELECT COUNT(*) c FROM units").fetchone()["c"]
    if labeled != total:
        raise RuntimeError(f"labels incomplete: {labeled}/{total}")

    kpi = {
        "units": total,
        "reviews": conn.execute("SELECT COUNT(*) c FROM reviews").fetchone()["c"],
        "papers": conn.execute("SELECT COUNT(DISTINCT paper_id) c FROM units").fetchone()["c"],
    }

    triples = [
        dict(row)
        for row in conn.execute(
            "SELECT l.object_key o, l.reasoning_key r, u.valence v, COUNT(*) n"
            " FROM unit_labels l JOIN units u ON u.unit_pk = l.unit_pk"
            " GROUP BY 1, 2, 3"
        )
    ]

    # per-object-category extras: how often criticism comes with a concrete fix,
    # and how often the unit is grounded in the reviewer's explicit words
    obj_extras = {
        row["k"]: {
            "n": row["n"],
            "with_improvement": row["imp"],
            "reviewer_explicit": row["expl"],
        }
        for row in conn.execute(
            "SELECT l.object_key k, COUNT(*) n,"
            " SUM(u.suggested_improvement IS NOT NULL) imp,"
            " SUM(u.support_status = 'reviewer_explicit') expl"
            " FROM unit_labels l JOIN units u ON u.unit_pk = l.unit_pk"
            " GROUP BY 1"
        )
    }

    sims = conn.execute(
        "SELECT SUM(object_sim >= 0.75) o_hi, SUM(reasoning_sim >= 0.75) r_hi,"
        " AVG(object_sim) o_avg, AVG(reasoning_sim) r_avg FROM unit_labels"
    ).fetchone()
    confidence = {
        "object_share_ge_075": round(sims["o_hi"] / total, 4),
        "reasoning_share_ge_075": round(sims["r_hi"] / total, 4),
        "object_mean_sim": round(sims["o_avg"], 4),
        "reasoning_mean_sim": round(sims["r_avg"], 4),
    }

    # Stratified explorer sample: for each object category, reviews anchored by a
    # confidently-assigned unit of that category (dedup across categories).
    seen_reviews: set[str] = set()
    sample_review_ids: list[str] = []
    for category in taxonomy["inspected_object"]:
        rows = conn.execute(
            "SELECT DISTINCT u.review_id FROM unit_labels l"
            " JOIN units u ON u.unit_pk = l.unit_pk"
            " WHERE l.object_key = ? AND l.object_sim >= ?"
            " ORDER BY u.review_id LIMIT ?",
            (category["key"], SIM_FLOOR, REVIEWS_PER_CATEGORY * 3),
        ).fetchall()
        picked = 0
        for row in rows:
            if picked >= REVIEWS_PER_CATEGORY:
                break
            if row["review_id"] in seen_reviews:
                continue
            seen_reviews.add(row["review_id"])
            sample_review_ids.append(row["review_id"])
            picked += 1

    titles: dict[str, dict] = {}
    if ANALYSIS_DB.exists():
        aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
        aconn.row_factory = sqlite3.Row
        for row in aconn.execute(
            "SELECT forum_id, title, decision FROM papers WHERE year = 2026"
        ):
            titles[row["forum_id"]] = {"title": row["title"], "decision": row["decision"]}
        aconn.close()

    sample_reviews = []
    for review_id in sample_review_ids:
        meta = conn.execute(
            "SELECT paper_id, review_logic_summary FROM reviews WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        units = [
            dict(row)
            for row in conn.execute(
                "SELECT u.unit_index, u.inspected_object, u.observation, u.reasoning,"
                " u.judgment, u.valence, u.suggested_improvement, u.support_status,"
                " u.confidence, l.object_key, l.object_sim, l.reasoning_key, l.reasoning_sim"
                " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
                " WHERE u.review_id = ? ORDER BY u.unit_index",
                (review_id,),
            )
        ]
        paper = titles.get(meta["paper_id"], {})
        sample_reviews.append(
            {
                "review_id": review_id,
                "paper_id": meta["paper_id"],
                "paper_title": paper.get("title"),
                "decision": paper.get("decision"),
                "summary": meta["review_logic_summary"],
                "units": units,
            }
        )
    conn.close()

    payload = {
        "kpi": kpi,
        "taxonomy": taxonomy,
        "triples": triples,
        "obj_extras": obj_extras,
        "confidence": confidence,
        "sample_reviews": sample_reviews,
    }
    out = OUTPUT_DIR / "viz-data.json"
    out.write_text(json.dumps(payload, ensure_ascii=False) + "\n")
    size_mb = out.stat().st_size / 1e6
    print(
        f"{out} written: {len(triples)} triples, {len(sample_reviews)} sample reviews,"
        f" {size_mb:.1f} MB"
    )


if __name__ == "__main__":
    main()
