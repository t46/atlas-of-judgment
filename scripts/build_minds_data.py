"""Reviewer archetypes, rhetorical moves, and nine-year vitals (Direct track).

  minds    — k-means over per-reviewer reasoning-standard profiles
             (official reviewers with >= 5 units), k=5, seed 7; per cluster:
             centroid share vs corpus baseline, size, negativity, year mix
  rhetoric — ordered keyword reading of each unit's reasoning text into
             inference forms (counterfactual / obligation / alternative
             explanation / precedent-comparison / generalization / other);
             counts overall, by standard, by valence, and per year
  vitals   — per year: units per reviewer, post-response share, softening rate

Writes data/analysis/iclr/unit-taxonomy-direct-v1/minds-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
TAXONOMY = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/taxonomy-v1.json"
K = 5
MIN_UNITS = 5

MOVES = [
    ("counterfactual", re.compile(r"\bwithout\b|\bunless\b|\botherwise\b|\bin the absence of\b|\babsent\b", re.I)),
    ("obligation", re.compile(r"\bshould\b|\bmust\b|\brequires?\b|\brequired\b|\bneeds? to\b|\bexpected to\b|\bprerequisite\b|\bnecessary\b", re.I)),
    ("alternative_explanation", re.compile(r"\bcould be\b|\bmay (be|reflect|stem|arise)\b|\bmight (be|reflect)\b|\bartifact\b|\bconfound|\bmemoriz|\bleakage\b", re.I)),
    ("precedent_comparison", re.compile(r"\bcompared? (to|with)\b|\bprior (work|art|methods)\b|\bexisting (work|methods|literature|approaches)\b|\bstate.of.the.art\b|\bbaselines?\b|\bliterature\b", re.I)),
    ("generalization", re.compile(r"\bgeneraliz|\bbeyond\b|\bbroader\b|\bother (domains|settings|datasets|tasks)\b|\btransfer\b", re.I)),
]


def classify_move(text: str) -> str:
    for name, rx in MOVES:
        if rx.search(text):
            return name
    return "other"


def main() -> None:
    taxonomy = json.loads(TAXONOMY.read_text())
    rea_keys = [c["key"] for c in taxonomy["reasoning"]]
    idx = {k: i for i, k in enumerate(rea_keys)}

    conn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)

    # ---- reviewer profiles ----
    profiles: dict[str, np.ndarray] = {}
    meta: dict[str, list] = {}
    for rid, year, key, n, neg in conn.execute(
        "SELECT u.custom_id || '|' || u.reviewer_key, u.year, l.reasoning_key,"
        " COUNT(*), SUM(u.valence = 'negative')"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' GROUP BY 1, 3"
    ):
        vec = profiles.setdefault(rid, np.zeros(len(rea_keys)))
        vec[idx[key]] += n
        m = meta.setdefault(rid, [year, 0, 0])
        m[1] += n
        m[2] += neg
    rids = [r for r in profiles if meta[r][1] >= MIN_UNITS]
    X = np.stack([profiles[r] / profiles[r].sum() for r in rids])
    baseline = np.stack([profiles[r] for r in rids]).sum(axis=0)
    baseline = baseline / baseline.sum()
    print(f"{len(rids)} reviewers with >= {MIN_UNITS} units", flush=True)

    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=K, random_state=7, n_init=6)
    labels = km.fit_predict(X)

    minds = []
    for c in range(K):
        mask = labels == c
        members = [rids[i] for i in np.where(mask)[0]]
        prof = X[mask].mean(axis=0)
        units = sum(meta[r][1] for r in members)
        negs = sum(meta[r][2] for r in members)
        year_counts = Counter(meta[r][0] for r in members)
        minds.append(
            {
                "cluster": c,
                "reviewers": int(mask.sum()),
                "share": round(mask.sum() / len(rids), 4),
                "neg_rate": round(negs / units, 4),
                "profile": {k: round(float(prof[i]), 4) for i, k in enumerate(rea_keys)},
                "ratio": {k: round(float(prof[i] / baseline[i]), 3) for i, k in enumerate(rea_keys)},
                "year_counts": {str(y): year_counts.get(y, 0) for y in range(2018, 2027)},
            }
        )
    minds.sort(key=lambda m: -m["reviewers"])
    year_totals = Counter(meta[r][0] for r in rids)

    # ---- rhetoric ----
    move_overall = Counter()
    move_by_std: dict[str, Counter] = defaultdict(Counter)
    move_by_val: dict[str, Counter] = defaultdict(Counter)
    move_by_year: dict[str, Counter] = defaultdict(Counter)
    for key, valence, year, reasoning in conn.execute(
        "SELECT l.reasoning_key, u.valence, u.year, u.reasoning"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        mv = classify_move(reasoning)
        move_overall[mv] += 1
        move_by_std[key][mv] += 1
        move_by_val[mv][valence] += 1
        move_by_year[mv][year] += 1

    # ---- vitals ----
    vitals = {}
    for year, units, reviewers, post, soft, observed in conn.execute(
        "SELECT year, COUNT(*), COUNT(DISTINCT custom_id || '|' || reviewer_key),"
        " SUM(temporal_position = 'post_author_response'),"
        " SUM(temporal_position = 'post_author_response' AND judgment_change IN ('weakened','reversed')),"
        " SUM(temporal_position = 'post_author_response' AND judgment_change"
        "     IN ('weakened','reversed','strengthened','clarified'))"
        " FROM units GROUP BY 1 ORDER BY 1"
    ):
        vitals[str(year)] = {
            "units_per_reviewer": round(units / reviewers, 3),
            "post_share": round(post / units, 4),
            "soften_rate": round(soft / post, 4) if post else 0,
            "soften_rate_observed": round(soft / observed, 4) if observed else 0,
        }
    conn.close()

    payload = {
        "minds": {
            "k": K,
            "min_units": MIN_UNITS,
            "n_reviewers": len(rids),
            "baseline": {k: round(float(baseline[i]), 4) for i, k in enumerate(rea_keys)},
            "year_totals": {str(y): year_totals.get(y, 0) for y in range(2018, 2027)},
            "clusters": minds,
        },
        "rhetoric": {
            "overall": dict(move_overall),
            "by_standard": {k: dict(v) for k, v in move_by_std.items()},
            "by_valence": {k: dict(v) for k, v in move_by_val.items()},
            "by_year": {k: {str(y): v.get(y, 0) for y in range(2018, 2027)} for k, v in move_by_year.items()},
        },
        "vitals": vitals,
    }
    out = DIRECT_DIR / "minds-data.json"
    out.write_text(json.dumps(payload) + "\n")
    print(f"{out} written")
    for m in minds:
        top = sorted(m["ratio"].items(), key=lambda kv: -kv[1])[:3]
        print(f"cluster {m['cluster']}: {m['share']:.1%} of reviewers, neg {m['neg_rate']:.1%}, top: {top}")
    total = sum(move_overall.values())
    for mv, n in move_overall.most_common():
        print(f"move {mv:24s} {n/total:.1%}")


if __name__ == "__main__":
    main()
