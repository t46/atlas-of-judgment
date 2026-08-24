"""Deposition ↔ page sync check (machine-reader layer, principle ④).

For every deposition in notes/depositions/, verify the cheap-but-real invariants:
  1. valid JSON with the required schema keys (matches plate-i.json's shape);
  2. every claim's source_island exists on disk (2026-v1 or direct-v1);
  3. every claim's recompute script exists;
  4. soft drift check: each claim's printed number (e.g. 0.718 → "71.8") still
     appears somewhere in the template's text — catches "caption edited,
     deposition forgotten" without re-deriving anything;
  5. dom_ref hover-bind keys actually exist in the template.

Run: uv run python scripts/check_deposition_sync.py
Exit code 1 if any HARD failure (1–3, 5); number-drift (4) reports as WARN only,
because prose legitimately words some quantities differently (e.g. "two thirds").
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS = ROOT / "notes/depositions"
V = ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
VD = ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
TPL = (ROOT / "scripts/atlas_template.html").read_text()

REQUIRED_TOP = {"id", "number", "title", "act", "corpus_scope", "question", "page_url", "figures", "caveats", "links"}
REQUIRED_CLAIM = {"id", "statement", "value", "source_island", "source_path", "derivation", "recompute", "dom_ref", "verified", "caveat_refs"}


def number_forms(q) -> list[str]:
    """Strings a quantity might appear as in prose (0.718 -> '71.8', '0.718'; 131 -> '131')."""
    if isinstance(q, list):
        out = []
        for x in q:
            out.extend(number_forms(x))
        return out
    if not isinstance(q, (int, float)):
        return []
    forms = [f"{q}"]
    if isinstance(q, float) and 0 < abs(q) < 1:
        pct = abs(q) * 100
        forms += [f"{pct:.1f}".rstrip("0").rstrip("."), f"{pct:.0f}", f"{abs(q)}"]
    if isinstance(q, (int, float)) and abs(q) >= 1000:
        forms.append(f"{q:,}")
    if isinstance(q, float):
        forms += [f"{abs(q):.2f}".rstrip("0").rstrip("."), f"{abs(q):.1f}"]
    # the template prints negatives with U+2212; match magnitude either way
    return [f.lstrip("-") for f in forms]


def main() -> None:
    hard, warn = 0, 0
    files = sorted(DEPS.glob("*.json"))
    if not files:
        print("no depositions found")
        sys.exit(1)
    for f in files:
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {f.name}: invalid JSON — {e}")
            hard += 1
            continue
        missing = REQUIRED_TOP - set(d)
        if missing:
            print(f"FAIL {f.name}: missing top-level keys {sorted(missing)}")
            hard += 1
        if d.get("id") != f.stem:
            print(f"FAIL {f.name}: id {d.get('id')!r} != filename stem")
            hard += 1
        caveat_ids = {c["id"] for c in d.get("caveats", [])}
        for fig in d.get("figures", []):
            for c in fig.get("claims", []):
                cm = REQUIRED_CLAIM - set(c)
                if cm:
                    print(f"FAIL {f.name} {c.get('id')}: missing claim keys {sorted(cm)}")
                    hard += 1
                    continue
                isl = c["source_island"]
                if isl and not ((V / isl).exists() or (VD / isl).exists()):
                    print(f"FAIL {f.name} {c['id']}: source island {isl} not on disk")
                    hard += 1
                rc = c["recompute"]
                if rc and not (ROOT / rc).exists():
                    print(f"FAIL {f.name} {c['id']}: recompute script {rc} not on disk")
                    hard += 1
                for ref in c["caveat_refs"]:
                    if ref not in caveat_ids:
                        print(f"FAIL {f.name} {c['id']}: caveat_ref {ref!r} not declared in caveats[]")
                        hard += 1
                if c["dom_ref"]:
                    key = c["dom_ref"].split("=")[0]
                    if key not in TPL:
                        print(f"FAIL {f.name} {c['id']}: dom_ref attribute {key!r} not in template")
                        hard += 1
                q = c["value"].get("quantity") if isinstance(c["value"], dict) else None
                forms = number_forms(q)
                if forms and not any(s in TPL for s in forms):
                    print(f"WARN {f.name} {c['id']}: none of {forms} found in template text")
                    warn += 1
    print(f"\n{len(files)} depositions · {hard} hard failures · {warn} number-drift warnings")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
