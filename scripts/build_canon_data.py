"""The canon of the prior: which works are named to kill novelty.

Over all Direct-track negative novelty units (official reviewers,
initial reviews), extract explicit prior-work references from the
observation+reasoning text: "(Author et al., 2021)" citation patterns
and arXiv IDs. Two outputs: (a) the NAMING RATE — what share of
novelty objections point at a specific named precedent at all, versus
the unnamed wave at "prior work" (per year); (b) the canon — the most
frequently named (surname, year) keys, counted once per forum.

Caveat owned in the caption: (surname, year) conflates same-surname
same-year papers, and mentions inside quoted author text can leak in.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/canon-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

CIT = re.compile(
    r"\b([A-Z][A-Za-zÀ-ɏ'-]+)\s+(?:et\s+al\.?,?\s*|and\s+[A-Z][A-Za-z'-]+,?\s+|&\s*[A-Z][A-Za-z'-]+,?\s+)?\(?\s*(20[0-2][0-9]|19[89][0-9])\s*[a-c]?\)?",
    )
ARXIV = re.compile(r"\b(\d{4}\.\d{4,5})\b")
VAGUE = re.compile(r"prior (work|art|literature|methods|approaches)|existing (work|methods|literature|approaches)|previous (work|studies|methods)", re.I)


def main() -> None:
    dc = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    per_forum_names = defaultdict(set)
    named_units = 0
    vague_only_units = 0
    n_units = 0
    by_year = defaultdict(lambda: [0, 0])   # year -> [named, total]
    examples = {}
    for yr, fid, obs, rea in dc.execute(
        "SELECT u.year, u.forum_id, u.observation, u.reasoning"
        " FROM units u JOIN unit_labels l ON l.unit_pk=u.unit_pk"
        " WHERE l.object_key='novelty' AND u.valence='negative'"
        " AND u.reviewer_role='official_reviewer' AND u.temporal_position='initial_review'"
    ):
        n_units += 1
        text = f"{obs or ''} {rea or ''}"
        names = {(m.group(1), m.group(2)) for m in CIT.finditer(text)
                 if m.group(1).lower() not in ("in", "the", "figure", "table", "section", "eq", "equation", "appendix", "since", "from", "until", "before", "after",
                                              "iclr", "neurips", "icml", "cvpr", "iccv", "eccv", "emnlp", "acl", "naacl", "aaai", "ijcai", "kdd", "sigir", "www", "tpami", "jmlr", "aistats", "uai", "colt", "interspeech", "miccai")}
        arx = set(ARXIV.findall(text))
        key_names = {f"{a} {y}" for a, y in names} | {f"arXiv:{x}" for x in arx}
        by_year[yr][1] += 1
        if key_names:
            named_units += 1
            by_year[yr][0] += 1
            for k in key_names:
                per_forum_names[k].add(fid)
                if k not in examples and len(text) > 60:
                    examples[k] = text[:200]
        elif VAGUE.search(text):
            vague_only_units += 1
    dc.close()

    counts = sorted((len(fs) for fs in per_forum_names.values()), reverse=True)
    payload = {
        "n_units": n_units,
        "named_share": round(named_units / n_units, 4),
        "vague_only_share": round(vague_only_units / n_units, 4),
        "named_share_by_year": {str(y): round(a / max(1, b), 4)
                                for y, (a, b) in sorted(by_year.items())},
        "n_distinct_named": len(per_forum_names),
        # concentration of the would-be canon, under a deliberately GENEROUS
        # key (surname+year, which merges distinct papers): even so, the
        # most-cited key reaches only max_forums forums.
        "max_forums_per_key": counts[0] if counts else 0,
        "top10_forums_total": sum(counts[:10]),
    }
    out = V / "canon-data.json"
    if out.exists() and "distilled" in json.loads(out.read_text()) and "--force" not in sys.argv:
        raise SystemExit(
            "REFUSING to overwrite canon-data.json: it is in its PROMOTED state\n"
            "(native reading primary, distilled reading preserved under 'distilled').\n"
            "Re-running this script alone would regress it to the distilled-only reading.\n"
            "If you mean to rebuild the pipeline, run with --force and then IMMEDIATELY\n"
            "run build_canon_native.py to re-promote."
        )
    out.write_text(json.dumps(payload))
    print(f"units {n_units:,} · named {payload['named_share']:.1%} · vague-only {payload['vague_only_share']:.1%} · distinct {len(per_forum_names):,}")
    print("max forums per key:", payload["max_forums_per_key"], "· top10 total:", payload["top10_forums_total"])
    print("by year:", payload["named_share_by_year"])


if __name__ == "__main__":
    main()
