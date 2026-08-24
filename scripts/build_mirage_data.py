"""The archetype mirage — real reviewer roses vs multinomial-null roses.

Rebuilds, as a permanent instrument, the null experiment that deflated the
Kinds plate (deleted 2026-08-21; notes/weak-results-bank.md): cluster the
real per-reviewer reasoning-standard profiles (official reviewers, >= 5
units, one profile per reviewer per paper — same universe and k-means
settings as build_minds_data.py, k=5 seed 7), then draw SYNTHETIC profiles
i.i.d. from the single shared field mix at each reviewer's own unit count
(multinomial, seed 46) and push them through the identical pipeline. If the
five petalled "kinds" reappear from pure noise, the roses were mostly what
k-means does to small multinomial samples.

Also emits the two honesty numbers: per-standard overdispersion (real
variance over expected multinomial variance — how much genuine reviewer
signature exists above noise) and k-means explained variance, real vs null.

Writes data/analysis/iclr/unit-taxonomy-direct-v1/mirage-data.json.
Run: uv run python scripts/build_mirage_data.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
TAXONOMY = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/taxonomy-v1.json"
K = 5
MIN_UNITS = 5
NULL_SEED = 46


def cluster_summary(X, baseline, rea_keys, seed=7):
    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=K, random_state=seed, n_init=6)
    labels = km.fit_predict(X)
    sst = ((X - X.mean(axis=0)) ** 2).sum()
    ev = 1 - km.inertia_ / sst
    out = []
    for c in range(K):
        mask = labels == c
        prof = X[mask].mean(axis=0)
        ratio = {k: round(float(prof[i] / baseline[i]), 3) for i, k in enumerate(rea_keys)}
        top = max(ratio.items(), key=lambda kv: kv[1])
        out.append({
            "share": round(mask.sum() / len(X), 4),
            "ratio": ratio,
            "top": [top[0], top[1]],
        })
    out.sort(key=lambda m: -m["share"])
    return out, float(ev)


def main() -> None:
    taxonomy = json.loads(TAXONOMY.read_text())
    rea_keys = [c["key"] for c in taxonomy["reasoning"]]
    idx = {k: i for i, k in enumerate(rea_keys)}

    conn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    profiles: dict[str, np.ndarray] = {}
    for rid, key, n in conn.execute(
        "SELECT u.custom_id || '|' || u.reviewer_key, l.reasoning_key, COUNT(*)"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' GROUP BY 1, 2"
    ):
        profiles.setdefault(rid, np.zeros(len(rea_keys)))[idx[key]] += n
    conn.close()

    counts = np.stack([v for v in profiles.values() if v.sum() >= MIN_UNITS])
    ns = counts.sum(axis=1)
    X = counts / ns[:, None]
    baseline = counts.sum(axis=0) / counts.sum()
    print(f"{len(counts):,} reviewer-paper profiles with >= {MIN_UNITS} units")

    real, ev_real = cluster_summary(X, baseline, rea_keys)

    # overdispersion: real variance of per-profile shares over the variance
    # a pure multinomial at each profile's own volume would produce
    od = []
    for i in range(len(rea_keys)):
        exp_var = float(np.mean(baseline[i] * (1 - baseline[i]) / ns))
        od.append(float(X[:, i].var() / exp_var))
    od = np.array(od)

    rng = np.random.default_rng(NULL_SEED)
    Xn = np.stack([rng.multinomial(int(n), baseline) / n for n in ns])
    null, ev_null = cluster_summary(Xn, baseline, rea_keys)

    payload = {
        "n_profiles": int(len(counts)),
        "min_units": MIN_UNITS,
        "null_seed": NULL_SEED,
        "baseline": {k: round(float(baseline[i]), 4) for i, k in enumerate(rea_keys)},
        "ev_real": round(ev_real, 4),
        "ev_null": round(ev_null, 4),
        "overdisp_median": round(float(np.median(od)), 3),
        "overdisp_max": round(float(od.max()), 3),
        "real": real,
        "null": null,
    }
    (DIRECT_DIR / "mirage-data.json").write_text(json.dumps(payload))
    print(f"EV real {ev_real:.1%} vs null {ev_null:.1%}")
    print(f"overdispersion median {np.median(od):.2f} max {od.max():.2f}")
    print("real tops:", [(m["top"][0], m["top"][1], m["share"]) for m in real])
    print("null tops:", [(m["top"][0], m["top"][1], m["share"]) for m in null])


if __name__ == "__main__":
    main()
