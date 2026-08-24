"""Boundary twin exhibits for Fig 11c (declared follow-up, 2026-08-21).

The exhibit pairs shipped with boilerplate-data.json are all 0.99+ —
the verbatim tier. To let the reader SEE what the 0.90 formula line and
the 0.85 familiar line mean, this script mines exemplar pairs whose
nearest-twin similarity sits AT those thresholds. Embeddings come from
the cache; a random subset of units is matched against the full corpus
(each subset unit's nearest cross-paper twin is exact), and pairs are
kept whose best similarity falls in a narrow band around each line.

Appends "pairs_boundary" to boilerplate-data.json in place.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"

BANDS = {"formula": (0.895, 0.907), "familiar": (0.845, 0.857)}
SUBSET = 48000
SEED = 46


def load_units():
    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    rows = []
    for pk, pid, rid, obj, val, obs in uc.execute(
        "SELECT u.unit_pk, u.paper_id, u.review_id, l.object_key, u.valence, u.observation"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.valence = 'negative' AND u.observation IS NOT NULL"
    ):
        t = (obs or "").strip()
        if len(t) >= 40:
            rows.append((pk, pid, rid, obj, t))
    uc.close()
    return rows


def main() -> None:
    rows = load_units()
    emb = np.load(V / "boilerplate-emb.npy")
    assert len(emb) == len(rows), (len(emb), len(rows))
    emb = np.ascontiguousarray(emb, dtype=np.float32)
    n = len(rows)
    papers = np.array([hash(r[1]) for r in rows], dtype=np.int64)

    rng = np.random.default_rng(SEED)
    subset = rng.choice(n, size=min(SUBSET, n), replace=False)

    found = defaultdict(list)
    B = 1024
    K = 8
    for s0 in range(0, len(subset), B):
        idx = subset[s0:s0 + B]
        sims = emb[idx] @ emb.T
        part = np.argpartition(sims, -K - 1, axis=1)[:, -K - 1:]
        for bi, i in enumerate(idx):
            cand = part[bi]
            order = cand[np.argsort(sims[bi, cand])[::-1]]
            for j in order:
                if j != i and papers[j] != papers[i]:
                    s = float(sims[bi, j])
                    for band, (lo, hi) in BANDS.items():
                        if lo <= s <= hi:
                            found[band].append((s, int(i), int(j)))
                    break
        if (s0 // B) % 10 == 0:
            print(f"  {s0:,}/{len(subset):,} · found "
                  + " ".join(f"{b}:{len(v)}" for b, v in found.items()), flush=True)

    out = []
    for band, hits in found.items():
        seen_obj = defaultdict(int)
        rng.shuffle(hits)
        for s, i, j in hits:
            a, b = rows[i][4], rows[j][4]
            if not (60 <= len(a) <= 230 and 60 <= len(b) <= 230):
                continue
            if seen_obj[rows[i][3]] >= 2:
                continue
            seen_obj[rows[i][3]] += 1
            out.append({"band": band, "sim": round(s, 4), "obj": rows[i][3],
                        "a": a[:230], "b": b[:230],
                        "pa": rows[i][1], "pb": rows[j][1],
                        "ra": rows[i][2], "rb": rows[j][2]})
            if sum(1 for o in out if o["band"] == band) >= 6:
                break

    data = json.loads((V / "boilerplate-data.json").read_text())
    data["pairs_boundary"] = out
    (V / "boilerplate-data.json").write_text(json.dumps(data))
    print(f"wrote {len(out)} boundary pairs "
          + " ".join(f"{b}:{sum(1 for o in out if o['band']==b)}" for b in BANDS))
    for o in out:
        print(f"  [{o['band']} {o['sim']}] {o['obj']}: {o['a'][:70]} || {o['b'][:70]}")


if __name__ == "__main__":
    main()
