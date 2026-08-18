"""Panel analyses: rebuttal dynamics, decisions, meta-reviewers, dissent, reliability.

  A. rebuttal  — judgment_change among post_author_response units (Direct),
                 per object category, and by update_trigger keyword bucket
  B. tribunal  — accept rate of papers carrying >=1 negative unit per category
                 (compact 2026 x papers.decision; withdrawn/undecided excluded)
  C. meta      — category profile and valence mix, meta vs official reviewers
  D. dissent   — per category: share of forums where >=2 reviewers took
                 opposite valence stances on it (Direct, official reviewers)
  F. reliability — memo_inferred share per category x year (Direct)

Writes data/analysis/iclr/unit-taxonomy-direct-v1/panel-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
COMPACT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

TRIGGER_BUCKETS = [
    ("new_evidence", re.compile(r"experiment|ablation|result|table|figure|benchmark|data|empirical", re.I)),
    ("revision", re.compile(r"revis|updated|rewrit|added|new section|incorporat", re.I)),
    ("clarification", re.compile(r"clarif|explain|explan|justif|response|rebuttal|argu|answer", re.I)),
    ("commitment", re.compile(r"promise|will add|will include|future|camera[- ]ready", re.I)),
]


def bucket_trigger(text: str | None) -> str:
    if not text:
        return "unstated"
    for name, rx in TRIGGER_BUCKETS:
        if rx.search(text):
            return name
    return "other"


def main() -> None:
    dconn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    dconn.row_factory = sqlite3.Row

    # ---- A. rebuttal ----
    overall = Counter()
    per_cat: dict[str, Counter] = defaultdict(Counter)
    trig: dict[str, Counter] = defaultdict(Counter)
    for row in dconn.execute(
        "SELECT l.object_key k, u.judgment_change c, u.update_trigger t"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.temporal_position = 'post_author_response'"
    ):
        overall[row["c"]] += 1
        per_cat[row["k"]][row["c"]] += 1
        if row["c"] in ("strengthened", "weakened", "reversed", "clarified"):
            trig[bucket_trigger(row["t"])][row["c"]] += 1
    rebuttal = {
        "post_units": sum(overall.values()),
        "overall": dict(overall),
        "per_category": {
            k: {
                "n": sum(c.values()),
                "softened": c["weakened"] + c["reversed"],
                "strengthened": c["strengthened"],
            }
            for k, c in per_cat.items()
        },
        "triggers": {k: dict(v) for k, v in trig.items()},
    }

    # ---- C. meta vs official ----
    role_cat: dict[str, Counter] = {"official_reviewer": Counter(), "meta_reviewer": Counter()}
    role_val: dict[str, Counter] = {"official_reviewer": Counter(), "meta_reviewer": Counter()}
    for row in dconn.execute(
        "SELECT u.reviewer_role r, l.object_key k, u.valence v, COUNT(*) n"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk GROUP BY 1, 2, 3"
    ):
        role_cat[row["r"]][row["k"]] += row["n"]
        role_val[row["r"]][row["v"]] += row["n"]
    meta = {
        role: {
            "n": sum(cats.values()),
            "cat_share": {k: round(v / sum(cats.values()), 4) for k, v in cats.items()},
            "val_share": {k: round(v / sum(role_val[role].values()), 4) for k, v in role_val[role].items()},
        }
        for role, cats in role_cat.items()
    }

    # ---- D. dissent ----
    stance: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in dconn.execute(
        "SELECT u.custom_id f, l.object_key k, u.reviewer_key r,"
        " SUM(u.valence = 'negative') neg, SUM(u.valence = 'positive') pos"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer'"
        " GROUP BY 1, 2, 3"
    ):
        s = stance[(row["f"], row["k"])][row["r"]]
        s[0] += row["neg"]
        s[1] += row["pos"]
    dissent_hit = Counter()
    dissent_base = Counter()
    for (_f, cat), reviewers in stance.items():
        signs = set()
        for neg, pos in reviewers.values():
            if neg > pos:
                signs.add("-")
            elif pos > neg:
                signs.add("+")
        if len(reviewers) >= 2:
            dissent_base[cat] += 1
            if "+" in signs and "-" in signs:
                dissent_hit[cat] += 1
    dissent = {
        k: {"contested": dissent_hit[k], "base": dissent_base[k]}
        for k in dissent_base
    }

    # ---- F. reliability (memo_inferred share per category x year) ----
    years = [r[0] for r in dconn.execute("SELECT DISTINCT year FROM units ORDER BY year")]
    rel_grid: dict[str, dict[int, list[int]]] = defaultdict(lambda: {y: [0, 0] for y in years})
    for row in dconn.execute(
        "SELECT u.year y, l.object_key k, COUNT(*) n,"
        " SUM(u.support_status = 'memo_inferred') inf"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk GROUP BY 1, 2"
    ):
        rel_grid[row["k"]][row["y"]] = [row["inf"], row["n"]]
    reliability = {
        "years": years,
        "memo_inferred_share": {
            k: [round(g[y][0] / max(1, g[y][1]), 4) for y in years] for k, g in rel_grid.items()
        },
    }
    dconn.close()

    # ---- B. tribunal (compact 2026 x decisions) ----
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    decision = {}
    for forum_id, dec in aconn.execute(
        "SELECT forum_id, decision FROM papers WHERE year = 2026 AND decision IS NOT NULL"
        " AND withdrawn = 0"
    ):
        decision[forum_id] = 1 if "accept" in dec.lower() else 0
    aconn.close()

    cconn = sqlite3.connect(f"file:{COMPACT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    paper_negcats: dict[str, set] = defaultdict(set)
    papers_with_units = set()
    for paper_id, cat in cconn.execute(
        "SELECT DISTINCT u.paper_id, l.object_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk WHERE u.valence = 'negative'"
    ):
        paper_negcats[paper_id].add(cat)
    for (paper_id,) in cconn.execute("SELECT DISTINCT paper_id FROM units"):
        papers_with_units.add(paper_id)
    cconn.close()

    decided = [p for p in papers_with_units if p in decision]
    base_accept = sum(decision[p] for p in decided) / len(decided)
    tribunal = {"n_papers": len(decided), "base_accept": round(base_accept, 4), "per_category": {}}
    all_cats = sorted({c for cats in paper_negcats.values() for c in cats})
    for cat in all_cats:
        with_neg = [p for p in decided if cat in paper_negcats.get(p, ())]
        without = [p for p in decided if cat not in paper_negcats.get(p, ())]
        tribunal["per_category"][cat] = {
            "n_with": len(with_neg),
            "accept_with": round(sum(decision[p] for p in with_neg) / max(1, len(with_neg)), 4),
            "accept_without": round(sum(decision[p] for p in without) / max(1, len(without)), 4),
        }

    # ---- gauntlet: breadth of criticism vs survival ----
    gauntlet = []
    for breadth in range(0, 10):
        papers = [
            p for p in decided
            if min(len(paper_negcats.get(p, ())), 9) == breadth
        ]
        if not papers:
            continue
        gauntlet.append(
            {
                "breadth": breadth,
                "n": len(papers),
                "accept": round(sum(decision[p] for p in papers) / len(papers), 4),
            }
        )

    payload = {
        "rebuttal": rebuttal,
        "gauntlet": gauntlet,
        "tribunal": tribunal,
        "meta": meta,
        "dissent": dissent,
        "reliability": reliability,
    }
    out = DIRECT_DIR / "panel-data.json"
    out.write_text(json.dumps(payload) + "\n")
    print(f"{out} written")


if __name__ == "__main__":
    main()
