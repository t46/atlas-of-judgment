"""Does the language of criticism drift 2018->2026? (exploratory, honest-null ok)

For each of three probe categories (novelty, empirical_scope, clarity):
sample up to 3k negative-judgment texts per year from the Direct track, embed
locally, and measure (a) centroid cosine distance between consecutive years
and 2018 vs 2026, (b) within-year dispersion (to normalize), and (c)
distinctive terms of the early era (2018-2020) vs the late era (2024-2026)
by log-odds. Prints everything; saves drift-language.json only if the signal
is clearly above the year-to-year noise floor.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
PER_YEAR = 3000
PROBES = ["novelty", "empirical_scope", "clarity"]


def main() -> None:
    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    rng = np.random.default_rng(7)
    report = {}
    for probe in PROBES:
        rows = conn.execute(
            "SELECT u.year, u.judgment FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE l.object_key = ? AND u.valence = 'negative' AND LENGTH(u.judgment) > 20",
            (probe,),
        ).fetchall()
        by_year = {}
        for y in range(2018, 2027):
            ts = [t for yy, t in rows if yy == y]
            if len(ts) < 400:
                continue
            idx = rng.permutation(len(ts))[:PER_YEAR]
            by_year[y] = [ts[i] for i in idx]
        cents, disp = {}, {}
        for y, ts in by_year.items():
            e = model.encode(ts, batch_size=256, normalize_embeddings=True)
            c = e.mean(0)
            cents[y] = c / np.linalg.norm(c)
            disp[y] = float(1 - (e @ (c / np.linalg.norm(c))).mean())
        years = sorted(cents)
        consec = [float(1 - cents[years[i]] @ cents[years[i + 1]]) for i in range(len(years) - 1)]
        span = float(1 - cents[years[0]] @ cents[years[-1]])
        noise = float(np.median(consec))
        print(f"\n== {probe}: years={years}")
        print(f"   consecutive-year centroid dist: {[round(c,4) for c in consec]}")
        print(f"   {years[0]}->{years[-1]} dist={span:.4f}  noise-floor(median consec)={noise:.4f}  ratio={span/max(noise,1e-9):.1f}x")
        # era terms
        def tokset(ts):
            c = Counter()
            for t in ts:
                c.update(set(re.findall(r"[a-z][a-z\-]{3,}", t.lower())))
            return c
        early = tokset(sum((by_year[y] for y in years if y <= 2020), []))
        late = tokset(sum((by_year[y] for y in years if y >= 2024), []))
        ne, nl = sum(early.values()), sum(late.values())
        lod = {w: np.log(((late[w] + 1) / nl) / ((early[w] + 1) / ne))
               for w in set(early) | set(late) if early[w] + late[w] > 200}
        top_late = sorted(lod, key=lambda w: -lod[w])[:10]
        top_early = sorted(lod, key=lambda w: lod[w])[:10]
        print(f"   late-era words : {', '.join(top_late)}")
        print(f"   early-era words: {', '.join(top_early)}")
        report[probe] = {"span": span, "noise": noise, "ratio": span / max(noise, 1e-9),
                         "late_terms": top_late, "early_terms": top_early}
    conn.close()
    strong = {p: r for p, r in report.items() if r["ratio"] >= 3}
    if strong:
        (V / "drift-language.json").write_text(json.dumps(report) + "\n")
        print("\nsignal strong enough; saved drift-language.json")
    else:
        print("\nno clean drift signal (span within ~noise floor); not saving")


if __name__ == "__main__":
    main()
