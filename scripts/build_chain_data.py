"""The syntax of reasoning: which inference form follows which.

Retrains the Plate III rhetoric classifier (same 600 gold labels, same
features) and predicts a form for every initial-review official-reviewer
unit in the Direct track, then counts form->form transitions between
consecutive units of the same review (ordered by unit_index). The null
model preserves each review's own form composition: expected transitions
are computed from within-review marginals, so a form that is merely
common cannot masquerade as "attracting" its neighbors.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/chain-data.json.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"

from build_rhetoric_v2 import FORMS, MERGE, feats  # noqa: E402


def main() -> None:
    sample = json.loads((D / "rhetoric-sample.json").read_text())
    labels = {int(i): MERGE[l] for i, l in csv.reader(open(D / "rhetoric-labels-analyst.csv"))}
    idxs = sorted(labels)
    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    E = model.encode([sample[i]["text"] for i in idxs], batch_size=128,
                     normalize_embeddings=True)
    F = np.array([feats(sample[i]["text"]) for i in idxs])
    clf = LogisticRegression(max_iter=3000, C=8.0)
    clf.fit(np.hstack([E, F * 0.5]), [labels[i] for i in idxs])

    emb = np.lib.format.open_memmap(D / "reasoning-embeddings.npy", mode="r")
    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)

    # predict forms for all initial-review official units, keyed by review
    seqs: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    CH = 40000
    total = conn.execute("SELECT MAX(unit_pk) FROM units").fetchone()[0]
    for start in range(0, total, CH):
        rows = conn.execute(
            "SELECT unit_pk, custom_id, reviewer_key, unit_index, reasoning"
            " FROM units WHERE unit_pk > ? AND unit_pk <= ?"
            " AND reviewer_role='official_reviewer' AND temporal_position='initial_review'"
            " ORDER BY unit_pk",
            (start, start + CH),
        ).fetchall()
        if not rows:
            continue
        Ei = np.asarray(emb[[r[0] - 1 for r in rows]], dtype=np.float32)
        Fi = np.array([feats(r[4]) for r in rows])
        preds = clf.predict(np.hstack([Ei, Fi * 0.5]))
        for (pk, fid, rk, ui, _t), p in zip(rows, preds):
            seqs[(fid, rk)].append((ui, p))
        print(f"predicted through pk {rows[-1][0]:,}", flush=True)
    conn.close()

    fi = {f: i for i, f in enumerate(FORMS)}
    obs = np.zeros((6, 6))
    exp = np.zeros((6, 6))
    start_c, end_c, all_c = Counter(), Counter(), Counter()
    n_seq = 0
    for units in seqs.values():
        if len(units) < 2:
            continue
        units.sort()
        forms = [f for _, f in units]
        n_seq += 1
        start_c[forms[0]] += 1
        end_c[forms[-1]] += 1
        for f in forms:
            all_c[f] += 1
        L = len(forms)
        cnt = Counter(forms)
        # expected transitions under within-review shuffle:
        # E[a->b] = (L-1) * c_a*c_b/(L*(L-1)) = c_a*c_b/L for a!=b
        # E[a->a] = c_a*(c_a-1)/L
        for a, ca in cnt.items():
            for b, cb in cnt.items():
                e = ca * (cb - 1) / L if a == b else ca * cb / L
                exp[fi[a], fi[b]] += e
        for a, b in zip(forms, forms[1:]):
            obs[fi[a], fi[b]] += 1

    lift = (obs + 1) / (exp + 1)
    tot = sum(all_c.values())
    payload = {
        "forms": FORMS,
        "n_reviews": n_seq,
        "n_units": tot,
        "obs": obs.round(0).tolist(),
        "exp": exp.round(1).tolist(),
        "lift": lift.round(3).tolist(),
        "start_share": {f: round(start_c[f] / n_seq, 4) for f in FORMS},
        "end_share": {f: round(end_c[f] / n_seq, 4) for f in FORMS},
        "overall_share": {f: round(all_c[f] / tot, 4) for f in FORMS},
    }
    (V / "chain-data.json").write_text(json.dumps(payload))
    print(f"{n_seq:,} sequences, {tot:,} units")
    for i, a in enumerate(FORMS):
        row = "  ".join(f"{lift[i, j]:.2f}" for j in range(6))
        print(f"{a:>7s}  {row}")
    print("start:", {f: round(start_c[f] / n_seq, 3) for f in FORMS})
    print("end:  ", {f: round(end_c[f] / n_seq, 3) for f in FORMS})


if __name__ == "__main__":
    main()
