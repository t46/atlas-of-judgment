"""The elements, unified: all twelve objects in one schema.

Joins elements-all-raw.json (per-unit sentence/fix cluster assignments)
with the reviewed naming maps (naming-group{1,2,3}.json) and folds in
the novelty pilot (elements-novelty.json). Each law is tagged with its
home docket — a law whose home is another object is a VISITING law,
generalizing the imported-evidence-rule observation.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/elements-all.json.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
SCRATCH = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/naming"

# where a law "lives"; a law appearing in another object's docket is visiting
HOME = {
    "novelty": "novelty", "diff": "novelty", "increment": "novelty",
    "combination": "novelty", "transfer": "novelty", "venue": "novelty",
    "clarity": "clarity", "notation": "clarity", "visualization": "clarity",
    "baseline": "baselines_ablations", "comparison": "baselines_ablations",
    "fair_comparison": "baselines_ablations", "ablation": "baselines_ablations",
    "reporting": "reproducibility", "disclosure": "reproducibility",
    "specification": "reproducibility", "code": "reproducibility",
    "evidence_imported": "__evidence__",
}


def home_of(key: str, obj: str) -> str | None:
    for stem, home in HOME.items():
        if key.startswith(stem):
            return home
    return obj


def main() -> None:
    raw = json.loads((V / "elements-all-raw.json").read_text())
    naming = {}
    for g in (1, 2, 3):
        naming.update(json.loads((SCRATCH / f"naming-group{g}.json").read_text()))
    graw = json.loads((V / "grounds-all-raw.json").read_text())
    gnaming = {}
    for g in (1, 2, 3):
        gnaming.update(json.loads((SCRATCH / f"grounds-group{g}.json").read_text()))

    out = {}
    for obj, nm in naming.items():
        r = raw[obj]
        n = r["n_units"]
        # laws: per-unit multi-membership from sentence assignments
        laws_of = defaultdict(set)
        for pk, rank in r["sent_assign"]:
            laws_of[pk].add(nm["rule_merge"][str(rank)])
        share = Counter()
        for pks in laws_of.values():
            for law in pks:
                share[law] += 1
        # quotes: exemplar from the largest cluster mapped to each law
        quote = {}
        for rank in range(12):
            law = nm["rule_merge"][str(rank)]
            if law not in quote:
                e = r["rules"][rank]["exemplars"][0]
                quote[law] = {"text": e["text"], "year": e.get("year"), "forum": e.get("forum")}
        # remedies: sample-based conditional shares scaled by fix coverage
        dem = Counter()
        for pk, rank in r["fix_assign"]:
            dem[nm["demand_merge"][str(rank)]] += 1
        dtot = sum(dem.values())
        fix_cov = r["n_fix"] / n
        remedies = {k: round(fix_cov * dem.get(k, 0) / max(1, dtot), 4)
                    for k in ("articulate", "evidence", "method", "report")}
        remedies["none"] = round(1 - fix_cov, 4)

        # law x remedy flows (sampled-fix pks only; others counted as unknown)
        rem_of = {}
        for pk, rank in r["fix_assign"]:
            rem_of[pk] = nm["demand_merge"][str(rank)]
        fix_pks = {pk for pk, _ in r["fix_assign"]}
        lr = {}
        for pk, laws in laws_of.items():
            rm = rem_of.get(pk)
            if rm is None and pk not in fix_pks:
                rm = "none"        # genuinely no fix (approx.: unsampled fixes excluded)
            if rm is None:
                continue
            for law in laws:
                lr.setdefault(law, Counter())[rm] += 1

        # grounds: per-unit assignment (sampled obs) + ground->law flows
        gm = gnaming[obj]
        gr = graw[obj]
        ground_of = {}
        for pk, rank in gr["assign"]:
            ground_of[pk] = gm["ground_merge"][str(rank)]
        gshare = Counter(ground_of.values())
        gtot = sum(gshare.values())
        dl = {}
        for pk, gk in ground_of.items():
            for law in laws_of.get(pk, ()):
                dl.setdefault(gk, Counter())[law] += 1
        grounds = sorted([{
            "key": k, "name": gm["grounds"][k][0], "def": gm["grounds"][k][1],
            "share": round(gshare[k] / max(1, gtot), 4),
        } for k in gm["grounds"]], key=lambda G: -G["share"])

        out[obj] = {
            "n": n,
            "stated": r["stated_share"],
            "laws": sorted([{
                "key": k, "name": nm["laws"][k][0], "def": nm["laws"][k][1],
                "share": round(share[k] / n, 4),
                "home": home_of(k, obj),
                "visiting": home_of(k, obj) not in (obj, None),
                "quote": quote.get(k),
                "remedy_mix": dict(lr.get(k, {})),
            } for k in nm["laws"]], key=lambda L: -L["share"]),
            "remedies": remedies,
            "grounds": grounds,
            "flows": {"doctrine_law": {k: dict(c) for k, c in dl.items()}},
        }

    # fold in novelty from the pilot
    nov = json.loads((V / "elements-novelty.json").read_text())
    rmap = {"articulate": "articulate", "substantiate": "evidence", "none": "none"}
    nrem = {"articulate": 0.0, "evidence": 0.0, "method": 0.0, "report": 0.0, "none": 0.0}
    for R in nov["remedies"]:
        nrem[rmap[R["key"]]] += R["share"]
    out["novelty"] = {
        "n": nov["n"], "stated": 1 - nov["unstated"]["share"],
        "laws": [{
            "key": L["key"], "name": L["name"], "def": L["def"],
            "share": L["share"],
            "home": "__evidence__" if L["key"] == "claims" else "novelty",
            "visiting": L["key"] == "claims",
            "quote": L["quotes"][0] if L.get("quotes") else None,
            "remedy_mix": {rmap.get(k2, k2): v2 for k2, v2 in
                           (nov["flows"]["law_remedy"].get(L["key"], {})).items()},
        } for L in nov["laws"]],
        "remedies": {k: round(v, 4) for k, v in nrem.items()},
        "grounds": nov["grounds"],
        "flows": {"doctrine_law": nov["flows"]["doctrine_law"]},
    }

    (V / "elements-all.json").write_text(json.dumps(out))
    print(f"{len(out)} objects written")
    for obj, v in out.items():
        top = v["laws"][0]
        visit = sum(1 for L in v["laws"] if L["visiting"])
        rem = max(v["remedies"], key=lambda k: v["remedies"][k] if k != "none" else 0)
        print(f"  {obj:>24s} n={v['n']:>7,} stated {v['stated']:.0%} · top law {top['name']} ({top['share']:.1%})"
              f" · visiting laws {visit} · lead remedy {rem} {v['remedies'][rem]:.0%} · none {v['remedies']['none']:.0%}")


if __name__ == "__main__":
    main()
