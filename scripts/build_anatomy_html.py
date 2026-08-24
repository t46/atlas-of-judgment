"""Inject viz-data.json + galaxy.json into observatory_template.html.

Escapes "</" inside the JSON data islands so free-text fields can never
terminate the <script> block, then writes
data/analysis/iclr/unit-taxonomy-2026-v1/anatomy.html (the published artifact).
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"


def load_json_island(name: str) -> str:
    return (OUTPUT_DIR / name).read_text().strip().replace("</", "<\\/")


def main() -> None:
    template = (PROJECT_ROOT / "scripts/atlas_template.html").read_text()
    html = template.replace("__DATA_JSON__", load_json_island("viz-data.json"))
    html = html.replace("__GALAXY_JSON__", load_json_island("galaxy.json"))
    html = html.replace("__FIELD_JSON__", load_json_island("field-stats.json"))
    html = html.replace("__SCORE_JSON__", load_json_island("score-data.json"))
    html = html.replace("__DEPTH_JSON__", load_json_island("score-depth.json"))
    html = html.replace("__CONSTRUCT_JSON__", load_json_island("construct-data.json"))
    html = html.replace("__STRUCTURE_JSON__", load_json_island("structure-data.json"))
    html = html.replace("__ORACLE_JSON__", load_json_island("oracle-data.json"))
    html = html.replace("__LOTTERY_JSON__", load_json_island("lottery-data.json"))
    html = html.replace("__LIFECYCLE_JSON__", load_json_island("lifecycle-data.json"))
    html = html.replace("__REPAIR_JSON__", load_json_island("repair-manual.json"))
    html = html.replace("__OVERRULE_JSON__", load_json_island("overrule-data.json"))
    html = html.replace("__SEASON_JSON__", load_json_island("season-data.json"))
    html = html.replace("__DELIB_JSON__", load_json_island("deliberation-data.json"))
    html = html.replace("__REPERTOIRE_JSON__", load_json_island("repertoire-data.json"))
    html = html.replace("__CURRENTS_JSON__", load_json_island("currents-data.json"))
    html = html.replace("__ITIN_JSON__", load_json_island("itinerary-data.json"))
    html = html.replace("__JURIS_JSON__", load_json_island("jurisprudence-data.json"))
    html = html.replace("__ARCHI_JSON__", load_json_island("archipelago-data.json"))
    html = html.replace("__COURT_JSON__", load_json_island("court-data.json"))
    html = html.replace("__TALK_JSON__", load_json_island("threads-data.json"))
    html = html.replace("__FORM_JSON__", load_json_island("boilerplate-data.json"))
    html = html.replace("__PARTY_JSON__", load_json_island("searchparty-data.json"))
    html = html.replace("__CFX_JSON__", load_json_island("counterfactual-data.json"))
    html = html.replace("__ELEMS_JSON__", load_json_island("elements-all.json"))
    html = html.replace("__NINE_JSON__", load_json_island("ninegrammar-data.json"))
    html = html.replace("__INTQ_JSON__", load_json_island("interrogative-data.json"))
    html = html.replace("__TIDE_JSON__", load_json_island("tide-data.json"))
    html = html.replace("__CHAIN_JSON__", load_json_island("chain-data.json"))
    html = html.replace("__COMMIT_JSON__", load_json_island("commit-data.json"))
    html = html.replace("__CANON_JSON__", load_json_island("canon-data.json"))
    html = html.replace("__LAWT_JSON__", load_json_island("lawtariff-data.json"))
    html = html.replace("__SHEET_JSON__", load_json_island("chargesheet-data.json"))
    html = html.replace("__ACECHO_JSON__", load_json_island("acecho-data.json"))
    html = html.replace("__TML_JSON__", load_json_island("timeless-data.json"))
    html = html.replace("__MOVES_JSON__", load_json_island("moves-data.json"))
    html = html.replace("__COMBO_JSON__", load_json_island("combination-data.json"))
    html = html.replace("__TRIBCI_JSON__", load_json_island("tribunal-ci.json"))
    direct_dir = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
    for placeholder, name in (
        ("__DRIFT_JSON__", "drift-data.json"),
        ("__PANEL_JSON__", "panel-data.json"),
        ("__MINDS_JSON__", "minds-data.json"),
        ("__MIRAGE_JSON__", "mirage-data.json"),
        ("__RHET_JSON__", "rhetoric-v2.json"),
        ("__YIELD_JSON__", "yield-data.json"),
    ):
        html = html.replace(
            placeholder, (direct_dir / name).read_text().strip().replace("</", "<\\/")
        )
    out = OUTPUT_DIR / "anatomy.html"
    out.write_text(html)
    print(f"{out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
