"""Replace generic regional-02 chain placeholders with supporting local chains."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("data/analysis/iclr/episode-reclassification-3135/unmapped-discovery")
SOURCE_LOCAL = {
    "R-02-P01": "U-05-P01",
    "R-02-P02": "U-05-P02",
    "R-02-P03": "U-05-P03",
    "R-02-P04": "U-06-P03",
    "R-02-P05": "U-07-P02",
    "R-02-P06": "U-08-P01",
    "R-02-P07": "U-08-P04",
}


def main() -> None:
    local = {}
    for group in range(5, 9):
        data = json.loads((ROOT / f"local-patterns-{group:02d}.json").read_text())
        local.update(
            {row["candidate_pattern_id"]: row for row in data["candidate_patterns"]}
        )
    path = ROOT / "regional-patterns-02.json"
    data = json.loads(path.read_text())
    for row in data["regional_patterns"]:
        source_id = SOURCE_LOCAL[row["regional_pattern_id"]]
        if source_id not in row["supporting_local_pattern_ids"]:
            raise ValueError(f"{source_id} is not declared support for {row['regional_pattern_id']}")
        row["chain_template"] = local[source_id]["chain_template"]
        notes = row.get("notes")
        if isinstance(notes, list):
            notes.append(f"Chain wording restored from detailed local support {source_id} after generic regional placeholder audit.")
        elif isinstance(notes, str):
            row["notes"] = [notes, f"Chain wording restored from detailed local support {source_id} after generic regional placeholder audit."]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
