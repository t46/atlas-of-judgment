"""Prepare and stage the public site copies of the three atlas pages.

The Claude Artifact host wraps published files in a full HTML skeleton, but
Cloudflare Pages serves files verbatim — without a doctype the site renders in
quirks mode, and without a viewport meta mobile renders at desktop width.
This script rewrites cross-links to relative URLs, prepends the proper
skeleton header, and writes the result into the repo clone (which doubles as
the deploy dist). Deploy afterwards with:

  npx wrangler pages deploy /Users/s30825/unktok/dev/atlas-of-judgment/.pages-dist \
    --project-name atlas-of-judgment --branch main --commit-dirty=true

(The repo root also holds the 29MB sanitized codex history, which exceeds the
Pages 25MiB per-file limit, so the deploy runs from a clean .pages-dist copy —
gitignored — holding only the three pages.)
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
REPO = Path("/Users/s30825/unktok/dev/atlas-of-judgment")

ATLAS_URL = "https://claude.ai/code/artifact/0aa65ded-3852-4a3c-8994-0db2fe393e09"
ABOUT_URL = "https://claude.ai/code/artifact/6f0ed75c-f899-47ce-870e-6c3d5c45cc48"
METHOD_URL = "https://claude.ai/code/artifact/c76a5355-ce79-456e-9193-d1be6a6a0baa"

HEAD = (
    "<!doctype html>\n"
    '<html lang="en">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
)

PAGES = [
    ("anatomy.html", "index.html", {ABOUT_URL: "about.html", METHOD_URL: "method.html"}),
    ("about.html", "about.html", {ATLAS_URL: "index.html", METHOD_URL: "method.html"}),
    ("method.html", "method.html", {ATLAS_URL: "index.html", ABOUT_URL: "about.html"}),
]


def main() -> None:
    dist = REPO / ".pages-dist"
    dist.mkdir(exist_ok=True)
    import re
    for src_name, out_name, links in PAGES:
        html = (V / src_name).read_text()
        for url, rel in links.items():
            html = html.replace(url, rel)
        # "Live" links point at this very site once deployed - drop them
        html = re.sub(
            r'(<span style="color:var\(--muted\)"> · </span>)?<a href="https://atlas-of-judgment\.pages\.dev"[^>]*>Live[^<]*</a>',
            "", html)
        (REPO / out_name).write_text(HEAD + html)
        (dist / out_name).write_text(HEAD + html)
        print(f"{out_name}: {len(html) / 1e6:.2f} MB staged")


if __name__ == "__main__":
    main()
