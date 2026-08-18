"""Rhetoric v2: classifier-based inference-form labels for all Direct units.

Training data: 600 units hand-labeled by the analyst LLM (labels.csv, 8 codes
merged to 6 forms). Features: cached bge-small embeddings of the reasoning
text + 8 binary surface-marker features + length. Model: logistic regression
(C=8). Validation: 5-fold CV on the training set; a cheap second annotator
(Haiku) agreed with the gold labels on only 42/100 merged — reported, not used.

Requires reasoning-embeddings.npy (scripts/embed_reasoning_direct.py).
Writes data/analysis/iclr/unit-taxonomy-direct-v1/rhetoric-v2.json.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
LABELS_CSV = DIRECT_DIR / "rhetoric-labels-analyst.csv"
MERGE = {"REQ": "NORM", "DEP": "BLOCK", "RIV": "DOUBT", "PRE": "ANCHOR",
         "SCO": "REACH", "CON": "REACH", "EVW": "WEIGH", "VAL": "WEIGH"}
FORMS = ["NORM", "BLOCK", "DOUBT", "ANCHOR", "REACH", "WEIGH"]

RX = [
    re.compile(r"\bwithout\b|\bunless\b|\bin the absence of\b|\babsent\b|\bcannot be (verified|assessed|determined|ruled|attributed|reproduced|evaluated|confirmed)\b|\bprevents?\b|\bhinders?\b", re.I),
    re.compile(r"\bshould\b|\bmust\b|\brequires?\b|\brequired\b|\bneeds? to\b|\bexpected\b|\bprerequisite\b|\bnecessary\b|\bessential\b|\bstandards?\b|\bcriterion\b|\bbar\b", re.I),
    re.compile(r"\bcould be\b|\bmay (be|reflect|stem|arise|indicate)\b|\bmight\b|\bartifact\b|\bconfound|\bmemoriz|\bleakage\b|\brather than\b|\binstead of\b|\bredundant\b", re.I),
    re.compile(r"\bprior (work|art|methods)\b|\bexisting (work|methods|literature|approaches|solutions)\b|\bstate.of.the.art\b|\bsota\b|\bbaselines?\b|\bliterature\b|\bnovelty\b|\bcitations?\b|\bcompared? (to|with)\b", re.I),
    re.compile(r"\bgeneraliz|\bbeyond\b|\bbroader\b|\bscope\b|\breal.world\b|\bunrealistic\b|\bapplicab|\btoy\b|\bnarrow\b|\blimited (scope|scale)\b|\bdomains?\b|\bsettings\b|\bscales?\b", re.I),
    re.compile(r"\bpractical\b|\badoption\b|\bdeploy|\bcost\b|\boverhead\b|\bimpact\b|\butility\b|\befficien|\bfeasib", re.I),
    re.compile(r"\bsufficient\b|\binsufficient\b|\bevidence\b|\bvalidate[sd]?\b|\bdemonstrate[sd]?\b|\bsupports?\b|\bresolve[sd]?\b|\baddress(es|ed)?\b|\bconfidence\b|\boutweigh|\bsignifican(t|ce)\b|\bconvincing\b|\bunresolved\b", re.I),
    re.compile(r"\bclarity\b|\bclear\b|\bwell.written\b|\belegan|\bvaluable\b|\bimportant\b|\binteresting\b|\bstrength\b|\bweakness\b|\bflaw\b|\bpoor\b|\bcareless|\bprofessional", re.I),
]


def feats(text: str) -> list[float]:
    return [1.0 if r.search(text) else 0.0 for r in RX] + [min(len(text.split()), 60) / 60]


def main() -> None:
    sample = json.loads((DIRECT_DIR / "rhetoric-sample.json").read_text())
    labels = {int(i): MERGE[l] for i, l in csv.reader(open(LABELS_CSV))}
    idxs = sorted(labels)
    train_texts = [sample[i]["text"] for i in idxs]
    y = [labels[i] for i in idxs]

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    E = model.encode(train_texts, batch_size=128, normalize_embeddings=True,
                     show_progress_bar=False)
    F = np.array([feats(t) for t in train_texts])
    X = np.hstack([E, F * 0.5])

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import cross_val_predict

    clf = LogisticRegression(max_iter=3000, C=8.0)
    pred = cross_val_predict(clf, X, y, cv=5)
    validation = {
        "n_train": len(y),
        "cv_accuracy": round(accuracy_score(y, pred), 3),
        "cv_macro_f1": round(f1_score(y, pred, average="macro"), 3),
        "per_class_f1": {
            c: round(f1_score([1 if t == c else 0 for t in y],
                              [1 if p == c else 0 for p in pred]), 3)
            for c in FORMS
        },
        "second_annotator_agreement": "42/100 merged (Haiku), kappa 0.29 — excluded from training",
    }
    clf.fit(X, y)

    # full-corpus inference from cached embeddings
    emb = np.lib.format.open_memmap(DIRECT_DIR / "reasoning-embeddings.npy", mode="r")
    conn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    total = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    assert emb.shape[0] == total

    by_std: dict[str, Counter] = defaultdict(Counter)
    by_val: dict[str, Counter] = defaultdict(Counter)
    by_year: dict[str, Counter] = defaultdict(Counter)
    overall = Counter()
    CH = 40000
    for start in range(0, total, CH):
        rows = conn.execute(
            "SELECT u.unit_pk, u.reasoning, u.valence, u.year, l.reasoning_key"
            " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
            " WHERE u.unit_pk > ? ORDER BY u.unit_pk LIMIT ?",
            (start, CH),
        ).fetchall()
        Ei = np.asarray(emb[rows[0][0] - 1 : rows[-1][0]], dtype=np.float32)
        Fi = np.array([feats(r[1]) for r in rows])
        preds = clf.predict(np.hstack([Ei, Fi * 0.5]))
        for (pk, _t, valence, year, std), p in zip(rows, preds):
            overall[p] += 1
            by_std[std][p] += 1
            by_val[p][valence] += 1
            by_year[p][year] += 1
        print(f"classified {rows[-1][0]}/{total}", flush=True)
    conn.close()

    payload = {
        "forms": FORMS,
        "validation": validation,
        "overall": dict(overall),
        "by_standard": {k: dict(v) for k, v in by_std.items()},
        "by_valence": {k: dict(v) for k, v in by_val.items()},
        "by_year": {k: {str(y_): v.get(y_, 0) for y_ in range(2018, 2027)} for k, v in by_year.items()},
    }
    out = DIRECT_DIR / "rhetoric-v2.json"
    out.write_text(json.dumps(payload) + "\n")
    print(f"{out} written; validation={validation}")


if __name__ == "__main__":
    main()
