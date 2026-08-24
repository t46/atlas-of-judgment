"""The moves of the rebuttal, read transparently: marker-defined moves.

The embedding clusters of moves-raw.json separate topics better than
tactics, so the headline figure uses deterministic, quotable markers
instead: concession, new experiments, pointing into the revision,
promising, disagreeing, clarifying, gratitude. Per (forum, reviewer)
pair (same universe as moves-raw), each marker's presence in the
author's replies is joined to whether the reviewer's post-response
units record any weakened/reversed judgment, overall and within
reply-length quartiles.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/moves-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
A = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

MARKERS = {
    "new_evidence": [re.compile(r"we (?:have |'ve )?(?:added|ran|run|conducted|performed|included) (?:new |additional |further |more )?(?:experiments?|results?|ablations?|analys[ei]s|comparisons?|baselines?|evaluations?)", re.I),
                     re.compile(r"new (?:experiments?|results?|ablations?) (?:are|have been|were) (?:added|included|reported)", re.I)],
    "revision": [re.compile(r"(?:in|to) the (?:revised|updated|new) (?:version|manuscript|paper|draft)|we have (?:revised|updated) the", re.I)],
    "promise": [re.compile(r"we will (?:add|include|run|conduct|address|revise|clarify|incorporate|update)|in the (?:final|camera.ready) version,? we will", re.I)],
    "concede": [re.compile(r"you are right|you're right|the reviewer is (?:right|correct)|we agree with|good catch|we acknowledge (?:this|that|the)", re.I)],
    "disagree": [re.compile(r"we (?:respectfully )?disagree|this (?:is|appears to be) a misunderstanding|we believe there (?:is|has been) a misunderstanding", re.I)],
    "clarify": [re.compile(r"to clarify|we would like to clarify|we apologize for (?:the|any) confusion|sorry for the confusion", re.I)],
    "gratitude": [re.compile(r"we (?:thank|appreciate)|thank you for", re.I)],
}
MNAME = {
    "new_evidence": ["The delivered experiment", "“we have added new experiments/results” — evidence produced, not promised"],
    "revision": ["The amended manuscript", "“in the revised version” — pointing at a change already made"],
    "promise": ["The promissory note", "“we will add…” — the fix deferred to a future version"],
    "concede": ["The concession", "“you are right” — granting the objection"],
    "disagree": ["The contest", "“we respectfully disagree” — refusing the objection"],
    "clarify": ["The clarification", "“to clarify” — recasting the paper's meaning, not its content"],
    "gratitude": ["The courtesy", "“we thank the reviewer” — the ritual register"],
}


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    outcome = {}
    for fid, rk, soft in dc.execute(
        "SELECT forum_id, reviewer_key, SUM(judgment_change IN ('weakened','reversed'))"
        " FROM units WHERE reviewer_role='official_reviewer'"
        " AND temporal_position='post_author_response' GROUP BY 1,2"
    ):
        outcome[(fid, rk)] = soft > 0
    charged = defaultdict(set)
    for fid, rk, obj in dc.execute(
        "SELECT DISTINCT u.forum_id, u.reviewer_key, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk=u.unit_pk"
        " WHERE u.reviewer_role='official_reviewer' AND u.temporal_position='initial_review'"
        " AND u.valence='negative'"
    ):
        charged[(fid, rk)].add(obj)
    soft_obj = defaultdict(set)
    for fid, rk, obj in dc.execute(
        "SELECT DISTINCT u.forum_id, u.reviewer_key, l.object_key"
        " FROM units u JOIN unit_labels l ON l.unit_pk=u.unit_pk"
        " WHERE u.reviewer_role='official_reviewer' AND u.temporal_position='post_author_response'"
        " AND u.judgment_change IN ('weakened','reversed')"
    ):
        soft_obj[(fid, rk)].add(obj)
    dc.close()
    forums = {f for f, _ in outcome}

    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    review_owner, parent = {}, {}
    author_notes = []
    for fid, nid, rt, kind, role, sig, txt in ac.execute(
        "SELECT forum_id, note_id, replyto, kind, role, signature, content_text FROM messages"
        " WHERE kind IN ('official_review','official_comment')"
    ):
        if fid not in forums:
            continue
        parent[nid] = rt
        if kind == "official_review":
            review_owner[nid] = sig.rsplit("/", 1)[-1]
        elif role == "author" and txt:
            author_notes.append((fid, nid, txt))
    ac.close()

    keys_by_forum = defaultdict(list)
    for f, rk in outcome:
        keys_by_forum[f].append(rk)

    def resolve(fid, tail):
        for rk in keys_by_forum.get(fid, ()):
            if rk == tail or f"Reviewer_{rk}" == tail or tail.endswith(f"_{rk}"):
                return rk
        return None

    def root(nid):
        seen = set()
        cur = nid
        while cur and cur not in seen:
            seen.add(cur)
            if cur in review_owner:
                return cur
            cur = parent.get(cur)
        return None

    text_of = defaultdict(list)
    for fid, nid, txt in author_notes:
        rn = root(nid)
        if rn is None:
            continue
        rk = resolve(fid, review_owner[rn])
        if rk is not None:
            text_of[(fid, rk)].append(txt)

    pairs = sorted(text_of)
    print(f"{len(pairs):,} pairs with author replies")
    y = np.array([outcome[p] for p in pairs])
    words = np.array([sum(len(t.split()) for t in text_of[p]) for p in pairs])
    qs = np.quantile(words, [0.25, 0.5, 0.75])
    quart = np.digitize(words, qs)

    pres = {}
    for mk, rxs in MARKERS.items():
        pres[mk] = np.array([any(r.search(t) for t in text_of[p] for r in rxs) for p in pairs])

    def stats(mask):
        r = {"present": round(float(y[mask].mean()), 4),
             "absent": round(float(y[~mask].mean()), 4),
             "n": int(mask.sum()),
             "prevalence": round(float(mask.mean()), 4)}
        deltas, all_d, all_n = [], [], []
        for q in range(4):
            m = quart == q
            n1 = int((m & mask).sum())
            d = float(y[m & mask].mean() - y[m & ~mask].mean()) if n1 else None
            all_d.append(round(d, 4) if d is not None else None)
            all_n.append(n1)
            if n1 >= 300 and (m & ~mask).sum() >= 300:
                deltas.append(round(d, 4))
        r["delta_by_quartile"] = deltas
        r["delta_by_quartile_all"] = all_d
        r["n_by_quartile"] = all_n
        return r

    moves = []
    for mk in MARKERS:
        st = stats(pres[mk])
        moves.append({"key": mk, "name": MNAME[mk][0], "def": MNAME[mk][1], **st})
    moves.sort(key=lambda m: -(m["present"] - m["absent"]))

    # effort curve: softening by reply-length decile
    dec = np.digitize(words, np.quantile(words, np.arange(0.1, 1, 0.1)))
    effort = [{"decile": int(d), "soft": round(float(y[dec == d].mean()), 4),
               "median_words": int(np.median(words[dec == d]))} for d in range(10)]

    # new_evidence x charged object: softened on that object
    objs = ["novelty", "empirical_scope", "baselines_ablations", "method_design",
            "clarity", "theory", "robustness_sensitivity", "related_work"]
    per_obj = {}
    for o in objs:
        rows = np.array([o in charged.get(p, ()) for p in pairs])
        yo = np.array([o in soft_obj.get(p, ()) for p in pairs])
        m1 = rows & pres["new_evidence"]
        m0 = rows & ~pres["new_evidence"]
        if m1.sum() >= 400 and m0.sum() >= 400:
            per_obj[o] = {"with": round(float(yo[m1].mean()), 4),
                          "without": round(float(yo[m0].mean()), 4), "n": int(m1.sum())}

    payload = {
        "n_pairs": len(pairs),
        "soft_base": round(float(y.mean()), 4),
        "word_quartiles": [int(q) for q in qs],
        "moves": moves,
        "effort": effort,
        "evidence_by_object": per_obj,
    }
    (V / "moves-data.json").write_text(json.dumps(payload))
    print(f"soft base {y.mean():.4f}")
    for m in moves:
        print(f"  {m['name']:>24s}  prev {m['prevalence']:.0%}  {m['present']:.3f} vs {m['absent']:.3f}  strat {m['delta_by_quartile']}")
    print("effort:", [(e["median_words"], e["soft"]) for e in effort])
    print("evidence by object:", per_obj)


if __name__ == "__main__":
    main()
