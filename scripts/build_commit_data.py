"""The commitment point: how early is the verdict readable?

For each 2026 review (review-level track), predict whether the reviewer's
rating lands at 6+ from only the first k logic units (object x valence
counts + negative share + mean confidence), k = 1..15, then from the full
review. 3-fold CV AUC per k. A second curve uses valence counts alone
(no objects), separating "what they looked at" from "how it went".

Writes data/analysis/iclr/unit-taxonomy-2026-v1/commit-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
A = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

KS = list(range(1, 16))


def main() -> None:
    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    rating = {}
    for nid, cj in ac.execute(
        "SELECT note_id, content_json FROM messages WHERE kind='official_review' AND year=2026"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, int):
            rating[nid] = r
    ac.close()
    vals = sorted(set(rating.values()))
    print("rating values:", vals[:12], "n:", len(rating))

    uc = sqlite3.connect(f"file:{V / 'units.sqlite3'}?mode=ro", uri=True)
    objs = [r[0] for r in uc.execute(
        "SELECT DISTINCT object_key FROM unit_labels ORDER BY 1")]
    oi = {o: i for i, o in enumerate(objs)}
    vi = {"negative": 0, "positive": 1, "mixed": 2, "neutral": 2}

    reviews: dict[str, list] = defaultdict(list)
    for rid, ui, ok, val, conf in uc.execute(
        "SELECT u.review_id, u.unit_index, l.object_key, u.valence, u.confidence"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
    ):
        if rid in rating:
            reviews[rid].append((ui, ok, val, conf if isinstance(conf, (int, float)) else 0.5))
    uc.close()

    rids = sorted(reviews)
    y = np.array([1 if rating[r] >= 6 else 0 for r in rids])
    seqs = []
    for r in rids:
        u = sorted(reviews[r])
        seqs.append([(oi[ok], vi.get(val, 2), c) for _, ok, val, c in u])
    print(f"{len(rids):,} reviews, positive share {y.mean():.3f}")

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    nO = len(objs)

    def featurize(k: int | None, valence_only: bool) -> np.ndarray:
        dim = 3 if valence_only else nO * 3 + 2
        X = np.zeros((len(seqs), dim), dtype=np.float32)
        for i, s in enumerate(seqs):
            sub = s if k is None else s[:k]
            n = len(sub)
            if n == 0:
                continue
            neg = sum(1 for _, v, _c in sub if v == 0)
            if valence_only:
                pos = sum(1 for _, v, _c in sub if v == 1)
                X[i] = [neg / n, pos / n, 1 - (neg + pos) / n]
            else:
                for o, v, _c in sub:
                    X[i, o * 3 + v] += 1
                X[i, nO * 3] = neg / n
                X[i, nO * 3 + 1] = np.mean([c for _, _v, c in sub])
        return X

    def cv_auc(X: np.ndarray) -> float:
        aucs = []
        for tr, te in StratifiedKFold(3, shuffle=True, random_state=46).split(X, y):
            m = LogisticRegression(max_iter=2000, C=1.0)
            m.fit(X[tr], y[tr])
            aucs.append(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]))
        return float(np.mean(aucs))

    full_auc = cv_auc(featurize(None, False))
    curve, curve_val = [], []
    for k in KS:
        a = cv_auc(featurize(k, False))
        av = cv_auc(featurize(k, True))
        curve.append(round(a, 4))
        curve_val.append(round(av, 4))
        print(f"k={k:>2d}  AUC {a:.4f}  valence-only {av:.4f}", flush=True)
    full_val = cv_auc(featurize(None, True))

    # The pooled curve saturates partly because a median review is exhausted by
    # k=6. Re-ask the question of reviews that actually have a tail: restrict to
    # reviews with >= LONG units, where "does the rest add anything?" is a real
    # question, and rebuild the curve inside that subset only.
    LONG = 10
    long_idx = np.array([i for i, s in enumerate(seqs) if len(s) >= LONG])
    y_all, seqs_all = y, seqs
    y = y_all[long_idx]
    seqs = [seqs_all[i] for i in long_idx]
    long_curve, long_curve_val = [], []
    for k in KS:
        long_curve.append(round(cv_auc(featurize(k, False)), 4))
        long_curve_val.append(round(cv_auc(featurize(k, True)), 4))
    long_full = cv_auc(featurize(None, False))
    print(f"long reviews (>= {LONG} units): n={len(long_idx):,}, full AUC {long_full:.4f}")
    print("  curve:", long_curve)
    y, seqs = y_all, seqs_all

    # ---- Exploratory extension A (2026-08-21): the ceiling of the words themselves.
    # The 0.78 ceiling above is the ceiling of coarse object x valence features.
    # How much more does the raw review text hold? TF-IDF bag-of-words over the
    # concatenated review fields, same target, same 3-fold CV. Declared
    # exploratory; reported whatever the number comes out to be.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline
    ac = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    texts_by_id = {}
    for nid, cj in ac.execute(
        "SELECT note_id, content_json FROM messages WHERE kind='official_review' AND year=2026"
    ):
        try:
            d = json.loads(cj)
        except json.JSONDecodeError:
            continue
        parts = []
        for f in ("summary", "strengths", "weaknesses", "questions"):
            v = d.get(f)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, dict) and isinstance(v.get("value"), str):
                parts.append(v["value"])
        texts_by_id[nid] = "\n".join(parts)
    ac.close()
    docs = [texts_by_id.get(r, "") for r in rids]
    text_aucs = []
    for tr, te in StratifiedKFold(3, shuffle=True, random_state=46).split(np.zeros(len(y)), y):
        pipe = make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2), min_df=5, max_features=200000,
                            sublinear_tf=True, strip_accents="unicode"),
            LogisticRegression(max_iter=2000, C=4.0),
        )
        pipe.fit([docs[i] for i in tr], y[tr])
        text_aucs.append(roc_auc_score(y[te], pipe.predict_proba([docs[i] for i in te])[:, 1]))
    text_auc = float(np.mean(text_aucs))
    print(f"raw-text (TF-IDF) AUC: {text_auc:.4f}  folds {[round(a,4) for a in text_aucs]}")

    # ---- Exploratory extension B (2026-08-21): the weights of the tally.
    # If the score is a tally of negatives, does one negative count the same
    # whatever its topic? Model: whole-review per-object NEGATIVE counts
    # (+ total positive and hedged counts) vs a pure-count model
    # (total negatives + positives + hedged). Coefficients are per one
    # negative unit and directly comparable across objects.
    def tally_X(per_object: bool) -> np.ndarray:
        dim = (nO + 2) if per_object else 3
        X = np.zeros((len(seqs), dim), dtype=np.float32)
        for i, sq in enumerate(seqs):
            neg_o = np.zeros(nO)
            pos = hed = 0
            for o, v, _c in sq:
                if v == 0:
                    neg_o[o] += 1
                elif v == 1:
                    pos += 1
                else:
                    hed += 1
            if per_object:
                X[i, :nO] = neg_o
                X[i, nO] = pos
                X[i, nO + 1] = hed
            else:
                X[i] = [neg_o.sum(), pos, hed]
        return X
    Xw, Xc = tally_X(True), tally_X(False)
    auc_weighted = cv_auc(Xw)
    auc_count = cv_auc(Xc)
    print(f"tally: pure-count AUC {auc_count:.4f} vs per-object-weight AUC {auc_weighted:.4f}")
    coefs = []
    for tr, _te in StratifiedKFold(3, shuffle=True, random_state=46).split(Xw, y):
        m = LogisticRegression(max_iter=2000, C=1.0)
        m.fit(Xw[tr], y[tr])
        coefs.append(m.coef_[0][:nO])
    coefs = np.array(coefs)
    w_mean, w_std = coefs.mean(0), coefs.std(0)
    for o in sorted(range(nO), key=lambda i: w_mean[i]):
        print(f"  {objs[o]:24s} {w_mean[o]:+.3f} ± {w_std[o]:.3f}")

    n_units = [len(s) for s in seqs]
    payload = {
        "text_auc": round(text_auc, 4),
        "text_auc_folds": [round(a, 4) for a in text_aucs],
        "tally": {
            "auc_count": round(auc_count, 4),
            "auc_weighted": round(auc_weighted, 4),
            "objects": objs,
            "coef": {objs[o]: round(float(w_mean[o]), 4) for o in range(nO)},
            "coef_fold_std": {objs[o]: round(float(w_std[o]), 4) for o in range(nO)},
        },
        "unit_count_deciles": [int(q) for q in np.quantile(n_units, np.arange(0.1, 1.0, 0.1))],
        "long_threshold": LONG,
        "n_long": int(len(long_idx)),
        "auc_long": long_curve,
        "auc_long_valence_only": long_curve_val,
        "auc_long_full": round(long_full, 4),
        "n_reviews": len(rids),
        "pos_share": round(float(y.mean()), 4),
        "median_units": float(np.median(n_units)),
        "mean_units": round(float(np.mean(n_units)), 2),
        "ks": KS,
        "auc": curve,
        "auc_valence_only": curve_val,
        "auc_full": round(full_auc, 4),
        "auc_full_valence_only": round(full_val, 4),
        "rating_values": vals,
        "target": "rating >= 6",
    }
    (V / "commit-data.json").write_text(json.dumps(payload))
    print("full:", full_auc, "valence-only full:", full_val)


if __name__ == "__main__":
    main()
