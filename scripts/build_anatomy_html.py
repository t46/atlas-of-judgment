"""Inject the data islands into atlas_template.html as JSON script tags.

Islands ship as <script type="application/json"> blocks and are parsed with
JSON.parse via __ISL(name) — 2-4x faster than evaluating multi-MB JS object
literals, and it lets the template defer parsing of the 42 non-critical
islands until after the overture (only DATA and GALAXY parse up front).
"</" is escaped as "<\\/" (a legal JSON escape) so free text can never
terminate the script block. Writes
data/analysis/iclr/unit-taxonomy-2026-v1/anatomy.html.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"

ISLANDS = [
    ("DATA", "viz-data.json"), ("GALAXY", "galaxy.json"), ("FIELD", "field-stats.json"),
    ("SCORE", "score-data.json"), ("DEPTH", "score-depth.json"), ("CONSTRUCT", "construct-data.json"),
    ("STRUCTURE", "structure-data.json"), ("ORACLE", "oracle-data.json"), ("LOTTERY", "lottery-data.json"),
    ("LIFECYCLE", "lifecycle-data.json"), ("REPAIR", "repair-manual.json"), ("OVERRULE", "overrule-data.json"),
    ("SEASON", "season-data.json"), ("DELIB", "deliberation-data.json"), ("REPERTOIRE", "repertoire-data.json"),
    ("CURRENTS", "currents-data.json"), ("ITIN", "itinerary-data.json"), ("JURIS", "jurisprudence-data.json"),
    ("ARCHI", "archipelago-data.json"), ("COURT", "court-data.json"), ("TALK", "threads-data.json"),
    ("FORM", "boilerplate-data.json"), ("PARTY", "searchparty-data.json"), ("CFX", "counterfactual-data.json"),
    ("ELEMS", "elements-all.json"), ("NINE", "ninegrammar-data.json"), ("INTQ", "interrogative-data.json"),
    ("TIDE", "tide-data.json"), ("CHAIN", "chain-data.json"), ("COMMIT", "commit-data.json"),
    ("CANON", "canon-data.json"), ("LAWT", "lawtariff-data.json"), ("SHEET", "chargesheet-data.json"),
    ("ACECHO", "acecho-data.json"), ("TML", "timeless-data.json"), ("MOVES", "moves-data.json"),
    ("COMBO", "combination-data.json"), ("TRIBCI", "tribunal-ci.json"), ("ARG", "argument-raw-novelty.json"), ("DEC", "decision-data.json"),
    ("LLMTRACE", "llmtrace-data.json"),
]
DIRECT_ISLANDS = [
    ("DRIFT", "drift-data.json"), ("PANEL", "panel-data.json"), ("MINDS", "minds-data.json"),
    ("MIRAGE", "mirage-data.json"), ("RHET", "rhetoric-v2.json"), ("YIELD", "yield-data.json"),
]


def island_tag(name: str, path: Path) -> str:
    body = path.read_text().strip().replace("</", "<\\/")
    return f'<script type="application/json" id="isl-{name}">{body}</script>'


def galaxy_lite_tag() -> str:
    import json
    g = json.loads((OUTPUT_DIR / "galaxy.json").read_text())
    g["points"] = g["points"][::3]
    body = json.dumps(g, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" id="isl-GALAXY_LITE">{body}</script>'


def main() -> None:
    html = (PROJECT_ROOT / "scripts/atlas_template.html").read_text()
    tags = [island_tag(n, OUTPUT_DIR / f) for n, f in ISLANDS]
    tags += [island_tag(n, DIRECT_DIR / f) for n, f in DIRECT_ISLANDS]
    tags.append(galaxy_lite_tag())
    for n, _ in ISLANDS + DIRECT_ISLANDS:
        html = html.replace(f"__{n}_JSON__", f'__ISL("{n}")')
    assert "__ISLANDS_BLOCK__" in html, "template missing __ISLANDS_BLOCK__ placeholder"
    html = html.replace("__ISLANDS_BLOCK__", "\n".join(tags))
    out = OUTPUT_DIR / "anatomy.html"
    out.write_text(html)
    print(f"{out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
