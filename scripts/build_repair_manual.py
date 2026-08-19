"""Name and merge the 22 repair clusters into the final repair taxonomy.

Human-curated mapping from k-means clusters (repair-raw.json, seed 7) to 16
named repair types; recomputes shares and per-object mixes from the sampled
assignments. Writes repair-manual.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"

GROUPS = {
    "Extend the evidence — more datasets, domains, models, scale": [1, 2, 19, 20],
    "Compare against stronger baselines": [14, 18],
    "Fix the surface — typos, notation, figures": [7, 13],
    "Measure the true cost — runtime, memory, GPU-hours": [9, 15],
    "Strengthen the theory — proofs, bounds, guarantees": [5],
    "Justify assumptions & scope the claims": [4],
    "Articulate the contribution": [8],
    "Release & document — code, data, repro details": [3],
    "Situate in the literature": [12],
    "Validate against humans & the real world": [16],
    "Show it on real visual data": [10],
    "Report error bars & significance": [17],
    "Ablate the components": [6],
    "Stress-test under attack & shift": [11],
    "Analyze the failures": [21],
    "Probe hyperparameter sensitivity": [0],
}

EXEMPLAR = {
    "Extend the evidence — more datasets, domains, models, scale": "Expand evaluation to additional datasets or tasks to demonstrate robustness and scalability.",
    "Compare against stronger baselines": "Compare against recent state-of-the-art methods.",
    "Fix the surface — typos, notation, figures": "Correct typos, add missing notation, and clarify figure captions.",
    "Measure the true cost — runtime, memory, GPU-hours": "Report inference-time savings and runtime measurements to demonstrate actual speed improvements.",
    "Strengthen the theory — proofs, bounds, guarantees": "Provide theoretical results on learnability or optimizability.",
    "Justify assumptions & scope the claims": "Justify the assumption or acknowledge its limitations.",
    "Articulate the contribution": "Articulate a clearer principle behind the method's design.",
    "Release & document — code, data, repro details": "Clarify reproducibility details, code release plans, and LLM usage.",
    "Situate in the literature": "Deepen engagement with specific prior works and clarify differentiation.",
    "Validate against humans & the real world": "Conduct a validation study comparing model outputs to human judgments.",
    "Show it on real visual data": "Investigate more tasks with real-world images to provide more convincing evidence.",
    "Report error bars & significance": "Report mean ± std or confidence intervals across multiple runs.",
    "Ablate the components": "Isolate components through ablation studies to demonstrate their individual contributions.",
    "Stress-test under attack & shift": "Demonstrate robustness across different model scales and under adversarial attack.",
    "Analyze the failures": "Identify and report specific failure modes or conditions where the approach does not work well.",
    "Probe hyperparameter sensitivity": "Conduct a sensitivity analysis to demonstrate robustness to hyperparameter variations.",
}


def main() -> None:
    raw = json.loads((V / "repair-raw.json").read_text())
    k2g = {}
    for g, ks in GROUPS.items():
        for k in ks:
            k2g[k] = g

    pk2cluster = dict((int(pk), int(k)) for pk, k in raw["assignments_sample"])
    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    obj_of = {}
    qmarks = ",".join("?" * 900)
    pks = list(pk2cluster)
    for i in range(0, len(pks), 900):
        chunk = pks[i:i + 900]
        for pk, o in conn.execute(
            f"SELECT unit_pk, object_key FROM unit_labels WHERE unit_pk IN ({','.join('?'*len(chunk))})", chunk):
            obj_of[pk] = o
    conn.close()

    g_n = Counter()
    g_obj = defaultdict(Counter)
    for pk, k in pk2cluster.items():
        g = k2g[k]
        g_n[g] += 1
        if pk in obj_of:
            g_obj[g][obj_of[pk]] += 1

    total = sum(g_n.values())
    groups = []
    for g, n in g_n.most_common():
        groups.append({
            "name": g, "share": round(n / total, 4),
            "exemplar": EXEMPLAR[g],
            "top_objects": [[o, round(c / n, 3)] for o, c in g_obj[g].most_common(3)],
        })
        print(f"{n/total:6.1%}  {g}")
    payload = {
        "n_units_with_improvement": raw["n_total_with_improvement"],
        "n_sampled": raw["n_sampled"],
        "groups": groups,
    }
    (V / "repair-manual.json").write_text(json.dumps(payload) + "\n")


if __name__ == "__main__":
    main()
