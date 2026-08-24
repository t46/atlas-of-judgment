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


VDIRECT = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
API_ASSETS = PROJECT_ROOT / "scripts/api_assets"
DEPOSITIONS = PROJECT_ROOT / "notes/depositions"

# plate catalogue for /api/v1/index.json and the docs page (islands per the
# DOM-verified map in notes/api-layer-draft.md §2.2)
PLATES = [
    ("plate-i", "I", "The Anatomy", "I · The Instrument", ["viz-data.json", "panel-data.json"]),
    ("plate-ii", "II", "The Grammar", "I · The Instrument", ["viz-data.json", "ninegrammar-data.json"]),
    ("plate-iii", "III", "The Rhetoric", "I · The Instrument", ["rhetoric-v2.json"]),
    ("plate-iv", "IV", "The Syntax", "II · One Hand", ["chain-data.json"]),
    ("plate-v", "V", "The Itinerary", "II · One Hand", ["structure-data.json", "itinerary-data.json"]),
    ("plate-vi", "VI", "The Early Verdict", "II · One Hand", ["commit-data.json", "score-data.json"]),
    ("plate-vii", "VII", "The Elements", "III · The Law", ["elements-all.json"]),
    ("plate-viii", "VIII", "The Combination Clause", "III · The Law", ["combination-data.json"]),
    ("plate-ix", "IX", "The Unnamed Precedent", "III · The Law", ["canon-data.json"]),
    ("plate-x", "X", "The Formulary", "III · The Law", ["boilerplate-data.json", "jurisprudence-data.json"]),
    ("plate-xi", "XI", "The Repair Manual", "III · The Law", ["repair-manual.json"]),
    ("plate-xii", "XII", "The Verdicts", "III · The Law", ["viz-data.json"]),
    ("plate-xiii", "XIII", "The Charge Sheet", "III · The Law", ["chargesheet-data.json"]),
    ("plate-xiv", "XIV", "The Price", "IV · The Tariff", ["lawtariff-data.json", "jurisprudence-data.json"]),
    ("plate-xv", "XV", "The Consequence", "IV · The Tariff", ["tribunal-ci.json", "panel-data.json"]),
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
    ("plate-xxix", "XXIX", "The Specimens", "Coda · The Archive", ["viz-data.json"]),
    ("appendix-i", "App. I", "The Lexicon", "Appendices", []),
    ("appendix-ii", "App. II", "Provenance & Method", "Appendices", ["viz-data.json", "structure-data.json", "galaxy.json", "panel-data.json"]),
    ("appendix-iii", "App. III", "The Null Cabinet", "Appendices", ["mirage-data.json", "field-stats.json"]),
]


def build_api(dist: Path) -> None:
    """Stage the machine-reader layer: /api/v1/*, /llms.txt, /openapi.yaml, /api/."""
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
    islands = []
    for name in island_files:
        src = V / name if (V / name).exists() else VDIRECT / name
        if not src.exists() or not name.endswith(".json"):
            continue
        shutil.copy(src, api / "data" / name)
        used_by = [p[0] for p in PLATES if name in p[4]]
        islands.append({
            "file": name,
            "bytes": src.stat().st_size,
            "track": "2018-2026 direct" if src.parent == VDIRECT else "2026 full-depth",
            "plates": used_by,
            "status": "orphaned" if name == "construct-data.json" else "active",
        })

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

    # 4. llms.txt (counts interpolated so a corpus refresh can't leave it stale)
    llms = (API_ASSETS / "llms.txt").read_text()
    llms = (llms.replace("__TOTAL_UNITS__", f"{man['units'] + man_d['units']:,}")
                .replace("__U2026__", f"{man['units']:,}")
                .replace("__R2026__", f"{man['reviews']:,}"))
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
        keys = ", ".join(list(json.loads((api / "data" / isl["file"]).read_text()).keys())[:8]) \
            if isl["bytes"] < 8_000_000 else "(large file)"
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
                .replace("__BUILD_DATE__", date.today().isoformat()))
    (dist / "api" / "index.html").write_text(HEAD + docs)
    print(f"api: {len(islands)} islands, {len(dep_ids)} deposition(s), index + llms.txt + openapi + docs staged")


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
    build_api(dist)


if __name__ == "__main__":
    main()
