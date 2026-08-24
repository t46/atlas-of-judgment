"""The higher court: the meta-reviewer's grammar, and what the bench
forgives at the margin.

Three instruments:

1. TWO GRAMMARS — object attention and valence of meta-reviewer units
   against official-reviewer units (Direct track; ACs concentrated in
   2024-26, stated in the caption).

2. WHAT THE BENCH FORGIVES (2026) — among panels whose mean rating sits
   below the empirical accept line (3.5 <= mean < 5.0), compare the
   criticism profile of papers that were nevertheless accepted against
   those rejected: P(>=1 negative unit on object | accepted) vs
   (| rejected), plus a rating-adjusted delta computed inside 0.25-wide
   mean-rating bins so "forgiven papers simply scored higher in-band"
   cannot masquerade as forgiveness.

3. THE REDEMPTION — on those same below-line forums, which objects the
   AC's own meta-review units praise (positive valence) when the paper
   is lifted, against the band's rejected papers.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/court-data.json.
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
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

BAND = (3.5, 5.0)


def main() -> None:
    # ---------- 1. two grammars ----------
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    gram = {"reviewer": defaultdict(Counter), "ac": defaultdict(Counter)}
    for role, obj, val in dc.execute(
        "SELECT u.reviewer_role, l.object_key, u.valence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        k = "ac" if role == "meta_reviewer" else "reviewer"
        gram[k][obj][val] += 1
    grammars = {}
    for k, d_ in gram.items():
        tot = sum(sum(c.values()) for c in d_.values())
        grammars[k] = {
            "n": tot,
            "objects": {o: {"share": round(sum(c.values()) / tot, 4),
                            "pos": round(c.get("positive", 0) / max(1, sum(c.values())), 4)}
                        for o, c in d_.items()},
        }

    # AC meta units per 2026 forum for instrument 3
    ac_units_2026 = defaultdict(list)  # forum_id -> [(obj, val)]
    for fid, obj, val in dc.execute(
        "SELECT u.forum_id, l.object_key, u.valence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'meta_reviewer' AND u.year = 2026"
    ):
        ac_units_2026[fid].append((obj, val))
    dc.close()

    # ---------- 2026 panel means + decisions ----------
    ac_ = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    ratings = defaultdict(list)
    for fid, cj in ac_.execute(
        "SELECT forum_id, content_json FROM messages WHERE kind='official_review' AND year=2026"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, int):
            ratings[fid].append(r)
    decisions = {fid: dec for fid, dec in ac_.execute(
        "SELECT forum_id, decision FROM papers WHERE year = 2026"
    )}
    ac_.close()

    # ---------- 2026 charged objects per forum (review-level track) ----------
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    charged = defaultdict(set)  # paper_id -> {objects with >=1 negative unit}
    for pid, obj in uc.execute(
        "SELECT u.paper_id, l.object_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk WHERE u.valence = 'negative'"
    ):
        charged[pid].add(obj)
    uc.close()

    def is_acc(dec):
        return bool(dec and re.search(r"accept|oral|poster|spotlight", dec, re.I))

    def is_rej(dec):
        return bool(dec and re.search(r"reject", dec, re.I))

    band_acc, band_rej = [], []  # (fid, mean)
    for fid, rs in ratings.items():
        if len(rs) < 2 or fid not in charged:
            continue
        m = sum(rs) / len(rs)
        if BAND[0] <= m < BAND[1]:
            dec = decisions.get(fid)
            if is_acc(dec):
                band_acc.append((fid, m))
            elif is_rej(dec):
                band_rej.append((fid, m))

    objects = sorted({o for s in charged.values() for o in s})

    def charge_rate(group, obj):
        n = len(group)
        return sum(1 for fid, _ in group if obj in charged[fid]) / n if n else 0

    # rating-adjusted delta inside 0.25 bins
    def binkey(m):
        return int((m - BAND[0]) / 0.25)

    bins_acc, bins_rej = defaultdict(list), defaultdict(list)
    for fid, m in band_acc:
        bins_acc[binkey(m)].append(fid)
    for fid, m in band_rej:
        bins_rej[binkey(m)].append(fid)

    import random
    rng = random.Random(46)

    def adj_delta(acc_bins, rej_bins, obj):
        num = den = 0.0
        for b in acc_bins:
            if b not in rej_bins:
                continue
            w = min(len(acc_bins[b]), len(rej_bins[b]))
            if w < 10:
                continue
            da = sum(1 for f in acc_bins[b] if obj in charged[f]) / len(acc_bins[b])
            dr = sum(1 for f in rej_bins[b] if obj in charged[f]) / len(rej_bins[b])
            num += w * (da - dr)
            den += w
        return num / den if den else None

    # bootstrap: resample forums within each bin, 300 reps
    B = 300
    boot = {obj: [] for obj in objects}
    for _ in range(B):
        racc = {b: [rng.choice(v) for _ in v] for b, v in bins_acc.items()}
        rrej = {b: [rng.choice(v) for _ in v] for b, v in bins_rej.items()}
        for obj in objects:
            d_ = adj_delta(racc, rrej, obj)
            if d_ is not None:
                boot[obj].append(d_)

    forgive = {}
    for obj in objects:
        bs = sorted(boot[obj])
        forgive[obj] = {
            "acc": round(charge_rate(band_acc, obj), 4),
            "rej": round(charge_rate(band_rej, obj), 4),
            "adj_delta": round(adj_delta(bins_acc, bins_rej, obj), 4),
            "lo": round(bs[int(0.025 * len(bs))], 4) if bs else None,
            "hi": round(bs[int(0.975 * len(bs)) - 1], 4) if bs else None,
        }

    # ---------- 3. the redemption: AC praise on band forums ----------
    def ac_pos_profile(group):
        cnt, tot = Counter(), 0
        for fid, _ in group:
            for obj, val in ac_units_2026.get(fid, []):
                tot += 1
                if val == "positive":
                    cnt[obj] += 1
        return {o: round(cnt[o] / tot, 4) for o in objects} if tot else {}, tot

    red_acc, n_acc_units = ac_pos_profile(band_acc)
    red_rej, n_rej_units = ac_pos_profile(band_rej)

    out = {
        "grammars": grammars,
        "band": {"lo": BAND[0], "hi": BAND[1], "n_acc": len(band_acc), "n_rej": len(band_rej)},
        "forgive": forgive,
        "redemption": {"acc": red_acc, "rej": red_rej,
                       "n_units_acc": n_acc_units, "n_units_rej": n_rej_units},
    }
    (V / "court-data.json").write_text(json.dumps(out))
    print(f"band [{BAND[0]},{BAND[1]}): accepted {len(band_acc)} vs rejected {len(band_rej)}")
    print(f"{'object':>26s} {'P|acc':>6s} {'P|rej':>6s} {'adjΔ':>7s}")
    for o in sorted(objects, key=lambda o: forgive[o]["adj_delta"] or 0):
        f = forgive[o]
        print(f"{o:>26s} {f['acc']:>6.3f} {f['rej']:>6.3f} {f['adj_delta']:>7.3f} [{f['lo']:>6.3f},{f['hi']:>6.3f}]")
    print("\nAC praise share (per AC unit) on band forums, accepted vs rejected:")
    for o in sorted(objects, key=lambda o: -(red_acc.get(o, 0) - red_rej.get(o, 0))):
        print(f"{o:>26s} {red_acc.get(o,0):>6.3f} {red_rej.get(o,0):>6.3f}")


if __name__ == "__main__":
    main()
