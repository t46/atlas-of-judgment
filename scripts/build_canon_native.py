"""The canon, re-measured on the RAW review text (declared follow-up).

Motivation (2026-08-21 review): the original canon measurement ran its
citation regex on the distilled unit text (observation+reasoning), so
distillation was a possible confound. Here the units are used ONLY to
locate the charge — which forum, which reviewer wrote a negative
novelty objection — and the measurement itself runs on the reviewer's
original words: the unit is matched to its source official_review by
token overlap, the charge neighbourhood is every sentence mentioning
novelty (novel/original/increment) plus two sentences either side, and
the citation / vague-wave regexes are applied to that window.

Design committed before results, reported whichever way they come out.
The +/-2-sentence window is deliberately GENEROUS to naming (it can
absorb citations that belong to a neighbouring criticism), so it biases
AGAINST the "structurally unnamed" claim.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/canon-native.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
A = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"

CIT = re.compile(
    r"\b([A-Z][A-Za-zÀ-ɏ'-]+)\s+(?:et\s+al\.?,?\s*|and\s+[A-Z][A-Za-z'-]+,?\s+|&\s*[A-Z][A-Za-z'-]+,?\s+)?\(?\s*(20[0-2][0-9]|19[89][0-9])\s*[a-c]?\)?",
)
ARXIV = re.compile(r"\b(\d{4}\.\d{4,5})\b")
VAGUE = re.compile(r"prior (work|art|literature|methods|approaches)|existing (work|methods|literature|approaches)|previous (work|studies|methods)", re.I)
NOV = re.compile(r"novel|original(?:ity)?\b|increment", re.I)
STOP = {"in", "the", "figure", "table", "section", "eq", "equation", "appendix", "since", "from", "until", "before", "after",
        "iclr", "neurips", "icml", "cvpr", "iccv", "eccv", "emnlp", "acl", "naacl", "aaai", "ijcai", "kdd", "sigir", "www",
        "tpami", "jmlr", "aistats", "uai", "colt", "interspeech", "miccai"}
TOK = re.compile(r"[a-z]{4,}")


def names_in(text: str):
    keys = {f"{m.group(1)} {m.group(2)}" for m in CIT.finditer(text) if m.group(1).lower() not in STOP}
    keys |= {f"arXiv:{x}" for x in ARXIV.findall(text)}
    return keys


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    units = list(dc.execute(
        "SELECT u.unit_pk, u.year, u.forum_id, u.observation, u.reasoning"
        " FROM units u JOIN unit_labels l ON l.unit_pk=u.unit_pk"
        " WHERE l.object_key='novelty' AND u.valence='negative'"
        " AND u.reviewer_role='official_reviewer' AND u.temporal_position='initial_review'"))
    dc.close()

    am = sqlite3.connect(f"file:{A}?mode=ro", uri=True)
    # preload reviews per forum lazily with a small cache keyed by forum
    by_forum = defaultdict(list)
    forum_year = {}
    for pk, yr, fid, obs, rea in units:
        by_forum[fid].append((pk, yr, obs or "", rea or ""))
        forum_year[fid] = yr

    n_matched = n_window = named_units = vague_only = 0
    by_year = defaultdict(lambda: [0, 0])       # year -> [named, with-window]
    per_forum_names = defaultdict(set)

    for i, (fid, us) in enumerate(by_forum.items()):
        rows = [(s, t or "") for s, t in am.execute(
            "SELECT signature, content_text FROM messages WHERE year=? AND forum_id=? AND kind='official_review'",
            (forum_year[fid], fid))]
        if not rows:
            continue
        # pre-tokenize + pre-split each review once per forum
        prep = []
        for _s, t in rows:
            sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", t) if x.strip()]
            prep.append((set(TOK.findall(t.lower())), sents))
        for pk, yr, obs, rea in us:
            ut = set(TOK.findall((obs + " " + rea).lower()))
            toks, sents = max(prep, key=lambda p: len(ut & p[0]))
            n_matched += 1
            window = []
            for j, s in enumerate(sents):
                if NOV.search(s) and len(s) > 25:
                    window.extend(sents[max(0, j - 2):j + 3])
            if not window:
                continue
            n_window += 1
            wtext = " ".join(window)
            keys = names_in(wtext)
            by_year[yr][1] += 1
            if keys:
                named_units += 1
                by_year[yr][0] += 1
                for k in keys:
                    per_forum_names[k].add(fid)
            elif VAGUE.search(wtext):
                vague_only += 1
        if (i + 1) % 2000 == 0:
            print(f"  {i + 1:,}/{len(by_forum):,} forums", flush=True)
    am.close()

    counts = sorted((len(fs) for fs in per_forum_names.values()), reverse=True)
    payload = {
        "n_units": len(units),
        "n_matched": n_matched,
        "n_with_window": n_window,
        "named_share": round(named_units / max(1, n_window), 4),
        "vague_only_share": round(vague_only / max(1, n_window), 4),
        "named_share_by_year": {str(y): round(a / max(1, b), 4) for y, (a, b) in sorted(by_year.items())},
        "n_distinct_named": len(per_forum_names),
        "max_forums_per_key": counts[0] if counts else 0,
        "top10_forums_total": sum(counts[:10]),
    }
    (V / "canon-native.json").write_text(json.dumps(payload))
    # promote to the primary measurement in canon-data.json, preserving the
    # distilled-unit reading under "distilled" (two instruments, both reported)
    cd = json.loads((V / "canon-data.json").read_text())
    dist = cd.get("distilled") or {k: cd[k] for k in (
        "named_share", "vague_only_share", "named_share_by_year",
        "n_distinct_named", "max_forums_per_key", "top10_forums_total")}
    (V / "canon-data.json").write_text(json.dumps({
        "n_units": cd["n_units"],
        "n_with_window": payload["n_with_window"],
        "named_share": payload["named_share"],
        "vague_only_share": payload["vague_only_share"],
        "named_share_by_year": payload["named_share_by_year"],
        "n_distinct_named": payload["n_distinct_named"],
        "max_forums_per_key": payload["max_forums_per_key"],
        "top10_forums_total": payload["top10_forums_total"],
        "distilled": dist,
    }))
    print(f"units {len(units):,} · matched {n_matched:,} · with novelty window {n_window:,}")
    print(f"named {payload['named_share']:.1%} · vague-only {payload['vague_only_share']:.1%} · distinct {len(per_forum_names):,}")
    print("max forums per key:", payload["max_forums_per_key"], "· top10:", payload["top10_forums_total"])
    print("by year:", payload["named_share_by_year"])


if __name__ == "__main__":
    main()
