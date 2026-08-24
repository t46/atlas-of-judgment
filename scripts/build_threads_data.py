"""The shapes of talk: the reply-tree of every review thread, 2018-2026.

No LLM anywhere — pure structure. Every official review roots a thread;
the official comments beneath it form its skeleton. Each thread is
classified by the turns actually taken:

  stump      — the review received no reply at all
  monologue  — authors replied, the reviewer never spoke again
  return     — the reviewer came back once after the authors
  volley     — the reviewer came back two or more times

Aggregates per year (the life and death of the conversation), plus
accept rates by shape (2026), plus a handful of real specimen trees
exported in full for the figure.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/threads-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def main() -> None:
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    msgs = {}      # note_id -> (year, forum, replyto, kind, role, cdate)
    children = defaultdict(list)
    for year, nid, fid, rt, kind, role, sig, cdate in ac.execute(
        "SELECT year, note_id, forum_id, replyto, kind, role, signature, cdate FROM messages"
        " WHERE kind IN ('official_review','official_comment','public_comment')"
    ):
        # early years (2018-21) tag reviewer/AC comments role='unknown';
        # recover the role from the signature tail (AnonReviewerN, Area_Chair)
        if role in (None, "unknown") and sig:
            tail = sig.rsplit("/", 1)[-1]
            if "AnonReviewer" in tail or "Reviewer" in tail:
                role = "reviewer"
            elif "Area_Chair" in tail:
                role = "area_chair"
        msgs[nid] = (year, fid, rt, kind, role, cdate or 0)
        children[rt].append(nid)
    decisions = {fid: dec for fid, dec in ac.execute("SELECT forum_id, decision FROM papers")}
    ac.close()
    print(f"{len(msgs):,} messages")

    def is_acc(dec):
        return bool(dec and re.search(r"accept|oral|poster|spotlight", dec, re.I))

    def is_dec(dec):
        return bool(dec and re.search(r"accept|oral|poster|spotlight|reject", dec, re.I))

    shapes = defaultdict(Counter)             # year -> shape -> n
    depth_hist = defaultdict(Counter)         # year -> n_msgs_in_thread -> n
    by_shape_dec = defaultdict(lambda: [0, 0])  # (year_bucket, shape) -> [n_decided, n_acc]
    returns_by_year = defaultdict(lambda: [0, 0])  # year -> [threads, reviewer_returned]

    def walk(root):
        """DFS the thread under a review; return stats."""
        stack = [(root, 0, False)]  # (node, depth, seen_author_above)
        n = 0
        maxd = 0
        returns = 0
        author_msgs = 0
        while stack:
            nid, d, seen_auth = stack.pop()
            for ch in children.get(nid, ()):
                _, _, _, kind, role, _ = msgs[ch]
                n += 1
                maxd = max(maxd, d + 1)
                a = seen_auth or role == "author"
                if role == "author":
                    author_msgs += 1
                if role == "reviewer" and seen_auth:
                    returns += 1
                stack.append((ch, d + 1, a))
        return n, maxd, returns, author_msgs

    specimens_pool = []
    for nid, (year, fid, rt, kind, role, cdate) in msgs.items():
        if kind != "official_review":
            continue
        n, maxd, returns, author_msgs = walk(nid)
        if n == 0:
            shape = "stump"
        elif returns == 0:
            shape = "monologue" if author_msgs else "aside"
        elif returns == 1:
            shape = "return"
        else:
            shape = "volley"
        shapes[year][shape] += 1
        depth_hist[year][min(n, 12)] += 1
        returns_by_year[year][0] += 1
        if returns:
            returns_by_year[year][1] += 1
        dec = decisions.get(fid)
        if year == 2026 and is_dec(dec):
            k = by_shape_dec[shape]
            k[0] += 1
            k[1] += 1 if is_acc(dec) else 0
        if year == 2026 and 6 <= n <= 14:
            specimens_pool.append((returns, maxd, n, fid))

    # ---- specimens: full trees of a few real 2026 forums ----
    specimens_pool.sort(reverse=True)
    chosen_forums = []
    for returns, maxd, n, fid in specimens_pool:
        if fid not in chosen_forums:
            chosen_forums.append(fid)
        if len(chosen_forums) >= 3:
            break
    # add a heavy-monologue forum and a stump-heavy forum
    forum_shape = defaultdict(Counter)
    for nid, (year, fid, rt, kind, role, cdate) in msgs.items():
        if kind == "official_review" and year == 2026:
            n, maxd, returns, author_msgs = walk(nid)
            forum_shape[fid]["stump" if n == 0 else ("volley" if returns >= 2 else "mono")] += 1
    for want in ("mono", "stump"):
        best = max(
            (f for f in forum_shape if sum(forum_shape[f].values()) >= 3 and f not in chosen_forums),
            key=lambda f: forum_shape[f][want] / sum(forum_shape[f].values()),
        )
        chosen_forums.append(best)

    specimens = []
    for fid in chosen_forums[:5]:
        nodes = []
        def emit(nid, parent_idx):
            _, _, _, kind, role, cdate = msgs[nid]
            idx = len(nodes)
            nodes.append({"p": parent_idx, "k": kind[0], "r": role or "?", "t": cdate})
            for ch in sorted(children.get(nid, ()), key=lambda c: msgs[c][5]):
                emit(ch, idx)
        roots = [nid for nid, m in msgs.items() if m[1] == fid and m[3] == "official_review"]
        for r in sorted(roots, key=lambda c: msgs[c][5]):
            emit(r, -1)
        dec = decisions.get(fid)
        specimens.append({"forum": fid, "decision": dec, "nodes": nodes})

    out = {
        "shapes": {str(y): dict(shapes[y]) for y in sorted(shapes)},
        "returns": {str(y): {"n": v[0], "returned": v[1]} for y, v in sorted(returns_by_year.items())},
        "accept_by_shape": {k: {"n": v[0], "acc": round(v[1] / v[0], 4) if v[0] else None}
                            for k, v in by_shape_dec.items()},
        "specimens": specimens,
    }
    (V / "threads-data.json").write_text(json.dumps(out))
    print("reviewer-return rate by year:")
    for y, v in sorted(returns_by_year.items()):
        print(f"  {y}: {v[1]/v[0]:.1%} of {v[0]:,} threads")
    print("shapes by year (share):")
    for y in sorted(shapes):
        tot = sum(shapes[y].values())
        print(f"  {y}: " + "  ".join(f"{k}:{v/tot:.1%}" for k, v in shapes[y].most_common()))
    print("2026 accept by shape:", out["accept_by_shape"])
    print("specimens:", [(s['forum'], len(s['nodes']), s['decision']) for s in specimens])


if __name__ == "__main__":
    main()
