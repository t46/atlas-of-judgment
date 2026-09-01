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
    '<link rel="icon" type="image/svg+xml" href="/favicon.svg">\n'
)

SITE = "https://atlas-of-judgment.pages.dev"
X_HANDLE = "@takagi_shiro"
GH_REPO = "t46/atlas-of-judgment"

# social cards: og/twitter meta per page, one shared card image
OG = {
    "index.html": ("Atlas of Judgment — How a Paper is Judged",
        "An interactive atlas of the reasoning inside every public ICLR peer review, "
        "2018–2026: 1,009,592 units of reviewer logic, analysed across 30 plates."),
    "about.html": ("About — Atlas of Judgment",
        "What this project is, why the reasoning and not the score, and why ICLR: "
        "the question, the pipeline, and the findings digest."),
    "method.html": ("Method — Atlas of Judgment",
        "The full reproduction record: every script, model, seed and cost — including "
        "the pilots that failed and the corrections as they happened."),
    "resources.html": ("Resources — Atlas of Judgment",
        "Source code, data downloads, the machine-reader API, citation, and licence — "
        "every outbound door on one page."),
    "api": ("Machine Reader — Atlas of Judgment",
        "A static JSON API over the same audited data the plates are built from: "
        "per-plate depositions, 48 data islands, corrections, llms.txt."),
}


def og_block(out_name: str) -> str:
    title, desc = OG[out_name]
    path = "" if out_name == "index.html" else ("api/" if out_name == "api" else out_name.removesuffix(".html"))
    return (
        f'<meta name="description" content="{desc}">\n'
        '<meta property="og:type" content="website">\n'
        '<meta property="og:site_name" content="Atlas of Judgment">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f'<meta property="og:url" content="{SITE}/{path}">\n'
        f'<meta property="og:image" content="{SITE}/card.jpg">\n'
        '<meta property="og:image:width" content="1200">\n'
        '<meta property="og:image:height" content="630">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:site" content="{X_HANDLE}">\n'
        f'<meta name="twitter:creator" content="{X_HANDLE}">\n'
    )

PAGES = [
    ("anatomy.html", "index.html", {ABOUT_URL: "about.html", METHOD_URL: "method.html"}),
    ("about.html", "about.html", {ATLAS_URL: "index.html", METHOD_URL: "method.html"}),
    ("method.html", "method.html", {ATLAS_URL: "index.html", ABOUT_URL: "about.html"}),
    ("resources.html", "resources.html", {ATLAS_URL: "index.html", ABOUT_URL: "about.html", METHOD_URL: "method.html"}),
]


VDIRECT = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
API_ASSETS = PROJECT_ROOT / "scripts/api_assets"
DEPOSITIONS = PROJECT_ROOT / "notes/depositions"

# plate catalogue for /api/v1/index.json and the docs page (islands per the
# DOM-verified map in notes/api-layer-draft.md §2.2)
PLATES = [
    ("plate-i", "I", "The Anatomy", "I · The Instrument", ["viz-data.json", "panel-data.json", "construct-data.json"]),
    ("plate-ii", "II", "The Grammar", "I · The Instrument", ["viz-data.json", "ninegrammar-data.json"]),
    ("plate-iii", "III", "The Rhetoric", "I · The Instrument", ["rhetoric-v2.json"]),
    ("plate-iv", "IV", "The Syntax", "II · One Hand", ["chain-data.json"]),
    ("plate-v", "V", "The Itinerary", "II · One Hand", ["structure-data.json", "itinerary-data.json"]),
    ("plate-vi", "VI", "The Early Verdict", "II · One Hand", ["commit-data.json", "score-data.json"]),
    ("plate-vii", "VII", "The Elements", "III · The Law", ["elements-all.json", "argument-raw-novelty.json"]),
    ("plate-viii", "VIII", "The Combination Clause", "III · The Law", ["combination-data.json"]),
    ("plate-ix", "IX", "The Unnamed Precedent", "III · The Law", ["canon-data.json"]),
    ("plate-x", "X", "The Formulary", "III · The Law", ["boilerplate-data.json", "jurisprudence-data.json"]),
    ("plate-xi", "XI", "The Repair Manual", "III · The Law", ["repair-manual.json", "repair-k-robustness.json"]),
    ("plate-xii", "XII", "The Verdicts", "III · The Law", ["viz-data.json"]),
    ("plate-xiii", "XIII", "The Charge Sheet", "III · The Law", ["chargesheet-data.json"]),
    ("plate-xiv", "XIV", "The Price", "IV · The Tariff", ["lawtariff-data.json", "jurisprudence-data.json"]),
    ("plate-xv", "XV", "The Consequence", "IV · The Tariff", ["tribunal-ci.json", "panel-data.json", "decision-data.json"]),
    ("plate-xvi", "XVI", "The Shapes of Talk", "V · The Encounter", ["threads-data.json"]),
    ("plate-xvii", "XVII", "The Rebuttal", "V · The Encounter", ["yield-data.json", "panel-data.json"]),
    ("plate-xviii", "XVIII", "The Moves", "V · The Encounter", ["moves-data.json"]),
    ("plate-xix", "XIX", "The Fate of an Objection", "V · The Encounter", ["lifecycle-data.json", "interrogative-data.json"]),
    ("plate-xx", "XX", "The Panel", "V · The Encounter", ["overrule-data.json", "panel-data.json", "searchparty-data.json"]),
    ("plate-xxi", "XXI", "The Deliberation", "V · The Encounter", ["deliberation-data.json", "repertoire-data.json", "overrule-data.json"]),
    ("plate-xxii", "XXII", "The Higher Court", "VI · The Higher Court", ["court-data.json"]),
    ("plate-xxiii", "XXIII", "The Borrowed Verdict", "VI · The Higher Court", ["acecho-data.json"]),
    ("plate-xxiv", "XXIV", "The Ladder", "VII · The Measure", ["score-depth.json", "score-data.json"]),
    ("plate-xxv", "XXV", "The Measurement", "VII · The Measure", ["lottery-data.json", "counterfactual-data.json"]),
    ("plate-xxvi", "XXVI", "The Drift", "VIII · Eras & Territories", ["drift-data.json", "currents-data.json", "minds-data.json", "rhetoric-v2.json"]),
    ("plate-xxvii", "XXVII", "The Unamended Code", "VIII · Eras & Territories", ["drift-data.json", "timeless-data.json"]),
    ("plate-xxviii", "XXVIII", "The Oracle", "VIII · Eras & Territories", ["oracle-data.json", "archipelago-data.json"]),
    ("plate-xxix", "XXIX", "The Watermark", "VIII · Eras & Territories", ["llmtrace-data.json"]),
    ("plate-xxx", "XXX", "The Specimens", "Coda · The Archive", ["viz-data.json"]),
    ("appendix-i", "App. I", "The Lexicon", "Appendices", []),
    ("appendix-ii", "App. II", "Provenance & Method", "Appendices", ["viz-data.json", "structure-data.json", "galaxy.json", "panel-data.json"]),
    ("appendix-iii", "App. III", "The Null Cabinet", "Appendices", ["mirage-data.json", "field-stats.json"]),
]


def build_api(dist: Path) -> None:
    """Stage the machine-reader layer: /api/v1/*, /llms.txt, /openapi.yaml, /api/."""
    import hashlib
    import json
    import re
    import shutil
    from datetime import date

    api = dist / "api" / "v1"
    (api / "data").mkdir(parents=True, exist_ok=True)
    (api / "plates").mkdir(parents=True, exist_ok=True)

    # 1. data islands: every file build_anatomy_html.py injects, served verbatim
    build_src = (PROJECT_ROOT / "scripts/build_anatomy_html.py").read_text()
    island_files = sorted(set(re.findall(r'load_json_island\("([a-z0-9_\-]+\.json)"(?:, direct=True)?\)', build_src))
                          | set(re.findall(r'"([a-z0-9_\-]+\.json)"', build_src)))
    # Any island a deposition points at has to be published too, or that claim
    # dead-ends for the machine reader the depositions exist for. Scan them
    # instead of hand-keeping a list: control and robustness records are never
    # injected into a page, so the regex above cannot see them.
    dep_islands: dict[str, set[str]] = {}
    if DEPOSITIONS.exists():
        for f in sorted(DEPOSITIONS.glob("*.json")):
            dep = json.loads(f.read_text())
            names = {c["source_island"]
                     for fig in dep.get("figures", []) for c in fig.get("claims", [])
                     if c.get("source_island")}
            names |= {Path(d).name for d in dep.get("links", {}).get("data", [])}
            dep_islands[f.stem] = names
    cited = set().union(*dep_islands.values()) if dep_islands else set()
    island_files = sorted(set(island_files) | {"repair-k-robustness.json"} | cited)

    islands = []
    for name in island_files:
        src = V / name if (V / name).exists() else VDIRECT / name
        if not src.exists() or not name.endswith(".json"):
            if name in cited:
                raise SystemExit(
                    f"deposition cites an island that does not exist: {name} — "
                    "a published claim would dead-end")
            continue
        shutil.copy(src, api / "data" / name)
        used_by = sorted({p[0] for p in PLATES if name in p[4]}
                         | {pid for pid, names in dep_islands.items() if name in names})
        islands.append({
            "file": name,
            "bytes": src.stat().st_size,
            "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
            "track": "2018-2026 direct" if src.parent == VDIRECT else "2026 full-depth",
            "plates": used_by,
            "status": "active" if used_by else "orphaned",
        })

    # 1b. the correction ledger (mirrors method sec.10; hand-maintained asset)
    json.loads((API_ASSETS / "corrections.json").read_text())  # fail loudly if invalid
    shutil.copy(API_ASSETS / "corrections.json", api / "corrections.json")

    # 2. depositions
    dep_ids = []
    if DEPOSITIONS.exists():
        for f in sorted(DEPOSITIONS.glob("*.json")):
            shutil.copy(f, api / "plates" / f.name)
            dep_ids.append(f.stem)

    # 3. index.json
    man = json.loads((V / "manifest.json").read_text())
    man_d = json.loads((VDIRECT / "manifest.json").read_text())
    index = {
        "name": "Atlas of Judgment — Machine Reader API",
        "version": "1.0.0",
        "generated": date.today().isoformat(),
        "corpus": {
            "units_2026": man["units"], "reviews_2026": man["reviews"],
            "units_2018_2026_direct": man_d["units"], "forums_2018_2026": man_d["forums"],
            "units_total": man["units"] + man_d["units"],
        },
        "endpoints": {
            "index": "/api/v1/index.json",
            "deposition": "/api/v1/plates/{plate-id}.json",
            "data": "/api/v1/data/{island}.json",
            "corrections": "/api/v1/corrections.json",
            "contact": "/api/v1/contact.json",
            "openapi": "/openapi.yaml",
            "llms": "/llms.txt",
            "docs": "/api/",
        },
        "plates": [
            {"id": pid, "number": no, "title": title, "act": act, "islands": isl,
             "deposition": f"/api/v1/plates/{pid}.json" if pid in dep_ids else None,
             "deposition_status": "available" if pid in dep_ids else "pending"}
            for pid, no, title, act, isl in PLATES
        ],
        "islands": islands,
    }
    (api / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))

    # 3b. contact.json — the inbox, described so an agent can use it unaided.
    # Deliberately not a write endpoint on this host: a public POST box would need
    # moderation and would collect arbitrary text under this project's name, while
    # the issue tracker is already a write API, already moderated, and public in the
    # same way the correction ledger is.
    contact = {
        "what": "How to reach the author of this atlas, including from an agent with no human "
                "in the loop. This host serves static files only — it has no write endpoint and "
                "issues no API keys. The inbox is the project's public issue tracker, which you "
                "call with your own credentials.",
        "author": {
            "name": "Shiro Takagi",
            "x": f"https://x.com/{X_HANDLE.lstrip('@')}",
            "conversation": "Use X for anything that wants a conversation rather than a ticket.",
        },
        "channels": [
            {
                "id": "correction",
                "for": "A claim on this site you believe is wrong, or a derivation that does not "
                       "reproduce when you run its recompute script.",
                "visibility": "public",
                "web": f"https://github.com/{GH_REPO}/issues/new?template=correction.yml",
                "api": {
                    "method": "POST",
                    "url": f"https://api.github.com/repos/{GH_REPO}/issues",
                    "auth": "Your own GitHub token. This site holds no credentials for you.",
                    "body": {
                        "title": "correction: <claim-id> — <one line>",
                        "labels": ["correction"],
                        "body": "Markdown; see report_fields below.",
                    },
                },
                "report_fields": {
                    "claim_id": "Required. e.g. plate-xi#11-extend-leads — name the id, not the "
                                "caption sentence; the id resolves to the derivation and survives "
                                "a rewrite of the prose around it.",
                    "as_published": "The value or statement currently on the site.",
                    "proposed": "What you believe is correct.",
                    "evidence": "How you checked — a recompute you ran, a source island and path, "
                                "or an argument about the derivation.",
                    "confidence": "Optional. Say plainly if you are unsure; a flagged suspicion is "
                                  "still useful.",
                },
                "what_happens": "A confirmed error is corrected in place on the plate, recorded in "
                                "method §10, and published at /api/v1/corrections.json — so the "
                                "correction becomes part of the public record, not a private fix.",
            },
            {
                "id": "question",
                "for": "A question about the method, the taxonomy, the corpus, or a caveat.",
                "visibility": "public",
                "web": f"https://github.com/{GH_REPO}/issues/new?template=question.yml",
                "api": {
                    "method": "POST",
                    "url": f"https://api.github.com/repos/{GH_REPO}/issues",
                    "auth": "Your own GitHub token.",
                    "body": {"title": "question: <one line>", "labels": ["question"]},
                },
            },
            {
                "id": "collaboration",
                "for": "Work you would like to do with this — a different view built on the same "
                       "islands, a replication on another venue, a joint analysis.",
                "visibility": "public or direct",
                "web": f"https://github.com/{GH_REPO}/issues/new?template=question.yml",
                "direct": f"https://x.com/{X_HANDLE.lstrip('@')}",
                "note": "The licence already permits building on this (code MIT, derived data "
                        "CC BY 4.0) — you do not need permission, only an interest in comparing notes.",
            },
        ],
        "not_available": {
            "write_endpoint": "There is no POST endpoint under /api/ on this host.",
            "api_keys": "None are issued. Any key you need is your own, for the channel above.",
            "email": "No address is published; use the tracker or X.",
        },
    }
    (api / "contact.json").write_text(json.dumps(contact, indent=1, ensure_ascii=False))

    # 4. llms.txt (counts interpolated so a corpus refresh can't leave it stale)
    llms = (API_ASSETS / "llms.txt").read_text()
    llms = (llms.replace("__TOTAL_UNITS__", f"{man['units'] + man_d['units']:,}")
                .replace("__U2026__", f"{man['units']:,}")
                .replace("__R2026__", f"{man['reviews']:,}")
                .replace("__ISLAND_COUNT__", str(len(islands)))
                .replace("__PLATE_COUNT__", str(len(dep_ids))))
    (dist / "llms.txt").write_text(llms)

    # 5. openapi.yaml
    shutil.copy(API_ASSETS / "openapi.yaml", dist / "openapi.yaml")

    # 6. docs page — plate table + island holdings generated from the same index data
    rows = []
    for p in index["plates"]:
        dep = (f'<a href="{p["deposition"]}">JSON</a>' if p["deposition"]
               else '<span class="pending">PENDING</span>')
        chips = "".join(f'<span class="chip">{i}</span>' for i in p["islands"]) or "—"
        rows.append(f'<tr><td><span class="pn">{p["number"]}</span></td><td>{p["title"]}</td>'
                    f'<td style="font-family:var(--mono);font-size:10px">{p["act"]}</td>'
                    f'<td>{dep}</td><td>{chips}</td></tr>')
    details = []
    for isl in islands:
        if isl["bytes"] >= 8_000_000:
            keys = "(large file)"
        else:
            doc = json.loads((api / "data" / isl["file"]).read_text())
            # a few records are arrays at the root, not objects
            keys = ", ".join(list(doc)[:8]) if isinstance(doc, dict) else \
                f"(array of {len(doc)} records)"
        flag = ' · <span style="color:var(--muted)">orphaned — not currently rendered on any plate</span>' \
            if isl["status"] == "orphaned" else ""
        plates_txt = ", ".join(isl["plates"]) or "—"
        details.append(
            f'<details><summary>{isl["file"]}<span class="sz">{isl["bytes"] / 1e6:.2f} MB · {isl["track"]}</span></summary>'
            f'<div class="body">read by: {plates_txt}{flag}<br>top-level keys: <code>{keys}</code>'
            f'<br><a href="/api/v1/data/{isl["file"]}">download</a></div></details>')
    docs = (API_ASSETS / "api-docs.html").read_text()
    docs = (docs.replace("__PLATE_ROWS__", "\n    ".join(rows))
                .replace("__ISLANDS__", "\n  ".join(details))
                .replace("__ISLAND_COUNT__", str(len(islands)))
                .replace("__BUILD_DATE__", date.today().isoformat()))
    if all(p["deposition"] for p in index["plates"]):
        # nothing is pending — drop the transcription-status note entirely
        docs = re.sub(r"<p[^>]*>Depositions marked[^<]*<span[^>]*>PENDING</span>[^<]*</p>\n?", "", docs)
    (dist / "api" / "index.html").write_text(HEAD + og_block("api") + docs)
    print(f"api: {len(islands)} islands, {len(dep_ids)} deposition(s), index + llms.txt + openapi + docs staged")


NOT_FOUND = """<title>Not in the record — Atlas of Judgment</title>
<meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>
  :root { --ground:#efe4c9; --paper:#f3ead2; --ink:#241c14; --brass:#8a5a1f; --muted:#7d7059;
          --serif:"Cormorant Garamond",Georgia,serif; --mono:"IBM Plex Mono",ui-monospace,monospace; }
  html,body { margin:0; height:100%; background:var(--ground); color:var(--ink); }
  main { min-height:100%; box-sizing:border-box; display:flex; flex-direction:column;
         justify-content:center; max-width:640px; margin:0 auto; padding:48px 28px; }
  .code { font:400 12px/1 var(--mono); letter-spacing:.22em; color:var(--brass); }
  h1 { font:400 clamp(38px,9vw,60px)/1.1 var(--serif); margin:18px 0 0; }
  h1 em { font-style:italic; }
  hr { border:0; border-top:1px solid rgba(138,90,31,.4); margin:26px 0; }
  p { font:400 clamp(18px,4.4vw,21px)/1.55 var(--serif); margin:0 0 18px; }
  nav { display:flex; flex-wrap:wrap; gap:10px 22px; margin-top:8px;
        font:400 12px/1 var(--mono); letter-spacing:.14em; text-transform:uppercase; }
  a { color:var(--brass); }
  .machine { margin-top:34px; padding:16px 18px; background:var(--paper);
             border-left:2px solid var(--brass); font:400 12.5px/1.7 var(--mono); color:var(--muted); }
  .machine code { color:var(--ink); }
</style>
<main>
  <span class="code">404 &middot; NO SUCH HOLDING</span>
  <h1>This address is not <em>in the record</em>.</h1>
  <hr>
  <p>Every page and every data file in this atlas is listed somewhere. Nothing else exists at
     this address &mdash; not a moved page, simply one that was never here.</p>
  <nav>
    <a href="/">The Atlas</a><a href="/about">About</a><a href="/method">Method</a>
    <a href="/resources">Resources</a><a href="/api/">Machine reader</a>
  </nav>
  <div class="machine">
    Machine readers: the catalogue of every valid endpoint, plate id and data island is
    <code><a href="/api/v1/index.json">/api/v1/index.json</a></code>.<br>
    Start here: <code><a href="/llms.txt">/llms.txt</a></code> &middot;
    schema: <code><a href="/openapi.yaml">/openapi.yaml</a></code>
  </div>
</main>
"""

# Cloudflare Pages edge rules. Without a 404.html, Pages falls back to index.html
# with a 200 — so a mistyped plate id used to return the whole 7 MB atlas page to a
# machine reader. The cache rules keep the launch-day spike off the origin while
# staying short enough that a correction propagates within the hour.
HEADERS = """/api/v1/data/*
  Cache-Control: public, max-age=300, s-maxage=3600, stale-while-revalidate=86400

/api/v1/plates/*
  Cache-Control: public, max-age=300, s-maxage=3600, stale-while-revalidate=86400

/api/v1/index.json
  Cache-Control: public, max-age=300, s-maxage=3600

/api/v1/corrections.json
  Cache-Control: public, max-age=300, s-maxage=3600

/openapi.yaml
  Cache-Control: public, max-age=300, s-maxage=3600

/llms.txt
  Cache-Control: public, max-age=300, s-maxage=3600

/favicon.svg
  Cache-Control: public, max-age=604800

/card.jpg
  Cache-Control: public, max-age=604800
"""

ROBOTS = f"""User-agent: *
Allow: /

# Machine readers are first-class here — you do not have to scrape the pages.
#   /llms.txt           entry point, written for you
#   /api/v1/index.json  catalogue of every endpoint, plate and data island
#   /openapi.yaml       schema
# Text, figures and derived data are CC BY 4.0; code is MIT.

Sitemap: {SITE}/sitemap.xml
"""


def build_edge(dist: Path) -> None:
    """Stage robots.txt, sitemap.xml, 404.html and _headers."""
    from datetime import date

    today = date.today().isoformat()
    urls = "".join(
        f"  <url><loc>{SITE}/{p}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>monthly</changefreq><priority>{pri}</priority></url>\n"
        for p, pri in (("", "1.0"), ("about", "0.8"), ("method", "0.8"),
                       ("resources", "0.6"), ("api/", "0.6"))
    )
    files = {
        "robots.txt": ROBOTS,
        "sitemap.xml": ('<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                        f"{urls}</urlset>\n"),
        "404.html": HEAD + NOT_FOUND,
        "_headers": HEADERS,
    }
    for name, body in files.items():
        (dist / name).write_text(body)
        (REPO / name).write_text(body)
    print("edge: robots.txt, sitemap.xml, 404.html, _headers staged")


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
        head = HEAD + og_block(out_name)
        (REPO / out_name).write_text(head + html)
        (dist / out_name).write_text(head + html)
        print(f"{out_name}: {len(html) / 1e6:.2f} MB staged")
    import shutil
    for asset in ("favicon.svg", "card.jpg"):
        shutil.copy(API_ASSETS / asset, dist / asset)
        shutil.copy(API_ASSETS / asset, REPO / asset)
    build_api(dist)
    build_edge(dist)


if __name__ == "__main__":
    main()
