"""The archipelago: the 21 primary areas as islands on a sea chart.

Each area's criticism fingerprint (per-object deviation from the venue
baseline, in percentage points) is the island's identity: classical MDS
on euclidean distances between fingerprints places similar fields near
one another, and a few rounds of radius-aware repulsion keep islands
from overlapping. The fingerprint itself will shape each coastline in
the figure (each of the 12 objects is a compass direction; a promontory
grows toward the objects criticized more than baseline).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/archipelago-data.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"

OBJECTS = [
    "novelty", "problem_framing", "method_design", "theory",
    "empirical_scope", "baselines_ablations", "stats_metrics",
    "robustness_sensitivity", "compute_cost", "reproducibility",
    "clarity", "related_work",
]


def main() -> None:
    d = json.loads((V / "oracle-data.json").read_text())
    base = d["baseline"]["objects"]
    areas = sorted(d["areas"].items(), key=lambda kv: -kv[1]["n"])
    names = [a for a, _ in areas]
    F = np.array([[v["objects"][o] - base[o] for o in OBJECTS] for _, v in areas])

    # classical MDS
    D2 = ((F[:, None, :] - F[None, :, :]) ** 2).sum(-1)
    n = len(names)
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ D2 @ J
    w, U = np.linalg.eigh(B)
    idx = np.argsort(w)[::-1][:2]
    X = U[:, idx] * np.sqrt(np.maximum(w[idx], 0))

    # normalize to unit box
    X -= X.min(0)
    X /= X.max(0)

    # radius-aware repulsion (radii from sqrt(papers))
    ns = np.array([v["n"] for _, v in areas], dtype=float)
    R = 0.035 + np.sqrt(ns / ns.max()) * 0.075
    for _ in range(600):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dv = X[i] - X[j]
                dist = np.hypot(*dv) + 1e-9
                need = (R[i] + R[j]) * 1.06
                if dist < need:
                    push = dv / dist * (need - dist) / 2
                    X[i] += push
                    X[j] -= push
                    moved = True
        if not moved:
            break
    X -= X.min(0)
    X /= X.max(0)

    out = {
        "objects": OBJECTS,
        "baseline": {"accept": d["baseline"]["accept"], "objects": base},
        "islands": [
            {
                "name": names[i],
                "x": round(float(X[i, 0]), 4),
                "y": round(float(X[i, 1]), 4),
                "n": int(ns[i]),
                "accept": areas[i][1]["accept"],
                "dev": [round(float(F[i, k]), 4) for k in range(len(OBJECTS))],
            }
            for i in range(n)
        ],
    }
    path = V / "archipelago-data.json"
    path.write_text(json.dumps(out))
    print(f"{path} ({path.stat().st_size/1024:.1f} KB)")
    for isl in out["islands"]:
        top = sorted(zip(OBJECTS, isl["dev"]), key=lambda t: -abs(t[1]))[:2]
        print(f"  ({isl['x']:.2f},{isl['y']:.2f}) r~{np.sqrt(isl['n']/ns.max()):.2f}  {isl['name'][:38]:<38} " +
              " ".join(f"{o}{v:+.2f}" for o, v in top))


if __name__ == "__main__":
    main()
