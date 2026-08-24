"""The elements of a charge (novelty pilot), stage 2.

Joins the three decompositions at the unit level, with multi-membership
where the data supports it:
  grounds  — the nine doctrines of stage 1 (one per unit, from clusters)
  laws     — normative sentences mined per unit and assigned to merged
             laws; a unit can invoke SEVERAL laws (sentence-level), or
             none ("unstated")
  remedies — the demanded fix, merged to articulate / substantiate /
             none

Outputs element tables (shares computed as "share of denials invoking
the element" — laws sum above 100% by design), the circuit flows
(doctrine x law, law x remedy, doctrine x remedy), canonical rulebook
quotes with provenance, and per-law year trends.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/elements-novelty.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

NORM = re.compile(r"requires?|must |constitutes?|is not sufficient|does not (?:equate|constitute|amount)|merely|alone (?:is|does)|threshold|criterion|necessary", re.I)

# stage-1 doctrine map (cluster idx in noveltylaw-raw -> doctrine)
NEG_MAP = {0: "uncited", 1: "transfer", 2: "uncited", 3: "anticipation",
           4: "equivalent", 5: "currency", 6: "combination", 7: "combination",
           8: "anticipation", 9: "restating", 10: "combination", 11: "transfer",
           12: "combination", 13: "step", 14: "step", 15: "assertion"}
DOCTRINE_NAMES = {
    "combination": "Obvious combination", "uncited": "The uncited neighborhood",
    "anticipation": "Anticipation", "transfer": "Mere transfer",
    "step": "The insufficient step", "equivalent": "The disguised equivalent",
    "currency": "The wrong currency", "restating": "Restating the known",
    "assertion": "The bare assertion",
}
# rule cluster idx (size-sorted, argument-raw) -> merged law
RULE_MAP = {0: "diff", 1: "diff", 2: "transfer", 3: "transfer", 4: "diff",
            5: "claims", 6: "diff", 7: "increment", 8: "increment", 9: "diff",
            10: "combination", 11: "increment", 12: "venue", 13: "venue"}
LAWS = {
    "diff": ("The differentiation rule", "novelty means articulated distinctness from prior art — and the burden of articulation is the author's"),
    "increment": ("The increment rule", "incremental modification or extension of an existing method does not constitute sufficient novelty"),
    "combination": ("The combination rule", "assembling known components is not novel unless the integration itself is"),
    "transfer": ("The transfer rule", "applying a known method to a new domain or setting does not constitute methodological novelty"),
    "venue": ("The venue bar", "explicitly indexed to the venue's rank — 'not sufficient for a top-tier conference'"),
    "claims": ("The evidence rule (imported)", "not a definition of novelty at all - the general claims-must-match-validation law, imported into novelty denials: the objection is to the support for the novelty claim, not to the novelty itself"),
}
# demand cluster idx -> remedy
DEM_MAP = {0: "articulate", 1: "substantiate", 2: "articulate", 3: "articulate",
           4: "articulate", 5: "articulate", 6: "articulate", 7: "substantiate"}
REMEDIES = {
    "articulate": ("Articulate the distinction", "clarify, compare, position — work of writing and argument"),
    "substantiate": ("Substantiate the advance", "larger improvements, deeper analysis, new evidence — work of research"),
    "none": ("No remedy offered", "the verdict arrives without a road back"),
}


def kfit(X, k):
    from sklearn.cluster import KMeans
    return KMeans(n_clusters=k, n_init=6, random_state=46).fit(X)


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    rows = {}
    for pk, yr, fid, obs, rea, fix in dc.execute(
        "SELECT u.unit_pk, u.year, u.forum_id, u.observation, u.reasoning, u.suggested_improvement"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE l.object_key = 'novelty' AND u.valence = 'negative'"
        " AND u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'"
    ):
        o, r = (obs or "").strip(), (rea or "").strip()
        if len(o) >= 40 and len(r) >= 40:
            f = (fix or "").strip()
            rows[pk] = (yr, fid, o, r, "" if f.lower() in ("", "none") else f)
    dc.close()
    print(f"{len(rows):,} units")

    # doctrine per pk from stage 1
    raw1 = json.loads((V / "noveltylaw-raw.json").read_text())
    doctrine = {}
    for ci, cluster in enumerate(raw1["negative"]["clusters"]):
        for pk in cluster["member_pks"]:
            if pk in rows:
                doctrine[pk] = NEG_MAP[ci]

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")

    def embed(texts):
        return model.encode(texts, batch_size=256, normalize_embeddings=True).astype(np.float32)

    # ---- laws: sentence-level, multi-membership ----
    sent_rows = []  # (pk, sentence)
    for pk, (yr, fid, o, r, f) in rows.items():
        for sent in re.split(r"(?<=[.;])\s+", r):
            if 40 <= len(sent) <= 220 and NORM.search(sent):
                sent_rows.append((pk, sent.strip()))
    Xr = embed([t for _, t in sent_rows])
    kr = kfit(Xr, 14)
    laws_of = defaultdict(set)
    law_quotes = defaultdict(list)
    sims = np.einsum("ij,ij->i", Xr, kr.cluster_centers_[kr.labels_] /
                     (np.linalg.norm(kr.cluster_centers_[kr.labels_], axis=1, keepdims=True) + 1e-9))
    # size-sorted cluster index mapping (RULE_MAP keys refer to size order)
    sizes = np.bincount(kr.labels_, minlength=14)
    order = np.argsort(-sizes)
    rank_of = {int(c): i for i, c in enumerate(order)}
    for i, (pk, sent) in enumerate(sent_rows):
        law = RULE_MAP[rank_of[int(kr.labels_[i])]]
        laws_of[pk].add(law)
        law_quotes[law].append((float(sims[i]), sent, rows[pk][0], rows[pk][1]))

    # ---- remedies ----
    fix_pks = [pk for pk in rows if rows[pk][4]]
    Xd = embed([rows[pk][4] for pk in fix_pks])
    kd = kfit(Xd, 8)
    dsizes = np.bincount(kd.labels_, minlength=8)
    dorder = np.argsort(-dsizes)
    drank = {int(c): i for i, c in enumerate(dorder)}
    remedy = {pk: "none" for pk in rows}
    for j, pk in enumerate(fix_pks):
        remedy[pk] = DEM_MAP[drank[int(kd.labels_[j])]]

    # ---- tables & flows ----
    n = len(rows)
    law_share = Counter()
    law_years = defaultdict(Counter)
    year_tot = Counter()
    for pk, (yr, fid, o, r, f) in rows.items():
        year_tot[yr] += 1
        for law in laws_of.get(pk, ()):
            law_share[law] += 1
            law_years[law][yr] += 1
    unstated = sum(1 for pk in rows if pk not in laws_of)
    avg_laws = sum(len(v) for v in laws_of.values()) / n

    docs = Counter(doctrine.values())
    rem = Counter(remedy.values())

    dl = defaultdict(Counter)   # doctrine -> law
    dr = defaultdict(Counter)   # doctrine -> remedy
    lr = defaultdict(Counter)   # law -> remedy
    for pk in rows:
        d_ = doctrine.get(pk)
        rm = remedy[pk]
        if d_:
            dr[d_][rm] += 1
        for law in laws_of.get(pk, ()):
            lr[law][rm] += 1
            if d_:
                dl[d_][law] += 1

    out = {
        "n": n,
        "grounds": [{"key": k, "name": DOCTRINE_NAMES[k], "n": v,
                     "share": round(v / n, 4)} for k, v in docs.most_common()],
        "laws": [{"key": k, "name": LAWS[k][0], "def": LAWS[k][1],
                  "n": law_share[k], "share": round(law_share[k] / n, 4),
                  "years": {str(y): round(law_years[k][y] / year_tot[y], 4)
                            for y in sorted(year_tot) if year_tot[y] >= 200},
                  "quotes": [{"text": t, "year": y, "forum": f}
                             for _, t, y, f in sorted(law_quotes[k], reverse=True)[:4]]}
                 for k in sorted(LAWS, key=lambda k: -law_share[k])],
        "unstated": {"n": unstated, "share": round(unstated / n, 4)},
        "avg_laws_per_stated": round(sum(len(v) for v in laws_of.values()) / max(1, len(laws_of)), 3),
        "remedies": [{"key": k, "name": REMEDIES[k][0], "def": REMEDIES[k][1],
                      "n": rem[k], "share": round(rem[k] / n, 4)} for k in ("articulate", "substantiate", "none")],
        "flows": {"doctrine_law": {d_: dict(c) for d_, c in dl.items()},
                  "doctrine_remedy": {d_: dict(c) for d_, c in dr.items()},
                  "law_remedy": {l_: dict(c) for l_, c in lr.items()}},
    }
    (V / "elements-novelty.json").write_text(json.dumps(out))
    # sidecar: per-unit assignments for validation passes
    (V / "elements-novelty-assign.json").write_text(json.dumps({
        str(pk): {"doctrine": doctrine.get(pk), "laws": sorted(laws_of.get(pk, [])),
                  "remedy": remedy[pk]} for pk in rows}))
    print(f"laws: avg {out['avg_laws_per_stated']} per stated unit · unstated {unstated/n:.1%}")
    for L in out["laws"]:
        yr = L["years"]
        print(f"  {L['name']:>26s} {L['share']:>6.1%}  '18 {yr.get('2018','-')} → '26 {yr.get('2026','-')}")
    for R in out["remedies"]:
        print(f"  [remedy] {R['name']:>26s} {R['share']:>6.1%}")
    print("law → remedy (share articulate|substantiate|none):")
    for l_, c in lr.items():
        t = sum(c.values())
        print(f"  {l_:>12s}: {c.get('articulate',0)/t:.0%}|{c.get('substantiate',0)/t:.0%}|{c.get('none',0)/t:.0%}")


if __name__ == "__main__":
    main()
