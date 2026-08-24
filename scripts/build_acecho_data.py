"""Whose words reach the decision: the meta-review's sources.

For every forum with meta-reviewer units and >= 2 official reviewers,
each meta unit's reasoning embedding (cached, pk-aligned) is matched to
its nearest official-reviewer unit in the same forum. Three tiers:
near-copy (cos >= 0.90), echo (0.75-0.90), the AC's own words (< 0.75).
Per forum we ask WHO gets echoed: the panel's lowest rater, highest,
or middle — against the 1/n chance baseline — and whether the echoed
side is the side the decision lands on.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/acecho-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
A = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
T_COPY, T_ECHO = 0.90, 0.75


def main() -> None:
    emb = np.lib.format.open_memmap(D / "reasoning-embeddings.npy", mode="r")

    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    meta = defaultdict(list)      # forum -> [(pk, valence)]
    offi = defaultdict(list)      # forum -> [(pk, reviewer_key, valence)]
    year_of = {}
    for pk, fid, yr, rk, role, val in dc.execute(
        "SELECT unit_pk, forum_id, year, reviewer_key, reviewer_role, valence FROM units"
        " WHERE reviewer_role IN ('meta_reviewer','official_reviewer')"
    ):
        year_of[fid] = yr
        if role == "meta_reviewer":
            meta[fid].append((pk, val))
        else:
            offi[fid].append((pk, rk, val))
    dc.close()

    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    ratings = defaultdict(dict)   # forum -> {reviewer_key_tail: rating}
    for fid, sig, cj in ac.execute(
        "SELECT forum_id, signature, content_json FROM messages WHERE kind='official_review'"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, (int, float)):
            ratings[fid][sig.rsplit("/", 1)[-1]] = float(r)
    decisions = {fid: (dec or "") for fid, dec in ac.execute(
        "SELECT forum_id, decision FROM papers")}
    ac.close()

    tiers = Counter()
    tier_by_val = defaultdict(Counter)
    conc = []                     # top-reviewer echo share per forum
    pos_counter = Counter()       # lowest / middle / highest / tie
    pos_base = 0.0
    n_pos_forums = 0
    side = Counter()              # echoed-side vs decision
    by_year = defaultdict(lambda: [0, 0.0])
    n_forums = 0
    sims_by_val = defaultdict(list)   # meta-valence -> nearest-neighbor cosines (cutoff robustness)

    for fid, mus in meta.items():
        ous = offi.get(fid, [])
        revs = sorted({rk for _, rk, _ in ous})
        if len(mus) < 3 or len(revs) < 2:
            continue
        n_forums += 1
        M = np.asarray(emb[[pk - 1 for pk, _ in mus]], dtype=np.float32)
        O = np.asarray(emb[[pk - 1 for pk, _, _ in ous]], dtype=np.float32)
        S = M @ O.T
        nn = S.argmax(1)
        mx = S.max(1)

        echoed = Counter()
        for i, (mpk_val, j, s) in enumerate(zip(mus, nn, mx)):
            t = "copy" if s >= T_COPY else ("echo" if s >= T_ECHO else "own")
            tiers[t] += 1
            tier_by_val[mpk_val[1]][t] += 1
            sims_by_val[mpk_val[1]].append(float(s))
            if t != "own":
                echoed[ous[j][1]] += 1
        by_year[year_of[fid]][0] += 1
        by_year[year_of[fid]][1] += sum(echoed.values()) / len(mus)

        if not echoed:
            continue
        top, topn = echoed.most_common(1)[0]
        if sum(echoed.values()) >= 3:
            conc.append(topn / sum(echoed.values()))

        rmap = ratings.get(fid, {})

        def find_rating(rk: str):
            if rk in rmap:
                return rmap[rk]
            if f"Reviewer_{rk}" in rmap:
                return rmap[f"Reviewer_{rk}"]
            hits = [v for t, v in rmap.items() if t.endswith(f"_{rk}")]
            return hits[0] if len(hits) == 1 else None

        rs = {rk: r for rk in revs if (r := find_rating(rk)) is not None}
        if len(rs) == len(revs) and len(set(rs.values())) > 1:
            n_pos_forums += 1
            vals = sorted(rs.values())
            pos_base += 1 / len(revs)
            tr = rs.get(top)
            if tr is None:
                pos_counter["unmatched"] += 1
            elif tr == vals[0] and vals.count(tr) == 1:
                pos_counter["lowest"] += 1
            elif tr == vals[-1] and vals.count(tr) == 1:
                pos_counter["highest"] += 1
            elif tr in (vals[0], vals[-1]):
                pos_counter["tie"] += 1
            else:
                pos_counter["middle"] += 1
            # side test: echo-weighted rating vs panel mean, conditioned on decision
            dec = decisions.get(fid, "")
            if dec and dec.lower() not in ("", "none"):
                acc = "accept" in dec.lower()
                ew = sum(rs.get(rk, np.mean(vals)) * n for rk, n in echoed.items()) / sum(echoed.values())
                pm = float(np.mean(vals))
                if abs(ew - pm) > 1e-9:
                    side[("accept" if acc else "reject",
                          "harsher" if ew < pm else "kinder")] += 1

    total = sum(tiers.values())
    payload = {
        "n_forums": n_forums,
        "n_meta_units": total,
        "tiers": {k: round(v / total, 4) for k, v in tiers.items()},
        "tiers_by_meta_valence": {v: {k: round(c / max(1, sum(cc.values())), 4) for k, c in cc.items()}
                                  for v, cc in ((v, tier_by_val[v]) for v in ("negative", "positive", "mixed"))},
        "top_echo_share_mean": round(float(np.mean(conc)), 4),
        "top_echo_share_hist": np.histogram(conc, bins=10, range=(0, 1))[0].tolist(),
        "echoed_position": {k: round(v / max(1, n_pos_forums), 4) for k, v in pos_counter.items()},
        "n_position_forums": n_pos_forums,
        "position_chance": round(pos_base / max(1, n_pos_forums), 4),
        "side": {f"{a}_{b}": v for (a, b), v in side.items()},
        "echo_rate_by_year": {str(y): round(s / n, 4) for y, (n, s) in sorted(by_year.items()) if n >= 50},
        "thresholds": {"copy": T_COPY, "echo": T_ECHO},
        "gap_by_cutoff": {
            f"{c:.2f}": {
                "negative": round(float(np.mean(np.asarray(sims_by_val["negative"]) >= c)), 4),
                "positive": round(float(np.mean(np.asarray(sims_by_val["positive"]) >= c)), 4),
            }
            for c in (0.70, 0.75, 0.80)
        },
    }
    (V / "acecho-data.json").write_text(json.dumps(payload))
    print(json.dumps({k: v for k, v in payload.items() if k not in ("top_echo_share_hist",)}, indent=1))


if __name__ == "__main__":
    main()
