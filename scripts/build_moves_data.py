"""The moves of the rebuttal: what authors do, and what actually moves.

Unit of analysis: a (forum, reviewer) pair where the reviewer wrote
post-author-response units (so the outcome — any weakened/reversed
judgment = SOFTENED, any strengthened = HARDENED — is observable).
The author side: every official_comment by the authors in that
reviewer's thread (walked up the replyto chain to the review note).
Reply sentences are embedded (bge-small, MPS) and k-means'd into 12
candidate "moves"; per pair we record which moves appear, then compare
P(soften | move present) against P(soften | move absent), overall and
inside reply-length quartiles (long replies both soften reviewers and
contain more of everything — the stratification keeps effort from
masquerading as technique).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/moves-raw.json.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
A = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"
K = 12
PAIR_CAP = 30000
SENT_PER_PAIR = 30


def main() -> None:
    rng = random.Random(46)
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    outcome: dict[tuple, dict] = {}
    for fid, rk, soft, hard in dc.execute(
        "SELECT forum_id, reviewer_key,"
        " SUM(judgment_change IN ('weakened','reversed')),"
        " SUM(judgment_change='strengthened')"
        " FROM units WHERE reviewer_role='official_reviewer'"
        " AND temporal_position='post_author_response' GROUP BY 1,2"
    ):
        outcome[(fid, rk)] = {"soft": soft > 0, "hard": hard > 0}
    charged = defaultdict(set)
    for fid, rk, obj in dc.execute(
        "SELECT DISTINCT u.forum_id, u.reviewer_key, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk=u.unit_pk"
        " WHERE u.reviewer_role='official_reviewer' AND u.temporal_position='initial_review'"
        " AND u.valence='negative'"
    ):
        charged[(fid, rk)].add(obj)
    # object-level softening (lifecycle semantics)
    soft_obj = defaultdict(set)
    for fid, rk, obj in dc.execute(
        "SELECT DISTINCT u.forum_id, u.reviewer_key, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk=u.unit_pk"
        " WHERE u.reviewer_role='official_reviewer' AND u.temporal_position='post_author_response'"
        " AND u.judgment_change IN ('weakened','reversed')"
    ):
        soft_obj[(fid, rk)].add(obj)
    dc.close()
    print(f"{len(outcome):,} (forum,reviewer) pairs with observable outcome")

    pairs = sorted(outcome)
    if len(pairs) > PAIR_CAP:
        pairs = rng.sample(pairs, PAIR_CAP)
    pairset = set(pairs)
    forums = {f for f, _ in pairs}

    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    review_owner: dict[str, tuple] = {}   # note_id -> (forum, tail)
    parent: dict[str, str] = {}
    author_notes: list[tuple] = []        # (forum, note_id, text)
    for fid, nid, rt, kind, role, sig, txt in ac.execute(
        "SELECT forum_id, note_id, replyto, kind, role, signature, content_text FROM messages"
        " WHERE kind IN ('official_review','official_comment')"
    ):
        if fid not in forums:
            continue
        parent[nid] = rt
        if kind == "official_review":
            review_owner[nid] = (fid, sig.rsplit("/", 1)[-1])
        elif role == "author" and txt:
            author_notes.append((fid, nid, txt))
    ac.close()

    def root_review(nid: str) -> str | None:
        seen = set()
        cur = nid
        while cur and cur not in seen:
            seen.add(cur)
            if cur in review_owner:
                return cur
            cur = parent.get(cur)
        return None

    # index pair keys by forum for tail matching
    keys_by_forum = defaultdict(list)
    for f, rk in pairset:
        keys_by_forum[f].append(rk)

    def resolve(fid: str, tail: str) -> str | None:
        ks = keys_by_forum.get(fid, ())
        for rk in ks:
            if rk == tail or f"Reviewer_{rk}" == tail or tail.endswith(f"_{rk}"):
                return rk
        return None

    texts_by_pair: dict[tuple, list[str]] = defaultdict(list)
    for fid, nid, txt in author_notes:
        rn = root_review(nid)
        if rn is None:
            continue
        _f, tail = review_owner[rn]
        rk = resolve(fid, tail)
        if rk is not None:
            texts_by_pair[(fid, rk)].append(txt)
    print(f"{len(texts_by_pair):,} pairs matched to author replies")

    SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
    sents: list[tuple] = []               # (pair_idx, sentence)
    pair_list = sorted(texts_by_pair)
    pi = {p: i for i, p in enumerate(pair_list)}
    n_sents_of = np.zeros(len(pair_list), dtype=np.int32)
    for p, txts in texts_by_pair.items():
        ss = [s.strip() for t in txts for s in SPLIT.split(t)
              if 40 <= len(s.strip()) <= 300]
        n_sents_of[pi[p]] = len(ss)
        if len(ss) > SENT_PER_PAIR:
            ss = rng.sample(ss, SENT_PER_PAIR)
        for s in ss:
            sents.append((pi[p], s))
    print(f"{len(sents):,} sentences to embed")

    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import MiniBatchKMeans
    model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="mps")
    X = model.encode([s for _, s in sents], batch_size=256,
                     normalize_embeddings=True).astype(np.float32)
    km = MiniBatchKMeans(n_clusters=K, batch_size=4096, n_init=6,
                         random_state=46).fit(X)
    sizes = np.bincount(km.labels_, minlength=K)
    order = np.argsort(-sizes)
    rank_of = {int(c): i for i, c in enumerate(order)}
    clusters = []
    for c in order:
        mem = np.where(km.labels_ == c)[0]
        ctr = km.cluster_centers_[c]
        sims = X[mem] @ ctr / (np.linalg.norm(ctr) + 1e-9)
        om = mem[np.argsort(-sims)]
        clusters.append({"n": int(len(mem)),
                         "exemplars": [sents[i][1][:230] for i in om[:8]]})

    # per-pair move presence
    present = np.zeros((len(pair_list), K), dtype=bool)
    for (p_idx, _s), lb in zip(sents, km.labels_):
        present[p_idx, rank_of[int(lb)]] = True
    soft = np.array([outcome[p]["soft"] for p in pair_list])
    hard = np.array([outcome[p]["hard"] for p in pair_list])
    qs = np.quantile(n_sents_of, [0.25, 0.5, 0.75])
    quart = np.digitize(n_sents_of, qs)

    def lift_stats(mask_move: np.ndarray) -> dict:
        r = {}
        a, b = soft[mask_move].mean(), soft[~mask_move].mean()
        r["p_soft_present"] = round(float(a), 4)
        r["p_soft_absent"] = round(float(b), 4)
        r["n_present"] = int(mask_move.sum())
        strat = []
        for q in range(4):
            m = quart == q
            if (m & mask_move).sum() >= 200 and (m & ~mask_move).sum() >= 200:
                strat.append(float(soft[m & mask_move].mean() - soft[m & ~mask_move].mean()))
        r["delta_within_quartiles"] = [round(x, 4) for x in strat]
        r["p_hard_present"] = round(float(hard[mask_move].mean()), 4)
        r["p_hard_absent"] = round(float(hard[~mask_move].mean()), 4)
        return r

    moves_stats = [lift_stats(present[:, j]) for j in range(K)]

    # move x object: softened on the charged object
    objs = sorted({o for s_ in charged.values() for o in s_})
    top_objs = [o for o, _ in Counter(o for p in pair_list for o in charged.get(p, ())).most_common(8)]
    mo = {}
    for o in top_objs:
        rows = np.array([o in charged.get(p, ()) for p in pair_list])
        yobj = np.array([o in soft_obj.get(p, ()) for p in pair_list])
        cell = {}
        for j in range(K):
            m = rows & present[:, j]
            m0 = rows & ~present[:, j]
            if m.sum() >= 300 and m0.sum() >= 300:
                cell[str(j)] = [round(float(yobj[m].mean()), 4),
                                round(float(yobj[m0].mean()), 4), int(m.sum())]
        mo[o] = cell

    payload = {
        "n_pairs": len(pair_list),
        "soft_base": round(float(soft.mean()), 4),
        "hard_base": round(float(hard.mean()), 4),
        "n_sents": len(sents),
        "sent_quartiles": [float(q) for q in qs],
        "clusters": clusters,
        "moves_stats": moves_stats,
        "move_by_object": mo,
        "caps": {"pair_cap": PAIR_CAP, "sent_per_pair": SENT_PER_PAIR},
    }
    (V / "moves-raw.json").write_text(json.dumps(payload))
    print(f"soft base {soft.mean():.3f}, hard base {hard.mean():.3f}")
    for j, (c, st) in enumerate(zip(clusters, moves_stats)):
        print(f"[m{j}] n={c['n']:>6,} soft {st['p_soft_present']:.3f} vs {st['p_soft_absent']:.3f}"
              f" strat {st['delta_within_quartiles']}  · {c['exemplars'][0][:80]}")


if __name__ == "__main__":
    main()
