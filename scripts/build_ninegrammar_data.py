"""The grammar's nine years: did P(standard | object) itself move?

Row-normalized object x reasoning-standard matrices for the early era
(2018-19) and the current era (2025-26), Direct track, official
reviewers; the delta is where the discipline changed which law it
applies to the same object. A shuffle floor (year labels permuted
within object) says how much delta chance alone produces.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/ninegrammar-data.json.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

EARLY = (2018, 2019)
LATE = (2025, 2026)


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    rows = []  # (era, obj, rea)
    for yr, obj, rea in dc.execute(
        "SELECT u.year, l.object_key, l.reasoning_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' AND u.year IN (?,?,?,?)",
        (*EARLY, *LATE),
    ):
        rows.append((0 if yr in EARLY else 1, obj, rea))
    dc.close()
    print(f"{len(rows):,} units in the two eras")

    objs = sorted({o for _, o, _ in rows})
    reas = sorted({r for _, _, r in rows})

    def matrices(data):
        cnt = {0: defaultdict(Counter), 1: defaultdict(Counter)}
        for e, o, r in data:
            cnt[e][o][r] += 1
        out = {}
        for e in (0, 1):
            out[e] = {
                o: {r: cnt[e][o][r] / max(1, sum(cnt[e][o].values())) for r in reas}
                for o in objs
            }
        return out

    M = matrices(rows)
    delta = {o: {r: M[1][o][r] - M[0][o][r] for r in reas} for o in objs}

    # shuffle floor: permute era labels within object, 50 reps
    rng = random.Random(46)
    by_obj = defaultdict(list)
    for e, o, r in rows:
        by_obj[o].append((e, r))
    floor = []
    for _ in range(50):
        sh = []
        for o, lst in by_obj.items():
            eras = [e for e, _ in lst]
            rng.shuffle(eras)
            sh.extend((e, o, r) for e, (_, r) in zip(eras, lst))
        Ms = matrices(sh)
        floor.append(max(abs(Ms[1][o][r] - Ms[0][o][r]) for o in objs for r in reas))
    floor95 = sorted(floor)[int(0.95 * len(floor))]

    movers = sorted(
        ((o, r, delta[o][r]) for o in objs for r in reas),
        key=lambda t: -abs(t[2]),
    )[:8]
    out = {
        "objects": objs, "reasonings": reas,
        "early": {o: {r: round(M[0][o][r], 4) for r in reas} for o in objs},
        "late": {o: {r: round(M[1][o][r], 4) for r in reas} for o in objs},
        "delta": {o: {r: round(delta[o][r], 4) for r in reas} for o in objs},
        "floor95": round(floor95, 4),
        "n_early": sum(1 for e, _, _ in rows if e == 0),
        "n_late": sum(1 for e, _, _ in rows if e == 1),
    }
    (V / "ninegrammar-data.json").write_text(json.dumps(out))
    print(f"shuffle floor (95th pct of max |delta|): {floor95:.4f}")
    print("top movers:")
    for o, r, d_ in movers:
        print(f"  {o:>26s} × {r:<28s} {d_:+.3f}")


if __name__ == "__main__":
    main()
