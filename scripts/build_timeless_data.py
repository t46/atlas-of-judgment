"""The unamended code: is the law of novelty timeless?

Display aggregation of novelty-direct-raw.json: mean distance of each
year's normative novelty sentences to the 2026-induced code (and to the
2018-19 code), the split-half calibration, and the law-mix by year
under the reviewed naming map.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/timeless-data.json.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
SCRATCH = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/naming"
NOV_NAMES = {"diff": "The differentiation rule", "increment": "The increment rule",
             "combination": "The combination rule", "transfer": "The transfer rule",
             "claims": "The evidence rule (imported)", "venue": "The venue bar"}


def main() -> None:
    nov = json.loads((V / "novelty-direct-raw.json").read_text())
    nmap = json.loads((SCRATCH / "novname.json").read_text())["rule_merge"]

    mix = defaultdict(Counter)
    n_year = Counter()
    for _pk, yr, rank, _d in nov["assign"]:
        law = nmap[str(rank)]
        mix[yr][law] += 1
        n_year[yr] += 1

    laws = sorted(NOV_NAMES, key=lambda k: -sum(c[k] for c in mix.values()))

    # per-year bootstrap SE of the mean distance (sentence resampling; sentences
    # cluster within reviews, so these SEs are, if anything, optimistic)
    by_dist = defaultdict(list)
    for _pk, yr, _rank, d in nov["assign"]:
        by_dist[yr].append(d)
    rng = np.random.default_rng(7)
    dist_se = {}
    for yr, v in sorted(by_dist.items()):
        v = np.asarray(v)
        boots = np.array([v[rng.integers(0, len(v), len(v))].mean() for _ in range(1000)])
        dist_se[str(yr)] = round(float(boots.std()), 4)
    print("dist_se:", dist_se)
    payload = {
        "n_sents": nov["n_sents"],
        "n_units": nov["n_units"],
        "dist_to_2026": nov["dist_to_2026_code"],
        "dist_se": dist_se,
        "dist_to_2018": nov["dist_to_2018_code"],
        "control": nov["control"],
        "laws": [{"key": k, "name": NOV_NAMES[k]} for k in laws],
        "mix_by_year": {str(y): {k: round(mix[y][k] / n_year[y], 4) for k in laws}
                        for y in sorted(n_year)},
        "n_by_year": {str(y): n_year[y] for y in sorted(n_year)},
    }
    (V / "timeless-data.json").write_text(json.dumps(payload))
    print("mix by year:")
    for y in sorted(n_year):
        row = "  ".join(f"{k[:5]} {mix[y][k]/n_year[y]:.0%}" for k in laws)
        print(f"  {y}  n={n_year[y]:>5,}  {row}")


if __name__ == "__main__":
    main()
