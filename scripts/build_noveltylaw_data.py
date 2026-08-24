"""The law of novelty, stage 2: merge the raw clusters into named
doctrines, compute shares, year trends, and how often each doctrine
cites specific prior art; same for the grounds of allowance.

Doctrine names were assigned by reading each cluster's exemplars
(noveltylaw-raw.json); the mapping below is the hand-made part.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/noveltylaw-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

# cluster index (size-sorted, as in noveltylaw-raw.json) -> doctrine key
NEG_MAP = {
    0: "uncited", 1: "transfer", 2: "uncited", 3: "anticipation",
    4: "equivalent", 5: "currency", 6: "combination", 7: "combination",
    8: "anticipation", 9: "restating", 10: "combination", 11: "transfer",
    12: "combination", 13: "step", 14: "step", 15: "assertion",
}
NEG_DOCTRINES = {
    "combination": ("Obvious combination", "an assembly of known parts, with no new mechanism inside"),
    "uncited": ("The uncited neighborhood", "close prior work exists and is neither cited nor compared"),
    "anticipation": ("Anticipation", "a specific named prior work already does (nearly) this"),
    "transfer": ("Mere transfer", "an old method carried to a new domain, task, or modality"),
    "step": ("The insufficient step", "a real advance, but too small — incremental over a named base"),
    "equivalent": ("The disguised equivalent", "a reformulation or reparameterization of a known method"),
    "currency": ("The wrong currency", "the contribution is a dataset, benchmark, or study — discounted as non-method"),
    "restating": ("Restating the known", "standard or well-known results presented as new"),
    "assertion": ("The bare assertion", "novelty denied with no specific ground or prior work named"),
}
POS_MAP = {0: "declared", 1: "declared", 2: "declared", 3: "declared",
           4: "declared", 5: "distinct", 6: "searched", 7: "declared"}
POS_GROUNDS = {
    "declared": ("Declared novel", "novelty asserted outright, the weight carried by results or writing"),
    "distinct": ("Distinct enough", "not groundbreaking, but sufficiently different from named neighbors"),
    "searched": ("No prior art found", "the reviewer searched, found nothing similar, and says so — often with an expertise hedge"),
}

CITE = re.compile(r"et al|[A-Z][a-z]+ (?:&|and) [A-Z][a-z]+|\b(19|20)\d{2}[a-z]?\b")


def main() -> None:
    raw = json.loads((V / "noveltylaw-raw.json").read_text())
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    text_of = {}
    for pk, obs, rea in dc.execute(
        "SELECT u.unit_pk, u.observation, u.reasoning FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk WHERE l.object_key = 'novelty'"
    ):
        text_of[pk] = ((obs or "") + " " + (rea or ""))
    dc.close()

    def merge(tag, cmap, defs):
        agg = {}
        for ci, cluster in enumerate(raw[tag]["clusters"]):
            key = cmap[ci]
            a = agg.setdefault(key, {"n": 0, "years": Counter(), "cited": 0, "exemplars": []})
            a["n"] += cluster["n"]
            for y, n in cluster["years"].items():
                a["years"][y] += n
            for pk in cluster["member_pks"]:
                if CITE.search(text_of.get(pk, "")):
                    a["cited"] += 1
            if len(a["exemplars"]) < 3:
                a["exemplars"].extend(cluster["exemplars"][:2])
        tot = sum(a["n"] for a in agg.values())
        year_tot = Counter()
        for a in agg.values():
            year_tot.update(a["years"])
        out = []
        for key, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
            name, defi = defs[key]
            out.append({
                "key": key, "name": name, "def": defi,
                "n": a["n"], "share": round(a["n"] / tot, 4),
                "cited": round(a["cited"] / a["n"], 4),
                "years": {y: round(a["years"][y] / year_tot[y], 4)
                          for y in sorted(year_tot) if year_tot[y] >= 300},
                "exemplars": a["exemplars"][:3],
            })
        return out, tot

    neg, neg_tot = merge("negative", NEG_MAP, NEG_DOCTRINES)
    pos, pos_tot = merge("positive", POS_MAP, POS_GROUNDS)

    # asymmetry: prior-art citing rate, denial vs praise
    def cite_rate(tag):
        pks = [pk for c in raw[tag]["clusters"] for pk in c["member_pks"]]
        return sum(1 for pk in pks if CITE.search(text_of.get(pk, ""))) / len(pks)

    out = {
        "denial": neg, "n_denial": neg_tot,
        "allowance": pos, "n_allowance": pos_tot,
        "cite_rate": {"denial": round(cite_rate("negative"), 4),
                      "allowance": round(cite_rate("positive"), 4)},
    }
    (V / "noveltylaw-data.json").write_text(json.dumps(out))
    print(f"denial {neg_tot:,} · allowance {pos_tot:,}")
    print(f"cites specific art: denial {out['cite_rate']['denial']:.1%} vs allowance {out['cite_rate']['allowance']:.1%}")
    for d_ in neg:
        yr = d_["years"]
        first = yr.get("2018") or yr.get("2019")
        last = yr.get("2026")
        print(f"  {d_['name']:>26s} {d_['share']:>6.1%}  cites {d_['cited']:>5.1%}  '18~{first:.0%} → '26 {last:.0%}")
    for d_ in pos:
        print(f"  [+] {d_['name']:>22s} {d_['share']:>6.1%}  cites {d_['cited']:>5.1%}")


if __name__ == "__main__":
    main()
