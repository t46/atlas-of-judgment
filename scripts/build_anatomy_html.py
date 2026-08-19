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
    direct_dir = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
    for placeholder, name in (
        ("__DRIFT_JSON__", "drift-data.json"),
        ("__PANEL_JSON__", "panel-data.json"),
        ("__MINDS_JSON__", "minds-data.json"),
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
