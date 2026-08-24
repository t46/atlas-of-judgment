"""The counterfactual conference: redraw every panel, count the flips.

Model, deliberately simple and stated in full:
  - One-way variance decomposition per year (the ICC machinery of Plate
    XIII): rating = paper value + reviewer draw,
    sigma_b^2 between papers, sigma_w^2 within.
  - For each decided paper with k ratings and observed mean m, the paper
    value theta is shrunk: theta ~ N(mu + rho_k (m - mu), rho_k sigma_w^2 / k)
    with rho_k = sigma_b^2 / (sigma_b^2 + sigma_w^2 / k).
  - A fresh panel of the same size then reads it: m' = theta + N(0, sigma_w^2/k).
  - The decision layer is the empirical acceptance curve P(accept | mean)
    (0.25-wide bins, observed), applied to m' — so the redraw includes
    both a fresh panel and a fresh margin call.
  - Flip probability per paper = P(new decision != actual decision),
    Monte Carlo over 4,000 redraws.

Outputs per year (2026 primary, 2025 robustness): overall expected flip
share, flip share among accepts / rejects, flip prob vs mean curve, and
the per-accepted-paper flip probabilities that drive the interactive
redraw of the program.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/counterfactual-data.json.
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
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

rng = np.random.default_rng(46)
DRAWS = 4000


def parse_rating(cj):
    try:
        v = json.loads(cj).get("rating")
    except json.JSONDecodeError:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"\s*(\d+)", v)
        if m:
            return float(m.group(1))
    return None


def year_result(year):
    ac = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    ratings = defaultdict(list)
    for fid, cj in ac.execute(
        "SELECT forum_id, content_json FROM messages WHERE kind='official_review' AND year=?", (year,)
    ):
        r = parse_rating(cj)
        if r is not None:
            ratings[fid].append(r)
    decisions = {fid: dec for fid, dec in ac.execute(
        "SELECT forum_id, decision FROM papers WHERE year=?", (year,)
    )}
    ac.close()

    papers = []
    for fid, rs in ratings.items():
        dec = decisions.get(fid)
        if len(rs) < 2 or not dec:
            continue
        if re.search(r"accept|oral|poster|spotlight", dec, re.I):
            acc = 1
        elif re.search(r"reject", dec, re.I):
            acc = 0
        else:
            continue
        papers.append((fid, np.mean(rs), len(rs), acc))
    if not papers:
        return None
    means = np.array([p[1] for p in papers])
    ks = np.array([p[2] for p in papers])
    accs = np.array([p[3] for p in papers])
    n = len(papers)

    # one-way ANOVA variance components over all rated forums
    allmeans, allvars, allks = [], [], []
    for fid, rs in ratings.items():
        if len(rs) >= 2:
            allmeans.append(np.mean(rs))
            allvars.append(np.var(rs, ddof=1))
            allks.append(len(rs))
    allks = np.array(allks)
    sw2 = float(np.average(allvars, weights=allks - 1))
    mu = float(np.average(allmeans, weights=allks))
    kbar = float(allks.mean())
    sb2 = max(0.05, float(np.var(allmeans, ddof=1)) - sw2 / kbar)

    # empirical acceptance curve in 0.25 bins, clipped range
    bins = np.arange(0, 10.26, 0.25)
    binidx = np.clip(np.digitize(means, bins) - 1, 0, len(bins) - 2)
    curve = np.full(len(bins) - 1, np.nan)
    for b in range(len(bins) - 1):
        sel = binidx == b
        if sel.sum() >= 20:
            curve[b] = accs[sel].mean()
    # fill edges: below first observed bin -> 0-ish, above -> ~1
    obs = np.where(~np.isnan(curve))[0]
    curve[: obs[0]] = curve[obs[0]]
    curve[obs[-1] + 1:] = curve[obs[-1]]
    for b in range(len(curve)):
        if np.isnan(curve[b]):
            lo = obs[obs < b].max()
            hi = obs[obs > b].min()
            w = (b - lo) / (hi - lo)
            curve[b] = curve[lo] * (1 - w) + curve[hi] * w

    def pacc(m):
        bi = np.clip(((m - 0) / 0.25).astype(int), 0, len(curve) - 1)
        return curve[bi]

    rho = sb2 / (sb2 + sw2 / ks)
    post_mean = mu + rho * (means - mu)
    post_sd = np.sqrt(rho * sw2 / ks)
    draw_sd = np.sqrt(sw2 / ks)

    # Monte Carlo in chunks
    flip = np.zeros(n)
    CH = 500
    for c in range(0, DRAWS, CH):
        theta = post_mean[:, None] + rng.standard_normal((n, CH)) * post_sd[:, None]
        m2 = theta + rng.standard_normal((n, CH)) * draw_sd[:, None]
        p2 = pacc(m2)
        newacc = rng.random((n, CH)) < p2
        flip += (newacc != accs[:, None].astype(bool)).sum(1)
    flip /= DRAWS

    # flip curve by observed mean
    fc_bins = np.arange(1.5, 9.01, 0.5)
    fc = []
    for b in range(len(fc_bins) - 1):
        sel = (means >= fc_bins[b]) & (means < fc_bins[b + 1])
        if sel.sum() >= 15:
            fc.append({"m": round(float((fc_bins[b] + fc_bins[b + 1]) / 2), 2),
                       "flip": round(float(flip[sel].mean()), 4),
                       "n": int(sel.sum())})

    res = {
        "n": n,
        "n_acc": int(accs.sum()),
        "flip_all": round(float(flip.mean()), 4),
        "flip_acc": round(float(flip[accs == 1].mean()), 4),
        "flip_rej": round(float(flip[accs == 0].mean()), 4),
        "secure_acc": round(float((flip[accs == 1] < 0.1).mean()), 4),
        "sb2": round(sb2, 3), "sw2": round(sw2, 3), "mu": round(mu, 3),
        "curve": fc,
    }
    if year == 2026:
        order = np.argsort(-means[accs == 1])
        res["acc_flip_probs"] = [round(float(x), 3) for x in flip[accs == 1][order]]
        res["acc_means"] = [round(float(x), 2) for x in means[accs == 1][order]]
    return res


def main() -> None:
    out = {}
    for y in (2026, 2025):
        r = year_result(y)
        out[str(y)] = r
        print(f"{y}: n={r['n']:,} acc={r['n_acc']:,}  flip overall {r['flip_all']:.1%}"
              f" · among accepts {r['flip_acc']:.1%} · among rejects {r['flip_rej']:.1%}"
              f" · secure accepts (<10% flip) {r['secure_acc']:.1%}"
              f"  [sb2={r['sb2']}, sw2={r['sw2']}]")
    (V / "counterfactual-data.json").write_text(json.dumps(out))
    print("curve 2026:", out["2026"]["curve"])


if __name__ == "__main__":
    main()
