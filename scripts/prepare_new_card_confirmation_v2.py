"""Prepare an independent second pass over v2-confirmed candidate pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .prepare_new_card_refinement_v2 import protocol
except ImportError:  # Direct script execution.
    from prepare_new_card_refinement_v2 import protocol


ROOT = Path("data/analysis/iclr/episode-reclassification-3135")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def add_unique(target: dict[tuple[str, str], dict], row: dict, label: str) -> None:
    key = row["episode_id"], row["card_id"]
    if key in target:
        raise ValueError(f"duplicate {label} pair: {key[0]}:{key[1]}")
    target[key] = row


def prepare(refinement: Path, output: Path, shard_size: int = 11) -> dict:
    source_by_key: dict[tuple[str, str], dict] = {}
    result_by_key: dict[tuple[str, str], dict] = {}
    manifest = json.loads((refinement / "manifest.json").read_text())
    shard_ids = [row["shard"] for row in manifest["shards"]]
    if len(shard_ids) != len(set(shard_ids)) or manifest.get("shard_count") != len(shard_ids):
        raise ValueError("refinement manifest has duplicate shards or an invalid shard_count")
    for shard in [row["shard"] for row in manifest["shards"]]:
        manifest_row = next(row for row in manifest["shards"] if row["shard"] == shard)
        expected = [tuple(key) for key in manifest_row["keys"]]
        source_rows = load(refinement / f"source-shard-{shard:03d}.jsonl")
        result_rows = load(refinement / f"refined-shard-{shard:03d}.jsonl")
        if [(row["episode_id"], row["card_id"]) for row in source_rows] != expected:
            raise ValueError(f"source keys differ from manifest in shard {shard:03d}")
        if [(row["episode_id"], row["card_id"]) for row in result_rows] != expected:
            raise ValueError(f"result keys differ from manifest in shard {shard:03d}")
        for row in source_rows:
            add_unique(source_by_key, row, "source")
        for row in result_rows:
            add_unique(result_by_key, row, "result")
    if manifest.get("pair_count") != len(source_by_key) or set(source_by_key) != set(result_by_key):
        raise ValueError("refinement manifest/source/result coverage mismatch")
    confirmed = {key for key, row in result_by_key.items() if row["verdict"] == "confirmed"}
    rows = [source_by_key[key] for key in confirmed]
    rows.sort(key=lambda row: hashlib.sha256(f"confirm:{row['episode_id']}:{row['card_id']}".encode()).hexdigest())
    output.mkdir(parents=True, exist_ok=True)
    shards = []
    for index, offset in enumerate(range(0, len(rows), shard_size), 1):
        batch = rows[offset:offset + shard_size]
        (output / f"source-shard-{index:03d}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in batch)
        )
        shards.append({
            "shard": index, "pair_count": len(batch),
            "keys": [[row["episode_id"], row["card_id"]] for row in batch],
        })
    output_manifest = {
        "version": 2, "independent_confirmation": True, "outcome_blind": True,
        "prior_first_pass_verdict_blind": True, "pair_count": len(rows),
        "shard_count": len(shards), "shards": shards,
    }
    (output / "manifest.json").write_text(json.dumps(output_manifest, indent=2) + "\n")
    (output / "REFINEMENT_PROTOCOL.md").write_text(protocol())
    return output_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refinement", type=Path, default=ROOT / "new-card-refinement-v2")
    parser.add_argument("--output", type=Path, default=ROOT / "new-card-confirmation-v2")
    parser.add_argument("--shard-size", type=int, default=11)
    args = parser.parse_args()
    output_manifest = prepare(args.refinement, args.output, args.shard_size)
    print(json.dumps({"pairs": output_manifest["pair_count"], "shards": output_manifest["shard_count"]}))


if __name__ == "__main__":
    main()
