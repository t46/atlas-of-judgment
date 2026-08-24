"""Non-obvious score analyses over the compact 2026 track.

  A. coverage  — per rating x object: share of reviews that inspect it at all
                 (and with >=1 negative unit): the silences of each score
  B. conditional profiles — P(object | negative unit, rating) and
                 P(object | positive unit, rating): does criticism change TOPIC
                 with score, or only volume?
  C. heterogeneity — per rating: mean total-variation distance of a review's
                 object profile to its rating centroid: which score is the most
                 scripted, which the most diverse
  D. split verdicts — same paper, one review <=2 and one >=8 (and separately
                 4 vs 6): per object, how the two reviewers' coverage and
                 negativity differ ON THE SAME PAPER

Writes data/analysis/iclr/unit-taxonomy-2026-v1/score-depth.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def load_scores() -> dict[str, int]:
    conn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    scores = {}
    for note_id, cj in conn.execute(
        "SELECT note_id, content_json FROM messages WHERE year = 2026 AND kind = 'official_review'"
    ):
        try:
            r = json.loads(cj).get("rating")
            if isinstance(r, str):
                r = int(str(r).split(":")[0].strip())
            if isinstance(r, int):
                scores[note_id] = r
        except (json.JSONDecodeError, ValueError):
            continue
    conn.close()
    return scores


def main() -> None:
    taxonomy = json.loads((V / "taxonomy-v1.json").read_text())
    obj_keys = [c["key"] for c in taxonomy["inspected_object"]]
    scores = load_scores()

    conn = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    # per review: paper, object counts, object-negative counts, object-positive counts
    reviews: dict[str, dict] = {}
    for review_id, paper_id, obj, valence in conn.execute(
        "SELECT u.review_id, u.paper_id, l.object_key, u.valence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        r = reviews.setdefault(review_id, {"paper": paper_id, "cnt": Counter(), "neg": Counter(), "pos": Counter()})
        r["cnt"][obj] += 1
        if valence == "negative":
            r["neg"][obj] += 1
        elif valence == "positive":
            r["pos"][obj] += 1
    conn.close()
    print(f"{len(reviews)} reviews loaded")

    # ---- A + B + C ----
    by_rating: dict[int, list[str]] = defaultdict(list)
    for rid in reviews:
        sc = scores.get(rid)
        if sc is not None:
            by_rating[sc].append(rid)

    coverage, cond_neg, cond_pos, hetero = {}, {}, {}, {}
    for rating, rids in sorted(by_rating.items()):
        if len(rids) < 100:
            continue
        touch = Counter()
        neg_touch = Counter()
        negs = Counter()
        poss = Counter()
        for rid in rids:
            r = reviews[rid]
            for o in r["cnt"]:
                touch[o] += 1
            for o in r["neg"]:
                if r["neg"][o]:
                    neg_touch[o] += 1
            negs.update(r["neg"])
            poss.update(r["pos"])
        n = len(rids)
        coverage[str(rating)] = {
            o: {"touch": round(touch[o] / n, 4), "neg_touch": round(neg_touch[o] / n, 4)}
            for o in obj_keys
        }
        tn, tp = sum(negs.values()), sum(poss.values())
        cond_neg[str(rating)] = {o: round(negs[o] / tn, 4) for o in obj_keys} if tn else {}
        cond_pos[str(rating)] = {o: round(poss[o] / tp, 4) for o in obj_keys} if tp else {}
        # heterogeneity: mean TV distance to centroid of object-share vectors
        import random
        random.seed(7)
        sample = random.sample(rids, min(4000, len(rids)))
        vecs = []
        for rid in sample:
            c = reviews[rid]["cnt"]
            tot = sum(c.values())
            vecs.append([c[o] / tot for o in obj_keys])
        centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(len(obj_keys))]
        tv = sum(sum(abs(v[i] - centroid[i]) for i in range(len(obj_keys))) / 2 for v in vecs) / len(vecs)
        hetero[str(rating)] = round(tv, 4)

    # ---- D. split verdicts on the same paper ----
    papers: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for rid in reviews:
        sc = scores.get(rid)
        if sc is not None:
            papers[reviews[rid]["paper"]].append((rid, sc))

    def split_pairs(lo_max: int, hi_min: int):
        agg = {o: {"lo_touch": 0, "hi_touch": 0, "lo_neg": 0, "hi_neg": 0} for o in obj_keys}
        n_pairs = 0
        for plist in papers.values():
            lows = [rid for rid, sc in plist if sc <= lo_max]
            highs = [rid for rid, sc in plist if sc >= hi_min]
            if not lows or not highs:
                continue
            n_pairs += 1
            for o in obj_keys:
                lo_t = any(reviews[r]["cnt"][o] for r in lows)
                hi_t = any(reviews[r]["cnt"][o] for r in highs)
                lo_n = any(reviews[r]["neg"][o] for r in lows)
                hi_n = any(reviews[r]["neg"][o] for r in highs)
                agg[o]["lo_touch"] += lo_t
                agg[o]["hi_touch"] += hi_t
                agg[o]["lo_neg"] += lo_n
                agg[o]["hi_neg"] += hi_n
        return {
            "n_papers": n_pairs,
            "objects": {
                o: {k: round(v / n_pairs, 4) for k, v in d.items()} for o, d in agg.items()
            } if n_pairs else {},
        }

    ratings_present = sorted({sc for plist in papers.values() for _, sc in plist})

    def split_exact(lo: int, hi: int):
        agg = {o: {"lo_touch": 0, "hi_touch": 0, "lo_neg": 0, "hi_neg": 0} for o in obj_keys}
        n_pairs = 0
        for plist in papers.values():
            lows = [rid for rid, sc in plist if sc == lo]
            highs = [rid for rid, sc in plist if sc == hi]
            if not lows or not highs:
                continue
            n_pairs += 1
            for o in obj_keys:
                agg[o]["lo_touch"] += any(reviews[r]["cnt"][o] for r in lows)
                agg[o]["hi_touch"] += any(reviews[r]["cnt"][o] for r in highs)
                agg[o]["lo_neg"] += any(reviews[r]["neg"][o] for r in lows)
                agg[o]["hi_neg"] += any(reviews[r]["neg"][o] for r in highs)
        return {
            "n_papers": n_pairs,
            "objects": {o: {k: round(v / n_pairs, 4) for k, v in d.items()} for o, d in agg.items()} if n_pairs else {},
        }

    pairwise = {}
    for i, lo in enumerate(ratings_present):
        for hi in ratings_present[i + 1:]:
            sp = split_exact(lo, hi)
            if sp["n_papers"] >= 100:
                pairwise[f"{lo}_{hi}"] = sp

    splits = {
        "extreme_2_vs_8": split_pairs(2, 8),
        "boundary_4_vs_6": split_pairs(4, 6),
        "pairwise": pairwise,
    }

    # ---- E. classic within-paper score dispersion ----
    import statistics
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    decision = {}
    for forum_id, dec in aconn.execute(
        "SELECT forum_id, decision FROM papers WHERE year = 2026 AND decision IS NOT NULL AND withdrawn = 0"
    ):
        decision[forum_id] = 1 if "accept" in dec.lower() else 0
    aconn.close()

    range_hist = Counter()
    spread_by_mean = defaultdict(list)
    accept_by_range_mid = defaultdict(lambda: [0, 0])  # papers with mean in [4.5, 6.5)
    n_multi = 0
    for pid, plist in papers.items():
        vals = [sc for _, sc in plist]
        if len(vals) < 2:
            continue
        n_multi += 1
        rng = max(vals) - min(vals)
        mean = sum(vals) / len(vals)
        range_hist[rng] += 1
        spread_by_mean[round(mean)].append(statistics.pstdev(vals))
        if 4.5 <= mean < 6.5 and pid in decision:
            b = accept_by_range_mid[min(rng, 6)]
            b[0] += decision[pid]
            b[1] += 1
    # unanimous vs divided at an EXACT mean (the banded version above confounds
    # range with mean inside the band; unanimity only exists at even means)
    accept_unan_div = {}
    for target in (4.0, 6.0):
        u, dv = [0, 0], [0, 0]
        for pid, plist in papers.items():
            vals = [sc for _, sc in plist]
            if len(vals) < 2 or pid not in decision:
                continue
            if abs(sum(vals) / len(vals) - target) > 1e-9:
                continue
            b = u if max(vals) == min(vals) else dv
            b[0] += decision[pid]
            b[1] += 1
        accept_unan_div[f"{target:.1f}"] = {
            "unan": {"accept": round(u[0] / u[1], 4), "n": u[1]},
            "div": {"accept": round(dv[0] / dv[1], 4), "n": dv[1]},
        }
    print("accept_unan_div:", accept_unan_div)

    dispersion = {
        "n_papers_multi": n_multi,
        "accept_unan_div": accept_unan_div,
        "range_hist": {str(k): v for k, v in sorted(range_hist.items())},
        "spread_by_mean": {
            str(m): {"mean_std": round(sum(v) / len(v), 3), "n": len(v)}
            for m, v in sorted(spread_by_mean.items()) if len(v) >= 50
        },
        "accept_by_range_mid": {
            str(r): {"accept": round(a / n, 4), "n": n}
            for r, (a, n) in sorted(accept_by_range_mid.items()) if n >= 100
        },
    }

    payload = {
        "dispersion": dispersion,
        "coverage": coverage,
        "cond_neg": cond_neg,
        "cond_pos": cond_pos,
        "heterogeneity": hetero,
        "splits": splits,
    }
    (V / "score-depth.json").write_text(json.dumps(payload) + "\n")
    print("heterogeneity:", hetero)
    print("split extreme papers:", splits["extreme_2_vs_8"]["n_papers"],
          "boundary papers:", splits["boundary_4_vs_6"]["n_papers"])


if __name__ == "__main__":
    main()
