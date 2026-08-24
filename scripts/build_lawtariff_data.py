"""The same law, a different sentence: per-law tariffs across dockets.

For every (docket, law) with enough invoking reviews, the within-paper
score deficit: the invoking reviewer's rating minus the mean rating of
the OTHER reviewers on the same paper (the Plate-Jurisprudence tariff,
now computed one law deep). The question it answers: does the visiting
evidence rule cost the same everywhere, or is the same law a misdemeanor
in one docket and a felony in another?

Law assignments come from the sampled sentence clusters (elements-all-raw
+ novelty-direct-raw + reviewed naming maps); sampling thins coverage but
does not bias a per-law mean deficit.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/lawtariff-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
A = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
SCRATCH = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1/naming"
MIN_N = 150


def load_laws_of() -> tuple[dict, dict]:
    """unit_pk -> set of (docket, law_key); plus display names."""
    raw = json.loads((V / "elements-all-raw.json").read_text())
    naming = {}
    for g in (1, 2, 3):
        naming.update(json.loads((SCRATCH / f"naming-group{g}.json").read_text()))
    laws_of: dict[int, set] = defaultdict(set)
    names = {}
    for obj, nm in naming.items():
        for pk, rank in raw[obj]["sent_assign"]:
            key = nm["rule_merge"][str(rank)]
            laws_of[pk].add((obj, key))
        for k, (nice, _d) in nm["laws"].items():
            names[(obj, k)] = nice
    nov = json.loads((V / "novelty-direct-raw.json").read_text())
    nmap = json.loads((SCRATCH / "novname.json").read_text())["rule_merge"]
    NOV_NAMES = {"diff": "The differentiation rule", "increment": "The increment rule",
                 "combination": "The combination rule", "transfer": "The transfer rule",
                 "claims": "The evidence rule (imported)", "venue": "The venue bar"}
    for pk, _yr, rank, _d in nov["assign"]:
        key = nmap[str(rank)]
        laws_of[pk].add(("novelty", key))
        names[("novelty", key)] = NOV_NAMES.get(key, key)
    return laws_of, names


def main() -> None:
    laws_of, names = load_laws_of()

    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    ratings = defaultdict(dict)
    for fid, sig, cj in ac.execute(
        "SELECT forum_id, signature, content_json FROM messages WHERE kind='official_review'"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, (int, float)):
            ratings[fid][sig.rsplit("/", 1)[-1]] = float(r)
    ac.close()

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    # per-review law sets (only reviews containing assigned pks)
    review_laws: dict[tuple, set] = defaultdict(set)
    year_of: dict[tuple, int] = {}
    CH = 900
    pks = sorted(laws_of)
    for i in range(0, len(pks), CH):
        chunk = pks[i:i + CH]
        qs = ",".join("?" * len(chunk))
        for pk, fid, rk, yr in dc.execute(
            f"SELECT unit_pk, forum_id, reviewer_key, year FROM units WHERE unit_pk IN ({qs})", chunk
        ):
            review_laws[(fid, rk)].update(laws_of[pk])
            year_of[(fid, rk)] = yr
    dc.close()
    print(f"{len(review_laws):,} reviews carry >=1 assigned law sentence")

    def find_rating(rmap, rk):
        if rk in rmap:
            return rmap[rk]
        if f"Reviewer_{rk}" in rmap:
            return rmap[f"Reviewer_{rk}"]
        hits = [v for t, v in rmap.items() if t.endswith(f"_{rk}")]
        return hits[0] if len(hits) == 1 else None

    # rating scales changed across years: normalize deficit within year by
    # the year's rating std so tariffs are comparable (in std units).
    year_ratings = defaultdict(list)
    for (fid, rk), _laws in review_laws.items():
        r = find_rating(ratings.get(fid, {}), rk)
        if r is not None:
            year_ratings[year_of[(fid, rk)]].append(r)
    year_std = {y: max(0.5, float(np.std(v))) for y, v in year_ratings.items() if len(v) > 50}

    deficits: dict[tuple, list] = defaultdict(list)
    for (fid, rk), laws in review_laws.items():
        rmap = ratings.get(fid, {})
        mine = find_rating(rmap, rk)
        if mine is None or len(rmap) < 2:
            continue
        yr = year_of[(fid, rk)]
        if yr not in year_std:
            continue
        others = [v for t, v in rmap.items()
                  if not (t == rk or t == f"Reviewer_{rk}" or t.endswith(f"_{rk}"))]
        if not others:
            continue
        d = (mine - float(np.mean(others))) / year_std[yr]
        for law in laws:
            deficits[law].append(d)

    out = defaultdict(list)
    for (obj, key), ds in deficits.items():
        if len(ds) < MIN_N:
            continue
        arr = np.array(ds)
        se = float(arr.std() / np.sqrt(len(arr)))
        out[obj].append({
            "key": key, "name": names.get((obj, key), key),
            "tariff": round(float(arr.mean()), 4), "se": round(se, 4),
            "n": len(arr),
        })
    for obj in out:
        out[obj].sort(key=lambda L: L["tariff"])

    payload = {"dockets": dict(out), "unit": "std units of that year's rating scale",
               "min_n": MIN_N}
    (V / "lawtariff-data.json").write_text(json.dumps(payload))
    ev = [(obj, L) for obj, Ls in out.items() for L in Ls if "imported" in L["name"] or L["key"].startswith("evidence")]
    print("evidence-rule tariff by docket:")
    for obj, L in sorted(ev, key=lambda x: x[1]["tariff"]):
        print(f"  {obj:>24s}  {L['tariff']:+.3f} ± {L['se']:.3f}  (n={L['n']:,})")
    for obj in ("novelty", "clarity"):
        print(obj, [(L["key"], L["tariff"], L["n"]) for L in out.get(obj, [])])


if __name__ == "__main__":
    main()
