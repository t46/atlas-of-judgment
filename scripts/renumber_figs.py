"""Renumber every figure sequentially in document order.

Scans atlas_template.html plate by plate, collects each plate's figure
ids (from fig-titles and captions), assigns the plate the next number in
sequence, keeps each id's letter part, and rewrites all references in a
single pass (Fig.-prefixed, Fig-comment, and bare lettered ids).
Idempotent: on an already-sequential document it is a no-op.

Run after inserting or moving any plate:  uv run python scripts/renumber_figs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

T = Path(__file__).resolve().parent / "atlas_template.html"

ID_RE = re.compile(r"Figs?\.?(?:&nbsp;|\s| )+([0-9]{1,2}[a-e]?|S[0-9])\b")
# A figure BELONGS to a plate only where it is defined: its fig-title div or a
# bolded caption opener. Bare mentions elsewhere are references, not definitions
# — collecting those once merged a cross-plate reference into the wrong plate.
DEF_RE = re.compile(
    r'(?:class="fig-title"[^>]*>|<b>)Figs?\.?(?:&nbsp;|\s| )+([0-9]{1,2}[a-e]?|S[0-9])\b'
)


R_VAL = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n: int) -> str:
    out = []
    for v, sym in R_VAL:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def renumber_plates(s: str) -> str:
    """Plates get sequential romans by document order; every 'Plate <roman>'
    token is remapped in one pass; act-leaf ranges are recomputed from the
    actual plates each act now contains."""
    order = re.findall(r'class="plate-no">Plate ([IVXL]+) ·', s)
    assert len(order) == len(set(order)), "duplicate plate numerals"
    pmap = {old: to_roman(i + 1) for i, old in enumerate(order)}
    changes = {k: v for k, v in pmap.items() if k != v}
    if changes:
        print("plate map:", changes)
        alt = "|".join(sorted((re.escape(k) for k in pmap), key=len, reverse=True))
        s = re.sub(r"(Plate(?:&nbsp;|\s| )+)(" + alt + r")\b",
                   lambda m: m.group(1) + pmap[m.group(2)], s)

    for m in re.finditer(r"Plates [IVXL]+[^<]{0,30}", s):
        print("WARN plural plate ref not remapped — fix by hand:", m.group(0))
    for m in re.finditer(r"PLATE(?:&nbsp;|\s| )+[IVXL]+\b[^<]{0,30}", s):
        print("WARN uppercase plate ref not remapped — fix by hand:", m.group(0))

    # recompute act-leaf "plates X–Y" ranges from current membership
    leaf_re = re.compile(r'(<div class="act-leaf">.*?</div>)(?=\n)', re.S)
    pieces = leaf_re.split(s)
    # pieces: [pre, leaf1, mid1, leaf2, mid2, ...]
    for i in range(1, len(pieces), 2):
        following = pieces[i + 1] if i + 1 < len(pieces) else ""
        romans = re.findall(r'class="plate-no">Plate ([IVXL]+) ·', following)
        if not romans:
            continue
        lo, hi = romans[0], romans[-1]
        rng = f"plate {lo}" if lo == hi else f"plates {lo}–{hi}"
        pieces[i] = re.sub(r"plates? [IVXL]+(?:–[IVXL]+)?", rng, pieces[i])
    return "".join(pieces)


def main() -> None:
    s = T.read_text()
    s2 = renumber_plates(s)
    if s2 != s:
        s = s2
        if "--dry-run" not in sys.argv:
            T.write_text(s)
    secs = [(m.start(), m.end()) for m in re.finditer(r"<section(?:\s[^>]*)?>.*?</section>", s, re.S)]
    plates = []
    for a, b in secs:
        m = re.search(r'class="plate-no">([^<]+)<', s[a:b])
        name = m.group(1).strip() if m else ""
        ids = []
        for mm in DEF_RE.finditer(s[a:b]):
            if mm.group(1) not in ids:
                ids.append(mm.group(1))
        plates.append((name, ids))

    figmap: dict[str, str] = {}
    counter = 0
    s_counter = 0
    for name, ids in plates:
        if not ids:
            continue
        if name.startswith("Prolegomenon"):
            # Figures before Plate I are named, not numbered (Census, The Season,
            # The Tide): an "S" series would be one more unexplained label.
            continue
        if not name.startswith("Plate "):
            continue
        counter += 1
        for old in ids:
            letter = old[len(old.rstrip("abcde")):] if old[-1] in "abcde" else ""
            assert old not in figmap, f"figure {old} defined in two plates ({figmap[old]} vs {counter}{letter})"
            figmap[old] = f"{counter}{letter}"

    changes = {k: v for k, v in figmap.items() if k != v}
    if not changes:
        print("already sequential — no-op")
        return
    print("map:", changes)

    ids_sorted = sorted(figmap, key=len, reverse=True)
    alt = "|".join(re.escape(i) for i in ids_sorted)
    lettered = {i for i in figmap if i[-1] in "abcde" or i.startswith("S")}
    pat = re.compile(r"(Figs?\.?(?:&nbsp;|\s| )+)?\b(" + alt + r")\b")
    stats = {"prefixed": 0, "bare": 0}
    audit = []

    def repl(m):
        pre, fid = m.group(1), m.group(2)
        if pre:
            stats["prefixed"] += 1
            return pre + figmap[fid]
        if fid in lettered:
            stats["bare"] += 1
            audit.append((fid, m.string[max(0, m.start() - 30):m.end() + 18].replace("\n", " ")))
            return figmap[fid]
        return fid

    s = pat.sub(repl, s)
    for fid, ctx in audit:
        print(f"BARE {fid}: ...{ctx}...")
    print(f"prefixed {stats['prefixed']} · bare {stats['bare']}")
    if "--dry-run" in sys.argv:
        print("dry run — not written")
        return
    T.write_text(s)
    print("written")


if __name__ == "__main__":
    main()
