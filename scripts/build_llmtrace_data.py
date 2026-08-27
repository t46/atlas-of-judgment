#!/usr/bin/env python3
"""LLM-era trend measurements over raw ICLR review text 2018-2026.

Pre-specified in notes/llm-era-analysis-plan.md (frozen before computation).
Corpus-level only: no review is individually labeled as AI-written.

Outputs data/analysis/iclr/unit-taxonomy-2026-v1/llmtrace-data.json
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DB = ROOT / "data" / "processed" / "iclr" / "analysis.sqlite3"
DIRECT_DB = ROOT / "data" / "analysis" / "iclr" / "unit-taxonomy-direct-v1" / "units.sqlite3"
OUT = ROOT / "data" / "analysis" / "iclr" / "unit-taxonomy-2026-v1" / "llmtrace-data.json"

YEARS = list(range(2018, 2027))
RNG = np.random.default_rng(20260827)

# Free-text fields kept per form era (everything else — enums, scores,
# checkboxes — is dropped so form changes cannot masquerade as vocabulary).
KEEP_FIELDS = {
    2018: {"title", "review"},
    2019: {"title", "review"},
    2020: {"title", "review"},
    2021: {"title", "review"},
    2022: {"summary_of_the_paper", "main_review", "summary_of_the_review"},
    2023: {
        "summary_of_the_paper",
        "strength_and_weaknesses",
        "clarity,_quality,_novelty_and_reproducibility",
        "summary_of_the_review",
    },
    2024: {"summary", "strengths", "weaknesses", "questions"},
    2025: {"summary", "strengths", "weaknesses", "questions"},
    2026: {"summary", "strengths", "weaknesses", "questions"},
}

HEADER = re.compile(r"^\[([a-z0-9_,:. ]+)\]$")
TOKEN = re.compile(r"[a-z]{2,}")

# Frozen marker set M — 13 forms, all from Liang et al. (ICML 2024) or
# Kobak et al. (Science Advances 2025) verified lists; style words only.
MARKERS = [
    "commendable", "meticulous", "meticulously", "intricate", "pivotal",
    "versatile", "delve", "delves", "delving", "underscores", "underscoring",
    "showcases", "showcasing",
]
# Frozen negative-control set C — reviewer-register style words with no
# documented LLM preference.
CONTROLS = [
    "interesting", "unclear", "convincing", "marginal", "concerns",
    "thorough", "sound", "novel", "weak", "solid",
]
MARKER_SET = frozenset(MARKERS)
CONTROL_SET = frozenset(CONTROLS)

# Content words excluded from the exploratory discovery ranking (topic drift,
# not style): matched as exact tokens.
CONTENT_STOP = frozenset(
    "llm llms gpt chatgpt gpt4 transformer transformers diffusion prompt "
    "prompts prompting hallucination hallucinations foundation multimodal "
    "finetuning pretraining pretrained rlhf sota bert vit token tokens "
    "embedding embeddings attention generative agent agents agentic "
    "instruction sam clip lora nerf mamba moe kv rag cot sft dpo vlm vlms "
    "reasoning ood contrastive ssl federated".split()
)


def free_text(content_text: str, year: int) -> str:
    keep = KEEP_FIELDS[year]
    out: list[str] = []
    keeping = False
    for line in content_text.splitlines():
        m = HEADER.match(line.strip())
        if m:
            keeping = m.group(1) in keep
            continue
        if keeping:
            out.append(line)
    return "\n".join(out)


def load_reviews(year: int) -> list[dict]:
    con = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        "SELECT note_id, forum_id, signature, cdate, content_json, content_text "
        "FROM messages WHERE year=? AND kind='official_review'",
        (year,),
    ).fetchall()
    con.close()
    out = []
    for note_id, forum_id, signature, cdate, cj, ct in rows:
        content = json.loads(cj)
        txt = free_text(ct or "", year)
        out.append(
            {
                "note_id": note_id,
                "forum_id": forum_id,
                "signature": signature or "",
                "cdate": cdate,
                "content": content,
                "text": txt,
            }
        )
    return out


def parse_leading_int(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"\s*(\d+)", v)
        if m:
            return float(m.group(1))
    return None


def ols_extrapolate(xs: list[int], ys: list[float], x: int) -> float:
    slope, intercept = np.polyfit(np.array(xs, float), np.array(ys, float), 1)
    return float(np.clip(slope * x + intercept, 0.0, 1.0))


def median_q(vals: np.ndarray) -> dict:
    if len(vals) == 0:
        return {"median": None, "q25": None, "q75": None, "n": 0}
    return {
        "median": round(float(np.median(vals)), 4),
        "q25": round(float(np.percentile(vals, 25)), 4),
        "q75": round(float(np.percentile(vals, 75)), 4),
        "n": int(len(vals)),
    }


def main() -> None:
    # ---------- pass 1: vocabulary + per-review facts, all years ----------
    df_by_year: dict[int, Counter] = {}
    n_reviews: dict[int, int] = {}
    marker_hit: dict[int, int] = {}
    control_hit: dict[int, int] = {}
    per_word_df: dict[str, dict[int, float]] = {w: {} for w in MARKERS + CONTROLS}

    # kept per year for later passes
    year_cache: dict[int, list[dict]] = {}

    for year in YEARS:
        revs = load_reviews(year)
        year_cache[year] = revs
        df = Counter()
        mh = ch = 0
        for r in revs:
            toks = set(TOKEN.findall(r["text"].lower()))
            r["tokset_markers"] = toks & MARKER_SET
            df.update(toks)
            if toks & MARKER_SET:
                mh += 1
            if toks & CONTROL_SET:
                ch += 1
        n = len(revs)
        df_by_year[year] = df
        n_reviews[year] = n
        marker_hit[year] = mh
        control_hit[year] = ch
        for w in MARKERS + CONTROLS:
            per_word_df[w][year] = round(df.get(w, 0) / n, 6)
        print(f"[vocab] {year}: n={n} marker_share={mh/n:.4f} control_share={ch/n:.4f}")

    base_years = [2018, 2019, 2020, 2021, 2022]
    ind = {y: marker_hit[y] / n_reviews[y] for y in YEARS}
    ind_c = {y: control_hit[y] / n_reviews[y] for y in YEARS}
    cf = {
        y: ols_extrapolate(base_years, [ind[b] for b in base_years], y)
        for y in [2023, 2024, 2025, 2026]
    }
    cf_c = {
        y: ols_extrapolate(base_years, [ind_c[b] for b in base_years], y)
        for y in [2023, 2024, 2025, 2026]
    }
    per_word_delta = {}
    for w in MARKERS:
        series = [per_word_df[w][b] for b in base_years]
        pw = {}
        for y in [2023, 2024, 2025, 2026]:
            q = ols_extrapolate(base_years, series, y)
            f = per_word_df[w][y]
            pw[str(y)] = {
                "f": round(f, 6),
                "q": round(q, 6),
                "delta": round(f - q, 6),
                "ratio": round(f / max(q, 1e-5), 2),
            }
        per_word_delta[w] = pw

    # exploratory discovery: excess ratio in 2026 vs base-mean, style focus
    discovery = []
    n26 = n_reviews[2026]
    for w, c26 in df_by_year[2026].items():
        f26 = c26 / n26
        if f26 < 1e-3 or w in CONTENT_STOP:
            continue
        base = [df_by_year[b].get(w, 0) / n_reviews[b] for b in base_years]
        if np.mean(base) >= 0.02:
            continue
        q = ols_extrapolate(base_years, base, 2026)
        r = f26 / max(q, float(np.mean(base)), 1e-4)
        if r >= 3.0:
            discovery.append(
                {
                    "word": w,
                    "f2026": round(f26, 5),
                    "base_mean": round(float(np.mean(base)), 5),
                    "q2026": round(q, 5),
                    "ratio": round(r, 1),
                }
            )
    discovery.sort(key=lambda d: -d["ratio"])
    discovery = discovery[:80]

    # ---------- pass 2: lexical convergence per year ----------
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    within_paper = {}
    cross_paper = {}
    for year in YEARS:
        revs = year_cache[year]
        texts = [r["text"] for r in revs]
        vec = TfidfVectorizer(
            lowercase=True, sublinear_tf=True, min_df=5, max_features=100_000,
            token_pattern=r"[a-z]{2,}",
        )
        X = normalize(vec.fit_transform(texts))
        by_forum: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(revs):
            by_forum[r["forum_id"]].append(i)
        sims = []
        for idxs in by_forum.values():
            if len(idxs) < 2:
                continue
            sub = X[idxs]
            g = (sub @ sub.T).toarray()
            iu = np.triu_indices(len(idxs), k=1)
            sims.append(float(g[iu].mean()))
        within_paper[year] = median_q(np.array(sims))

        n = len(revs)
        forums = np.array([r["forum_id"] for r in revs])
        a = RNG.integers(0, n, 80_000)
        b = RNG.integers(0, n, 80_000)
        mask = (a != b) & (forums[a] != forums[b])
        a, b = a[mask][:50_000], b[mask][:50_000]
        cs = np.asarray(X[a].multiply(X[b]).sum(axis=1)).ravel()
        cross_paper[year] = median_q(cs)
        print(f"[conv] {year}: within={within_paper[year]['median']} cross={cross_paper[year]['median']}")

    # Robustness: constant-form window 2024-2026 with ONE shared vocabulary,
    # so per-year vectorizer differences cannot manufacture the trend.
    fixed_vocab = {}
    all_texts = [r["text"] for y in (2024, 2025, 2026) for r in year_cache[y]]
    vec = TfidfVectorizer(
        lowercase=True, sublinear_tf=True, min_df=5, max_features=100_000,
        token_pattern=r"[a-z]{2,}",
    )
    vec.fit(all_texts)
    del all_texts
    for year in (2024, 2025, 2026):
        revs = year_cache[year]
        X = normalize(vec.transform([r["text"] for r in revs]))
        by_forum = defaultdict(list)
        for i, r in enumerate(revs):
            by_forum[r["forum_id"]].append(i)
        sims = []
        for idxs in by_forum.values():
            if len(idxs) < 2:
                continue
            sub = X[idxs]
            g = (sub @ sub.T).toarray()
            iu = np.triu_indices(len(idxs), k=1)
            sims.append(float(g[iu].mean()))
        n = len(revs)
        forums = np.array([r["forum_id"] for r in revs])
        a = RNG.integers(0, n, 80_000)
        b = RNG.integers(0, n, 80_000)
        mask = (a != b) & (forums[a] != forums[b])
        a, b = a[mask][:50_000], b[mask][:50_000]
        cs = np.asarray(X[a].multiply(X[b]).sum(axis=1)).ravel()
        fixed_vocab[year] = {
            "within": median_q(np.array(sims)),
            "cross": median_q(cs),
        }
        print(f"[conv-fixed] {year}: within={fixed_vocab[year]['within']['median']} cross={fixed_vocab[year]['cross']['median']}")

    # ---------- pass 3: content convergence (12-object overlap) ----------
    con = sqlite3.connect(f"file:{DIRECT_DB}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        "SELECT u.year, u.forum_id, u.reviewer_key, l.object_key "
        "FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk "
        "WHERE u.reviewer_role = 'official_reviewer'"
    ).fetchall()
    con.close()
    objsets: dict[int, dict[str, dict[str, set]]] = defaultdict(lambda: defaultdict(dict))
    for y, forum, rk, obj in rows:
        d = objsets[y][forum]
        d.setdefault(rk, set()).add(obj)
    within_jac = {}
    cross_jac = {}
    for year in YEARS:
        js = []
        pool = []  # (frozen sets) for cross-forum sampling
        for forum, revd in objsets[year].items():
            sets = [s for s in revd.values() if s]
            pool.extend(sets)
            if len(sets) < 2:
                continue
            pj = []
            for i in range(len(sets)):
                for j in range(i + 1, len(sets)):
                    inter = len(sets[i] & sets[j])
                    union = len(sets[i] | sets[j])
                    pj.append(inter / union)
            js.append(float(np.mean(pj)))
        within_jac[year] = median_q(np.array(js))
        m = len(pool)
        a = RNG.integers(0, m, 50_000)
        b = RNG.integers(0, m, 50_000)
        cj = np.array(
            [
                len(pool[i] & pool[j]) / len(pool[i] | pool[j])
                for i, j in zip(a, b)
                if i != j
            ]
        )
        cross_jac[year] = median_q(cj)
        print(f"[jac] {year}: within={within_jac[year]['median']} cross={cross_jac[year]['median']}")

    # ---------- pass 4: correlate battery ----------
    from scipy.stats import binomtest

    def reply_counts(year: int) -> dict[tuple[str, str], int]:
        con = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro&immutable=1", uri=True)
        rows = con.execute(
            "SELECT forum_id, signature FROM messages "
            "WHERE year=? AND kind='official_comment'",
            (year,),
        ).fetchall()
        con.close()
        out: dict[tuple[str, str], int] = Counter()
        for forum, sig in rows:
            tail = (sig or "").rsplit("/", 1)[-1]
            out[(forum, tail)] += 1
        return out

    def paired_rating(year: int) -> dict:
        revs = year_cache[year]
        by_forum: dict[str, list[tuple[bool, float]]] = defaultdict(list)
        for r in revs:
            rating = parse_leading_int(
                r["content"].get("rating", r["content"].get("recommendation"))
            )
            if rating is None:
                continue
            by_forum[r["forum_id"]].append((bool(r["tokset_markers"]), rating))
        diffs = []
        # Sharma et al. (arXiv:2601.20920) find LLM-aided leniency concentrated
        # on low-rated papers; stratify by the unmarked co-reviews' own mean.
        strata: dict[str, list[float]] = {"weak": [], "mid": [], "strong": []}
        for pairs in by_forum.values():
            marked = [v for m, v in pairs if m]
            clean = [v for m, v in pairs if not m]
            if marked and clean:
                d = float(np.mean(marked) - np.mean(clean))
                diffs.append(d)
                base = float(np.mean(clean))
                key = "weak" if base < 4 else ("mid" if base <= 6 else "strong")
                strata[key].append(d)
        diffs = np.array(diffs)
        # Regression-to-the-mean control: stratifying by the unmarked side's
        # mean manufactures a weak+/strong- gradient even under random
        # labeling. Permute the marked labels within each paper (200x) and
        # recompute the stratified means; only the observed-minus-permuted
        # excess is interpretable.
        eligible = [p for p in by_forum.values() if 0 < sum(m for m, _ in p) < len(p)]
        perm_acc = {"weak": [], "mid": [], "strong": []}
        for _ in range(200):
            ps = {"weak": [], "mid": [], "strong": []}
            for pairs in eligible:
                k = sum(m for m, _ in pairs)
                vals = [v for _, v in pairs]
                idx = RNG.permutation(len(vals))
                mv = [vals[i] for i in idx[:k]]
                cv = [vals[i] for i in idx[k:]]
                d = float(np.mean(mv) - np.mean(cv))
                base = float(np.mean(cv))
                key = "weak" if base < 4 else ("mid" if base <= 6 else "strong")
                ps[key].append(d)
            for k2, v in ps.items():
                if v:
                    perm_acc[k2].append(float(np.mean(v)))
        by_quality = {
            k: {
                "n": len(v),
                "mean_diff": round(float(np.mean(v)), 4) if v else None,
                "permuted_mean_diff": round(float(np.mean(perm_acc[k])), 4)
                if perm_acc[k] else None,
            }
            for k, v in strata.items()
        }
        pos = int((diffs > 0).sum())
        neg = int((diffs < 0).sum())
        p = binomtest(pos, pos + neg, 0.5).pvalue if pos + neg > 0 else 1.0
        boot = np.array(
            [
                float(np.mean(diffs[RNG.integers(0, len(diffs), len(diffs))]))
                for _ in range(2000)
            ]
        )
        return {
            "n_papers": int(len(diffs)),
            "mean_diff": round(float(diffs.mean()), 4),
            "ci95": [round(float(np.percentile(boot, 2.5)), 4),
                     round(float(np.percentile(boot, 97.5)), 4)],
            "share_marked_higher": round(pos / max(pos + neg, 1), 4),
            "sign_test_p": float(f"{p:.2e}"),
            "by_quality": by_quality,
        }

    def paired_longest(year: int) -> dict:
        """Benchmark: does the LONGEST review of a paper score higher anyway?"""
        revs = year_cache[year]
        by_forum: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for r in revs:
            rating = parse_leading_int(
                r["content"].get("rating", r["content"].get("recommendation"))
            )
            if rating is None:
                continue
            by_forum[r["forum_id"]].append((len(r["text"].split()), rating))
        diffs = []
        for pairs in by_forum.values():
            if len(pairs) < 2:
                continue
            pairs.sort(key=lambda t: -t[0])
            rest = [v for _, v in pairs[1:]]
            diffs.append(float(pairs[0][1] - np.mean(rest)))
        diffs = np.array(diffs)
        return {
            "n_papers": int(len(diffs)),
            "mean_diff": round(float(diffs.mean()), 4),
        }

    def battery_for(year: int) -> dict:
        revs = year_cache[year]
        rc = reply_counts(year)
        cds = np.array([r["cdate"] or 0 for r in revs], float)
        pctile = cds.argsort().argsort() / max(len(revs) - 1, 1)
        marked = np.array([bool(r["tokset_markers"]) for r in revs])
        words = np.array([len(r["text"].split()) for r in revs], float)
        cols = {
            "confidence": np.array(
                [parse_leading_int(r["content"].get("confidence")) or np.nan
                 for r in revs], float),
            "etal": np.array(
                ["et al" in r["text"].lower() for r in revs], float),
            "replies": np.array(
                [rc.get((r["forum_id"], r["signature"].rsplit("/", 1)[-1]), 0)
                 for r in revs], float),
            "late": np.asarray(pctile, float),
            "words": words,
        }
        # word-count deciles for length stratification
        dec = np.searchsorted(np.percentile(words, np.arange(10, 100, 10)), words)

        def group_stat(vals: np.ndarray) -> dict:
            ok = ~np.isnan(vals)
            m, u = vals[ok & marked], vals[ok & ~marked]
            boots = []
            for _ in range(1000):
                bm = m[RNG.integers(0, len(m), len(m))]
                bu = u[RNG.integers(0, len(u), len(u))]
                boots.append(float(np.mean(bm) - np.mean(bu)))
            boots = np.array(boots)
            # length-stratified diff: weighted by n_marked per decile
            sd_num = sd_den = 0.0
            for d in range(10):
                sel = ok & (dec == d)
                mm, uu = vals[sel & marked], vals[sel & ~marked]
                if len(mm) >= 30 and len(uu) >= 30:
                    sd_num += (np.mean(mm) - np.mean(uu)) * len(mm)
                    sd_den += len(mm)
            return {
                "marked": round(float(np.mean(m)), 4),
                "unmarked": round(float(np.mean(u)), 4),
                "diff": round(float(np.mean(m) - np.mean(u)), 4),
                "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                         round(float(np.percentile(boots, 97.5)), 4)],
                "diff_length_stratified": round(sd_num / sd_den, 4) if sd_den else None,
            }

        return {
            "n": len(revs),
            "n_marked": int(marked.sum()),
            "share_marked": round(float(marked.mean()), 4),
            "confidence": group_stat(cols["confidence"]),
            "et_al_share": group_stat(cols["etal"]),
            "rebuttal_replies": group_stat(cols["replies"]),
            "word_count": group_stat(cols["words"]),
            "cdate_percentile": group_stat(cols["late"]),
            "paired_rating": paired_rating(year),
            "paired_longest_benchmark": paired_longest(year),
        }

    battery = {str(y): battery_for(y) for y in (2024, 2025, 2026)}
    battery["marked_share_by_year"] = {str(y): round(ind[y], 4) for y in YEARS}

    out = {
        "meta": {
            "generated": "2026-08-27",
            "plan": "notes/llm-era-analysis-plan.md (frozen before computation)",
            "n_reviews": {str(y): n_reviews[y] for y in YEARS},
            "kept_fields": {str(y): sorted(KEEP_FIELDS[y]) for y in YEARS},
            "note": "Corpus-level measurements only; no review is individually "
                    "labeled as AI-written. Marker set frozen from Liang et al. "
                    "ICML 2024 and Kobak et al. Science Advances 2025.",
        },
        "vocab": {
            "marker_set": MARKERS,
            "control_set": CONTROLS,
            "base_years": base_years,
            "indicator": {str(y): round(ind[y], 5) for y in YEARS},
            "counterfactual": {str(y): round(cf[y], 5) for y in cf},
            "delta": {str(y): round(ind[y] - cf[y], 5) for y in cf},
            "control_indicator": {str(y): round(ind_c[y], 5) for y in YEARS},
            "control_delta": {str(y): round(ind_c[y] - cf_c[y], 5) for y in cf_c},
            "per_word_df": {
                w: {str(y): per_word_df[w][y] for y in YEARS} for w in MARKERS
            },
            "per_word_delta": per_word_delta,
            "discovery_top": discovery,
        },
        "convergence": {
            "within_paper": {str(y): within_paper[y] for y in YEARS},
            "cross_paper": {str(y): cross_paper[y] for y in YEARS},
            "fixed_vocab_2024_2026": {str(y): fixed_vocab[y] for y in fixed_vocab},
            "form_eras": {"2018": "A", "2019": "A", "2020": "B", "2021": "A",
                          "2022": "C", "2023": "D", "2024": "E", "2025": "E",
                          "2026": "E"},
        },
        "content_convergence": {
            "within_forum_jaccard": {str(y): within_jac[y] for y in YEARS},
            "cross_forum_jaccard": {str(y): cross_jac[y] for y in YEARS},
        },
        "correlates": battery,
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
