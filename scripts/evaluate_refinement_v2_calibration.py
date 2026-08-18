"""Compare v2 calibration outputs with independent skeptical audit verdicts."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path("data/analysis/iclr/episode-reclassification-3135")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    audit_dir = ROOT / "new-card-challenge-audit"
    labels: dict[tuple[str, str], str] = {}
    for card in ("N-P01", "N-P03"):
        for row in load(audit_dir / f"audit-{card}-a.jsonl"):
            if row["verdict"] in {"retain_distinct", "absorb_existing"}:
                labels[(row["episode_id"], card)] = (
                    "confirmed" if row["verdict"] == "retain_distinct" else "excluded"
                )
    n2a = {row["episode_id"]: row["verdict"] for row in load(audit_dir / "audit-N-P02-a.jsonl")}
    n2b = {row["episode_id"]: row["verdict"] for row in load(audit_dir / "audit-N-P02-b.jsonl")}
    for episode_id in n2a.keys() & n2b.keys():
        if n2a[episode_id] == n2b[episode_id] and n2a[episode_id] in {"retain_distinct", "absorb_existing"}:
            labels[(episode_id, "N-P02")] = "confirmed" if n2a[episode_id] == "retain_distinct" else "excluded"
    refinement = ROOT / "new-card-refinement-v2"
    predictions = []
    for path in sorted(refinement.glob("refined-shard-*.jsonl")):
        predictions.extend(load(path))
    scored = []
    for row in predictions:
        key = (row["episode_id"], row["card_id"])
        if key in labels:
            scored.append({"episode_id": key[0], "card_id": key[1], "expected": labels[key], "predicted": row["verdict"]})
    by_card = {}
    for card in ("N-P01", "N-P02", "N-P03"):
        rows = [row for row in scored if row["card_id"] == card]
        by_card[card] = {
            "n": len(rows), "agreement": sum(row["expected"] == row["predicted"] for row in rows),
            "matrix": dict(Counter(f"{row['expected']}->{row['predicted']}" for row in rows)),
        }
    result = {
        "scored_pair_count": len(scored),
        "agreement_count": sum(row["expected"] == row["predicted"] for row in scored),
        "agreement_rate": round(sum(row["expected"] == row["predicted"] for row in scored) / len(scored), 6) if scored else 0,
        "by_card": by_card,
        "disagreements": [row for row in scored if row["expected"] != row["predicted"]],
    }
    (refinement / "calibration-evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
