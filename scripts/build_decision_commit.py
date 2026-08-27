"""Decision legibility: how well does the panel's ACTUAL accept/reject verdict
read off the criticism alone, at the paper level?

Mirrors build_commit_data.py (Plate VI, review-level rating>=6 from
verdict-tally features and a raw-text embedding ceiling), but the unit of
prediction here is the PAPER (forum), not the review, and the target is the
decision the tribunal actually handed down, not a rating threshold.

Target and corpus were fixed before any AUC was computed:
  - Decision comes from data/processed/iclr/analysis.sqlite3 `papers`
    (decision IS NOT NULL, withdrawn=0, desk_rejected=0). 1 = any "accept"
    variant in the decision string, else 0. Year coverage of decisions in
    this corpus: 2018, 2020-2026 (2019 has none at all -- excluded, not a
    choice). Note: 2018's "Invite to Workshop Track" (90 papers) does not
    match "accept" and so falls into the 0 bucket under this rule -- it is
    not a real reject, just the nearest binary bucket; flagged, not hidden.
  - Criticism comes from unit-taxonomy-direct-v1/units.sqlite3 (forum-level,
    2018-2026, same 12-object taxonomy as Plate VI), restricted to
    reviewer_role='official_reviewer' and temporal_position='initial_review'
    -- i.e. the reviews as submitted, before rebuttal, before any
    meta-reviewer had weighed in. meta_assessment units are excluded on
    purpose: a meta-review is written with the decision already forming in
    the AC's head, so including it would not be "criticism alone" in the
    sense Plate VI uses the phrase.
  - Scores are excluded from every headline model. mean rating is reported
    only as a separately-labelled reference ceiling.

Models (5-fold stratified CV, logistic regression, seed 7):
  A. tally         -- 12 per-object negative-unit counts + total positive +
                       total hedged (mixed/uncertain/conditional) count,
                       summed across all of the paper's initial official
                       reviews. Same feature shape as build_commit_data.py's
                       per-object tally model.
  B. tally_nrev     -- A + number of distinct official reviewers.
  C. text (ceiling) -- mean-pooled bge-small-en-v1.5 embedding (mps,
                       normalized) of the paper's negative-unit judgment
                       texts, then logistic regression. ~513k texts total
                       across the decided corpus, which embeds in a couple
                       of minutes on mps -- no subsampling needed.
  baselines: base rate; nrev_only (number of reviewers alone); rating_ref
       (mean rating alone -- reference only, not part of the headline).
       Rating parsing crosses 4 scoring-form eras (string "N: label" in
       2018/2021/2024, `recommendation` instead of `rating` in 2022/2023,
       bare int in 2025/2026); pooling raw scores across those eras for a
       single reference number is the known moving-ruler caveat (method
       Sec. 10) -- the per-year breakdown below is the honest version of
       this baseline.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/decision-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
OUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

SEED = 7
N_FOLDS = 5
VI = {"negative": 0, "positive": 1}  # everything else (mixed/uncertain/conditional) -> hedged


def parse_rating(content_json: str) -> int | None:
    try:
        d = json.loads(content_json)
    except json.JSONDecodeError:
        return None
    v = d.get("rating")
    if v is None:
        v = d.get("recommendation")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        m = re.match(r"\s*(\d+)", v)
        if m:
            return int(m.group(1))
    return None


def main() -> None:
    t_start = time.time()

    # ---- decisions ----
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    decision: dict[str, int] = {}
    decision_year: dict[str, int] = {}
    decision_raw: dict[str, str] = {}
    for forum_id, dec, year in aconn.execute(
        "SELECT forum_id, decision, year FROM papers"
        " WHERE decision IS NOT NULL AND withdrawn = 0 AND desk_rejected = 0"
    ):
        decision[forum_id] = 1 if "accept" in dec.lower() else 0
        decision_year[forum_id] = year
        decision_raw[forum_id] = dec
    years_with_decisions = sorted({y for y in decision_year.values()})
    print(f"decision rows: {len(decision):,}, years: {years_with_decisions}")
    workshop_invites = sum(1 for d in decision_raw.values() if "workshop" in d.lower())
    print(f"  quirk: {workshop_invites} 'Invite to Workshop Track' rows bucketed as 0 (not accept, not a real reject)")

    # ---- mean rating per forum (reference baseline only) ----
    rating_sum: dict[str, float] = defaultdict(float)
    rating_n: dict[str, int] = defaultdict(int)
    for forum_id, cj in aconn.execute(
        "SELECT forum_id, content_json FROM messages WHERE kind='official_review'"
    ):
        r = parse_rating(cj)
        if r is not None:
            rating_sum[forum_id] += r
            rating_n[forum_id] += 1
    mean_rating = {f: rating_sum[f] / rating_n[f] for f in rating_sum}
    aconn.close()
    print(f"forums with >=1 parseable rating: {len(mean_rating):,}")

    # ---- criticism units (direct-v1, forum-level, official initial reviews) ----
    dconn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    objs = [r[0] for r in dconn.execute("SELECT DISTINCT object_key FROM unit_labels ORDER BY 1")]
    oi = {o: i for i, o in enumerate(objs)}
    nO = len(objs)
    print(f"{nO} objects: {objs}")

    neg_counts: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(nO, dtype=np.float32))
    pos_counts: Counter = Counter()
    hed_counts: Counter = Counter()
    reviewers: dict[str, set] = defaultdict(set)
    forum_years: dict[str, int] = {}
    for forum_id, year, reviewer_key, ok, val in dconn.execute(
        "SELECT u.forum_id, u.year, u.reviewer_key, l.object_key, u.valence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'"
    ):
        reviewers[forum_id].add(reviewer_key)
        forum_years[forum_id] = year
        v = VI.get(val, 2)
        if v == 0:
            neg_counts[forum_id][oi[ok]] += 1
        elif v == 1:
            pos_counts[forum_id] += 1
        else:
            hed_counts[forum_id] += 1

    # ---- final corpus: decided (accept/reject-coded) papers with >=1 official initial review ----
    forums = sorted(set(decision) & set(reviewers))
    fidx = {f: i for i, f in enumerate(forums)}
    print(f"decided papers with >=1 official initial-review unit: {len(forums):,}")

    y = np.array([decision[f] for f in forums])
    years_arr = np.array([forum_years[f] for f in forums])
    n_reviews = np.array([len(reviewers[f]) for f in forums], dtype=np.float32)
    Xtally = np.zeros((len(forums), nO + 2), dtype=np.float32)
    for i, f in enumerate(forums):
        Xtally[i, :nO] = neg_counts.get(f, np.zeros(nO))
        Xtally[i, nO] = pos_counts.get(f, 0)
        Xtally[i, nO + 1] = hed_counts.get(f, 0)
    Xtally_nrev = np.hstack([Xtally, n_reviews.reshape(-1, 1)])
    Xnrev = n_reviews.reshape(-1, 1)

    base_rate = float(y.mean())
    print(f"n_papers={len(forums):,}  base_rate(accept)={base_rate:.4f}")
    print(f"  per-year n / accept-rate: " + ", ".join(
        f"{yr}:{int((years_arr == yr).sum())}/{y[years_arr == yr].mean():.3f}"
        for yr in years_with_decisions if (years_arr == yr).sum() > 0
    ))

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    def cv_auc(X: np.ndarray, yy: np.ndarray, C: float = 1.0) -> tuple[float, list[float]]:
        aucs = []
        for tr, te in StratifiedKFold(N_FOLDS, shuffle=True, random_state=SEED).split(X, yy):
            m = LogisticRegression(max_iter=2000, C=C)
            m.fit(X[tr], yy[tr])
            aucs.append(roc_auc_score(yy[te], m.predict_proba(X[te])[:, 1]))
        return float(np.mean(aucs)), [round(a, 4) for a in aucs]

    def per_year_auc(X: np.ndarray, yy: np.ndarray, yrs: np.ndarray) -> dict[int, float | None]:
        out: dict[int, float | None] = {}
        for yr in years_with_decisions:
            m = yrs == yr
            # need every class populated enough for 5 stratified folds, not just 20 total
            cls = np.bincount(yy[m].astype(int)) if m.sum() else np.array([0])
            if m.sum() < 20 or len(set(yy[m].tolist())) < 2 or cls.min() < N_FOLDS:
                out[yr] = None
                continue
            out[yr] = round(cv_auc(X[m], yy[m])[0], 4)
        return out

    auc_tally, folds_tally = cv_auc(Xtally, y)
    auc_tally_nrev, folds_tally_nrev = cv_auc(Xtally_nrev, y)
    auc_nrev, folds_nrev = cv_auc(Xnrev, y)
    print(f"tally AUC {auc_tally:.4f} {folds_tally}")
    print(f"tally+nrev AUC {auc_tally_nrev:.4f} {folds_tally_nrev}")
    print(f"nrev-only AUC {auc_nrev:.4f} {folds_nrev}")

    py_tally = per_year_auc(Xtally, y, years_arr)
    py_tally_nrev = per_year_auc(Xtally_nrev, y, years_arr)
    print("per-year tally AUC:", py_tally)

    # coefficients (per-object, from the plain tally model, fold-averaged)
    coefs = []
    for tr, _te in StratifiedKFold(N_FOLDS, shuffle=True, random_state=SEED).split(Xtally, y):
        m = LogisticRegression(max_iter=2000, C=1.0)
        m.fit(Xtally[tr], y[tr])
        coefs.append(m.coef_[0][:nO])
    coefs = np.array(coefs)
    w_mean, w_std = coefs.mean(0), coefs.std(0)
    coef_sorted = sorted(range(nO), key=lambda i: w_mean[i])
    print("per-object coefficients (tally model, ascending = worse for accept odds):")
    for o in coef_sorted:
        print(f"  {objs[o]:24s} {w_mean[o]:+.4f} +/- {w_std[o]:.4f}")

    # ---- rating_ref (reference only, not headline) ----
    rating_forums = [f for f in forums if f in mean_rating]
    ratf_idx = np.array([fidx[f] for f in rating_forums])
    Xrating = np.array([[mean_rating[f]] for f in rating_forums], dtype=np.float32)
    yrating = y[ratf_idx]
    years_rating = years_arr[ratf_idx]
    auc_rating, folds_rating = cv_auc(Xrating, yrating)
    py_rating = per_year_auc(Xrating, yrating, years_rating)
    print(f"rating_ref AUC {auc_rating:.4f} n={len(rating_forums):,} (reference only)")
    print("per-year rating_ref AUC:", py_rating)

    # ---- text ceiling: mean-pooled bge-small-en-v1.5 of negative-unit judgment texts ----
    t0 = time.time()
    forum_set = set(forums)
    texts_by_forum: dict[str, list[str]] = defaultdict(list)
    for forum_id, judgment in dconn.execute(
        "SELECT u.forum_id, u.judgment FROM units u"
        " WHERE u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'"
        " AND u.valence = 'negative'"
    ):
        if forum_id in forum_set:
            texts_by_forum[forum_id].append(judgment)
    dconn.close()
    n_texts = sum(len(v) for v in texts_by_forum.values())
    n_no_neg = sum(1 for f in forums if f not in texts_by_forum)
    print(f"embedding {n_texts:,} negative-unit judgment texts across {len(texts_by_forum):,} papers"
          f" ({n_no_neg} decided papers have zero negative initial-review units -> zero vector)")

    from sentence_transformers import SentenceTransformer
    import torch
    model = SentenceTransformer("BAAI/bge-small-en-v1.5",
                                device="mps" if torch.backends.mps.is_available() else None)
    all_texts, owner = [], []
    for f in forums:
        for txt in texts_by_forum.get(f, []):
            all_texts.append(txt)
            owner.append(f)
    E = model.encode(all_texts, batch_size=256, normalize_embeddings=True, show_progress_bar=False)
    dim = E.shape[1]
    pooled = np.zeros((len(forums), dim), dtype=np.float32)
    counts = np.zeros(len(forums), dtype=np.int32)
    for txt_i, f in enumerate(owner):
        i = fidx[f]
        pooled[i] += E[txt_i]
        counts[i] += 1
    nz = counts > 0
    pooled[nz] /= counts[nz, None]
    embed_dt = time.time() - t0
    print(f"embedding + pooling took {embed_dt:.1f}s")

    auc_text, folds_text = cv_auc(pooled, y, C=1.0)
    py_text = per_year_auc(pooled, y, years_arr)
    print(f"text (embedding ceiling) AUC {auc_text:.4f} {folds_text}")
    print("per-year text AUC:", py_text)

    payload = {
        "target": "paper decision (accept vs reject), tribunal-level",
        "decision_rule": "1 if 'accept' in decision.lower() else 0; decision NOT NULL, withdrawn=0, desk_rejected=0",
        "years_with_decisions": years_with_decisions,
        "workshop_invite_quirk_n": workshop_invites,
        "n_papers": len(forums),
        "base_rate": round(base_rate, 4),
        "seed": SEED,
        "n_folds": N_FOLDS,
        "objects": objs,
        "aucs": {
            "tally": round(auc_tally, 4),
            "tally_nrev": round(auc_tally_nrev, 4),
            "text": round(auc_text, 4),
            "nrev_only": round(auc_nrev, 4),
            "rating_ref": round(auc_rating, 4),
        },
        "auc_folds": {
            "tally": folds_tally,
            "tally_nrev": folds_tally_nrev,
            "text": folds_text,
            "nrev_only": folds_nrev,
            "rating_ref": folds_rating,
        },
        "per_year_auc": {
            "tally": py_tally,
            "tally_nrev": py_tally_nrev,
            "text": py_text,
            "rating_ref": py_rating,
        },
        "n_rating_ref": len(rating_forums),
        "n_text_zero_vector": int(n_no_neg),
        "tally_coef": {
            "objects_sorted_ascending": [objs[o] for o in coef_sorted],
            "coef": {objs[o]: round(float(w_mean[o]), 4) for o in range(nO)},
            "coef_fold_std": {objs[o]: round(float(w_std[o]), 4) for o in range(nO)},
        },
        "notes": (
            "Criticism units = official_reviewer, temporal_position=initial_review only "
            "(pre-rebuttal, pre-meta-review). rating_ref pools raw scores across 4 scoring-form "
            "eras (2018-2026) -- see per_year_auc.rating_ref for the honest per-era version. "
            "2019 has no decision data in this corpus and is excluded entirely, not by choice."
        ),
        "wall_clock_seconds": round(time.time() - t_start, 1),
    }
    out = OUT_DIR / "decision-data.json"
    out.write_text(json.dumps(payload) + "\n")
    print(f"{out} written. total wall clock {payload['wall_clock_seconds']}s")


if __name__ == "__main__":
    main()
