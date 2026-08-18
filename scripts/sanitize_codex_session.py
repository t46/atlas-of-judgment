"""Sanitize the canonical Codex session history for public release.

Reads the session JSONL cited by docs/provenance/README.md, applies three
redactions, verifies the output is clean, and writes it into export/:

  1. `encrypted_content` blobs (opaque ciphertext) -> stripped. These blobs
     also produced every `sk-...` / `hf_...` false positive in the secret scan.
  2. 1Password references (op://vault/item/...) -> op://[REDACTED-1PASSWORD-REF]
     (references, not secrets, but they leak vault/item naming).
  3. The owner's personal email -> [EMAIL-REDACTED].

After writing, re-scans the output for known secret formats and refuses to
finish if anything is found. No network access; read-only on the source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(
    "/Users/s30825/.codex/sessions/2026/08/11/"
    "rollout-2026-08-11T18-17-08-019ff01c-83cb-7060-ac21-3658f8b4a748.jsonl"
)
OUT_DIR = Path(__file__).resolve().parent.parent / "export/codex-history"
OUT = OUT_DIR / "session-2026-08-11-sanitized.jsonl"

OP_REF = re.compile(r"op://[^\s\"'\\]+")
EMAIL = re.compile(r"takagi4646@gmail\.com")
VERIFY = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pos]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"op://(?!\[REDACTED)"),
    re.compile(r"takagi4646"),
]

stats = {"lines": 0, "stripped_blobs": 0, "op_refs": 0, "emails": 0}


def clean(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "encrypted_content" and isinstance(v, str) and len(v) > 40:
                stats["stripped_blobs"] += 1
                out[k] = "[STRIPPED: encrypted reasoning blob]"
            else:
                out[k] = clean(v)
        return out
    if isinstance(obj, list):
        return [clean(x) for x in obj]
    if isinstance(obj, str):
        s2, n1 = OP_REF.subn("op://[REDACTED-1PASSWORD-REF]", obj)
        s2, n_extra = re.subn(r"op://(?!\[REDACTED)", "op://[REDACTED-1PASSWORD-REF]", s2)
        n1 += n_extra
        s2, n2 = EMAIL.subn("[EMAIL-REDACTED]", s2)
        stats["op_refs"] += n1
        stats["emails"] += n2
        return s2
    return obj


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with SRC.open(encoding="utf-8", errors="replace") as src, OUT.open("w") as out:
        for line in src:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                out.write(json.dumps(clean(json.loads(line)), ensure_ascii=False) + "\n")
            except json.JSONDecodeError:
                s2 = EMAIL.sub("[EMAIL-REDACTED]", OP_REF.sub("op://[REDACTED-1PASSWORD-REF]", line))
                out.write(s2 + "\n")
    print("sanitized:", stats)

    findings = []
    for pat in VERIFY:
        for line_no, line in enumerate(OUT.open(encoding="utf-8", errors="replace"), 1):
            if pat.search(line):
                findings.append((pat.pattern, line_no))
                break
    if findings:
        OUT.unlink()
        raise SystemExit(f"VERIFICATION FAILED — output deleted. Hits: {findings}")
    size_mb = OUT.stat().st_size / 1e6
    print(f"VERIFIED CLEAN: {OUT} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
