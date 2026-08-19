"""The Oracle: conditional criticism profiles by primary area (ICLR 2026).

For each primary_area with >= 120 decided papers: the share of papers that drew
at least one negative unit on each object of scrutiny, the acceptance rate
among decided non-withdrawn papers, the mean rating, and the same figures for
the whole venue as baseline. Pure conditional frequencies — association only.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/oracle-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

SHORT = {
    "applications to computer vision, audio, language, and other modalities": "CV / audio / language applications",
    "foundation or frontier models, including LLMs": "foundation & frontier models (LLMs)",
    "generative models": "generative models",
    "alignment, fairness, safety, privacy, and societal considerations": "alignment, fairness & safety",
    "datasets and benchmarks": "datasets & benchmarks",
    "unsupervised, self-supervised, semi-supervised, and supervised representation learning": "representation learning",
    "reinforcement learning": "reinforcement learning",
    "applications to physical sciences (physics, chemistry, biology, etc.)": "physical-science applications",
    "learning on graphs and other geometries & topologies": "graphs & geometry",
    "applications to neuroscience & cognitive science": "neuroscience & cognitive science",
    "causal reasoning": "causal reasoning",
    "learning theory": "learning theory",
    "probabilistic methods (Bayesian methods, variational inference, sampling, UQ, etc.)": "probabilistic methods",
    "optimization": "optimization",
    "societal considerations including fairness, safety, privacy": "societal considerations",
    "infrastructure, software libraries, hardware, systems, etc.": "infrastructure & systems",
    "neurosymbolic & hybrid AI systems (physics-informed, logic & formal reasoning, etc.)": "neurosymbolic & hybrid AI",
    "interpretability and explainable AI": "interpretability & XAI",
    "applications to robotics, autonomy, planning": "robotics, autonomy & planning",
    "other topics in machine learning (i.e., none of the above)": "other topics",
    "transfer learning, meta learning, and lifelong learning": "transfer / meta / lifelong learning",
}


def main() -> None:
    taxonomy = json.loads((V / "taxonomy-v1.json").read_text())
    obj_keys = [c["key"] for c in taxonomy["inspected_object"]]

    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    meta: dict[str, dict] = {}
    for forum_id, cj, decision, withdrawn in aconn.execute(
        "SELECT forum_id, content_json, decision, withdrawn FROM papers WHERE year = 2026"
    ):
        try:
            c = json.loads(cj)
        except json.JSONDecodeError:
            continue
        area = c.get("primary_area")
        if not isinstance(area, str):
            continue
        meta[forum_id] = {"area": area, "decision": decision, "withdrawn": bool(withdrawn)}
    aconn.close()

    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    paper_neg: dict[str, set] = defaultdict(set)
    for paper_id, obj in conn.execute(
        "SELECT DISTINCT u.paper_id, l.object_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk WHERE u.valence = 'negative'"
    ):
        paper_neg[paper_id].add(obj)
    reviewed = {r[0] for r in conn.execute("SELECT DISTINCT paper_id FROM reviews")}
    conn.close()

    def profile(pids):
        decided = [p for p in pids if meta[p]["decision"] and not meta[p]["withdrawn"]]
        acc = [p for p in decided if "accept" in (meta[p]["decision"] or "").lower()
               or any(w in (meta[p]["decision"] or "").lower() for w in ("oral", "poster", "spotlight"))]
        return {
            "n": len(pids),
            "n_decided": len(decided),
            "accept": round(len(acc) / len(decided), 4) if decided else None,
            "objects": {o: round(sum(1 for p in pids if o in paper_neg[p]) / len(pids), 4) for o in obj_keys},
        }

    by_area: dict[str, list] = defaultdict(list)
    for pid in reviewed:
        if pid in meta:
            by_area[meta[pid]["area"]].append(pid)

    areas = {}
    for area, pids in sorted(by_area.items(), key=lambda kv: -len(kv[1])):
        if len(pids) < 120:
            continue
        areas[SHORT.get(area, area[:48])] = profile(pids)
    baseline = profile([p for p in reviewed if p in meta])

    payload = {"baseline": baseline, "areas": areas}
    (V / "oracle-data.json").write_text(json.dumps(payload) + "\n")
    print(f"{len(areas)} areas; baseline n={baseline['n']} accept={baseline['accept']}")
    for a, pr in list(areas.items())[:6]:
        top = max(pr["objects"], key=pr["objects"].get)
        print(f"  {a[:42]:44s} n={pr['n']:5d} acc={pr['accept']} top-charge={top} {pr['objects'][top]:.0%}")


if __name__ == "__main__":
    main()
