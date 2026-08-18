"""Name the constellations: annotate the galaxy with data-derived region labels.

KMeans over the 40k 2D points -> for each region, a c-TF-IDF label from the
inspected_object snippets, a convex hull of its dense core, and its majority
object category. Merged back into galaxy.json under "regions".
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
K = 22
STOP = set(
    """the a an of and or to in on for with without its their as by is are be
    paper papers method methods proposed approach model models experiment
    experiments experimental results result work section claim claims
    performance evaluation analysis between against across regarding
    specific overall quality lack missing use used using""".split()
)


def tokens(text: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[a-z][a-z\-]{2,}", text.lower())
        if t not in STOP
    ]


def main() -> None:
    galaxy = json.loads((OUTPUT_DIR / "galaxy.json").read_text())
    pts = galaxy["points"]
    xy = np.array([[p[0], p[1]] for p in pts], dtype=float)

    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=K, random_state=7, n_init=4)
    labels = km.fit_predict(xy)

    # c-TF-IDF: term counts per cluster vs document frequency across clusters
    cluster_terms = [Counter() for _ in range(K)]
    for p, c in zip(pts, labels):
        cluster_terms[c].update(set(tokens(p[5])))
    df = Counter()
    for ct in cluster_terms:
        df.update(ct.keys())

    from scipy.spatial import ConvexHull

    regions = []
    for c in range(K):
        idx = np.where(labels == c)[0]
        if len(idx) < 200:
            continue
        center = km.cluster_centers_[c]
        d = np.linalg.norm(xy[idx] - center, axis=1)
        core = idx[np.argsort(d)[: max(50, int(len(idx) * 0.6))]]
        hull_pts = xy[core][ConvexHull(xy[core]).vertices]
        n = len(idx)
        scored = sorted(
            cluster_terms[c].items(),
            key=lambda kv: -(kv[1] / n) * np.log(K / (1 + df[kv[0]])),
        )
        name = " · ".join(t for t, _ in scored[:3])
        maj = Counter(pts[i][2] for i in idx).most_common(1)[0][0]
        regions.append(
            {
                "name": name,
                "x": round(float(center[0])),
                "y": round(float(center[1])),
                "n": int(n),
                "obj": int(maj),
                "hull": [[round(float(x)), round(float(y))] for x, y in hull_pts],
            }
        )
    regions.sort(key=lambda r: -r["n"])
    galaxy["regions"] = regions
    (OUTPUT_DIR / "galaxy.json").write_text(json.dumps(galaxy, ensure_ascii=False) + "\n")
    for r in regions:
        print(f"{r['n']:6d}  {r['name']}")


if __name__ == "__main__":
    main()
