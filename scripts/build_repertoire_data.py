"""The repertoire: global patterns of deliberation across all panels.

Three aggregate questions over the Direct-track units (official reviewers):

1. Signatures — classify every panel (>=3 reviewers) by what its discussion
   phase did: silence (no post-response units), procedural (post units, no
   judgment movement), entrenchment (hardened only), softening (softened
   only), contested (both). Shares per year pooled; for 2026, acceptance
   rate per signature (association only).

2. Attention overlap — do co-reviewers of the same paper even inspect the
   same objects? Mean pairwise Jaccard of initial-unit object sets for real
   co-reviewer pairs, against a shuffled baseline of same-year stranger
   pairs. (The NeurIPS experiments measured outcome disagreement; this
   measures whether panels attend to the same paper at all.)

3. Dissonance — objects judged oppositely by co-reviewers in the initial
   round: are they revisited after the response more or less often than
   objects the panel agreed on?

Writes data/analysis/iclr/unit-taxonomy-2026-v1/repertoire-data.json.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def main() -> None:
    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)

    # per (forum, reviewer): initial object set + valence per object + post stats
    init_objs: dict = defaultdict(lambda: defaultdict(set))          # cid -> rk -> {obj}
    init_val: dict = defaultdict(lambda: defaultdict(dict))          # cid -> obj -> rk -> sign
    post_objs: dict = defaultdict(set)                               # cid -> {obj revisited}
    post_flags: dict = defaultdict(lambda: {"post": 0, "soft": 0, "hard": 0})
    year_of: dict = {}
    forum_of: dict = {}
    for cid, fid, year, rk, phase, chg, val, obj in conn.execute(
        "SELECT u.custom_id, u.forum_id, u.year, u.reviewer_key, u.temporal_position,"
        " u.judgment_change, u.valence, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer'"
    ):
        year_of[cid] = year
        forum_of[cid] = fid
        if phase == "initial_review":
            init_objs[cid][rk].add(obj)
            if val in ("negative", "positive"):
                d = init_val[cid].setdefault(obj, {})
                d[rk] = "-" if val == "negative" else "+"
        elif phase == "post_author_response":
            post_objs[cid].add(obj)
            f = post_flags[cid]
            f["post"] += 1
            if chg in ("weakened", "reversed"):
                f["soft"] += 1
            elif chg == "strengthened":
                f["hard"] += 1
    conn.close()

    # ---- decisions for 2026 ----
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    accepted = {}
    for fid, decision, withdrawn in aconn.execute(
        "SELECT forum_id, decision, withdrawn FROM papers WHERE year = 2026"
    ):
        if decision and not withdrawn:
            accepted[fid] = any(w in decision.lower() for w in ("accept", "oral", "poster", "spotlight"))
    aconn.close()

    # ---- 1. signatures ----
    sig_counts = defaultdict(int)
    sig_year = defaultdict(int)
    year_tot = defaultdict(int)
    sig_acc = defaultdict(lambda: [0, 0])   # accepted, decided
    n_panels = 0
    for cid, revs in init_objs.items():
        if len(revs) < 3:
            continue
        n_panels += 1
        f = post_flags[cid]
        if f["post"] == 0:
            sig = "silence"
        elif f["soft"] == 0 and f["hard"] == 0:
            sig = "procedural"
        elif f["soft"] > 0 and f["hard"] > 0:
            sig = "contested"
        elif f["hard"] > 0:
            sig = "entrenchment"
        else:
            sig = "softening"
        sig_counts[sig] += 1
        sig_year[(year_of[cid], sig)] += 1
        year_tot[year_of[cid]] += 1
        if year_of[cid] == 2026 and forum_of[cid] in accepted:
            sig_acc[sig][1] += 1
            sig_acc[sig][0] += accepted[forum_of[cid]]

    signatures = {}
    for sig, n in sorted(sig_counts.items(), key=lambda kv: -kv[1]):
        a, d = sig_acc[sig]
        signatures[sig] = {"n": n, "share": round(n / n_panels, 4),
                           "accept_2026": round(a / d, 4) if d > 100 else None, "n_decided_2026": d}
        print(f"{sig:14s} {n:6d} ({n/n_panels:5.1%})  accept2026={a/max(1,d):.1%} (n={d})")

    # ---- 2. attention overlap ----
    def jac(a, b):
        return len(a & b) / len(a | b) if (a or b) else 0.0

    real, real_by_year = [], defaultdict(list)
    pool_by_year = defaultdict(list)
    for cid, revs in init_objs.items():
        rl = [s for s in revs.values() if s]
        for s in rl:
            pool_by_year[year_of[cid]].append(s)
        if len(rl) < 2:
            continue
        for i in range(len(rl)):
            for j in range(i + 1, len(rl)):
                v = jac(rl[i], rl[j])
                real.append(v)
                real_by_year[year_of[cid]].append(v)
    rng = random.Random(7)
    base = []
    for year, pool in pool_by_year.items():
        for _ in range(min(60000, len(pool) * 2)):
            a, b = rng.sample(range(len(pool)), 2)
            base.append(jac(pool[a], pool[b]))

    def hist(vals):
        bins = [0] * 10
        for v in vals:
            bins[min(9, int(v * 10))] += 1
        tot = len(vals)
        return [round(b / tot, 4) for b in bins]

    overlap = {
        "real_mean": round(sum(real) / len(real), 4),
        "base_mean": round(sum(base) / len(base), 4),
        "real_hist": hist(real), "base_hist": hist(base),
        "n_pairs": len(real),
        "by_year": {y: round(sum(v) / len(v), 4) for y, v in sorted(real_by_year.items()) if len(v) > 500},
    }
    print(f"\nattention overlap: co-reviewers {overlap['real_mean']:.3f} vs strangers {overlap['base_mean']:.3f} ({len(real)} pairs)")

    # ---- 3. dissonance revisits ----
    dis_rev, dis_tot, con_rev, con_tot = 0, 0, 0, 0
    for cid, objmap in init_val.items():
        if len(init_objs[cid]) < 3:
            continue
        for obj, votes in objmap.items():
            if len(votes) < 2:
                continue
            signs = set(votes.values())
            revisited = obj in post_objs[cid]
            if "+" in signs and "-" in signs:
                dis_tot += 1
                dis_rev += revisited
            elif len(signs) == 1:
                con_tot += 1
                con_rev += revisited
    dissonance = {
        "contested_n": dis_tot, "contested_revisit": round(dis_rev / dis_tot, 4),
        "agreed_n": con_tot, "agreed_revisit": round(con_rev / con_tot, 4),
    }
    print(f"dissonant objects revisited {dis_rev/dis_tot:.1%} (n={dis_tot}) vs agreed {con_rev/con_tot:.1%} (n={con_tot})")

    by_year = {}
    for y in sorted(year_tot):
        if year_tot[y] < 300:
            continue
        by_year[y] = {sig: round(sig_year[(y, sig)] / year_tot[y], 4)
                      for sig in ("silence", "procedural", "entrenchment", "softening", "contested")}
        print(y, by_year[y])
    payload = {"n_panels": n_panels, "signatures": signatures, "overlap": overlap, "dissonance": dissonance, "by_year": by_year}
    (V / "repertoire-data.json").write_text(json.dumps(payload) + "\n")


if __name__ == "__main__":
    main()
