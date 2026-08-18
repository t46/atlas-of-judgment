"""Build a conservative 13-card candidate table from two-pass v2 consensus."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


ROOT = Path("data/analysis/iclr/episode-reclassification-3135")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pair_key(row: dict) -> tuple[str, str]:
    return row["episode_id"], row["card_id"]


def index_unique(rows: list[dict], label: str) -> dict[tuple[str, str], dict]:
    indexed: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = pair_key(row)
        if key in indexed:
            raise ValueError(f"duplicate {label} pair: {key[0]}:{key[1]}")
        indexed[key] = row
    return indexed


def load_shards(directory: Path, prefix: str) -> list[dict]:
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest_shards = manifest["shards"]
    shard_ids = [row["shard"] for row in manifest_shards]
    if len(shard_ids) != len(set(shard_ids)):
        raise ValueError(f"duplicate shard id in {directory / 'manifest.json'}")
    if manifest.get("shard_count") != len(manifest_shards):
        raise ValueError(f"manifest shard_count mismatch in {directory}")
    rows = []
    manifest_keys: list[tuple[str, str]] = []
    for manifest_row in manifest_shards:
        shard = manifest_row["shard"]
        shard_rows = load(directory / f"{prefix}-shard-{shard:03d}.jsonl")
        expected = [tuple(key) for key in manifest_row["keys"]]
        actual = [pair_key(row) for row in shard_rows]
        if actual != expected:
            raise ValueError(f"manifest/output key mismatch in {directory}, shard {shard:03d}")
        if manifest_row.get("pair_count") != len(shard_rows):
            raise ValueError(f"manifest pair_count mismatch in {directory}, shard {shard:03d}")
        manifest_keys.extend(expected)
        rows.extend(shard_rows)
    if manifest.get("pair_count") != len(rows):
        raise ValueError(f"manifest total pair_count mismatch in {directory}")
    if len(manifest_keys) != len(set(manifest_keys)):
        raise ValueError(f"duplicate episode/card pair across shards in {directory}")
    index_unique(rows, str(directory))
    return rows


def require_complete_confirmation(first: list[dict], second: list[dict]) -> dict[tuple[str, str], dict]:
    first_by_key = index_unique(first, "first-pass")
    second_by_key = index_unique(second, "confirmation-pass")
    expected = {key for key, row in first_by_key.items() if row["verdict"] == "confirmed"}
    actual = set(second_by_key)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            "confirmation coverage mismatch: "
            f"missing={len(missing)} {missing[:5]}, unexpected={len(unexpected)} {unexpected[:5]}"
        )
    return second_by_key


def main() -> None:
    existing = load(ROOT / "reclassified-membership.jsonl")
    first = load_shards(ROOT / "new-card-refinement-v2", "refined")
    second = load_shards(ROOT / "new-card-confirmation-v2", "refined")
    second_by_key = require_complete_confirmation(first, second)
    decisions = []
    confirmed_by_episode: dict[str, list[dict]] = defaultdict(list)
    uncertain_by_episode: dict[str, list[dict]] = defaultdict(list)
    for row in first:
        key = (row["episode_id"], row["card_id"])
        confirm = second_by_key.get(key)
        final = "excluded"
        if row["verdict"] == "confirmed" and confirm and confirm["verdict"] == "confirmed":
            final = "confirmed"
            membership = {
                "pattern_id": row["card_id"], "fit": confirm["fit"],
                "gate_evidence": confirm["gate_evidence"], "reason": confirm["reason"],
                "confidence": min((row["confidence"], confirm["confidence"]), key=("low", "medium", "high").index),
                "independent_confirmation": True,
                "first_pass_reason": row["reason"],
                "second_pass_reason": confirm["reason"],
            }
            confirmed_by_episode[row["episode_id"]].append(membership)
        elif row["verdict"] == "uncertain" or (row["verdict"] == "confirmed" and confirm and confirm["verdict"] != "confirmed"):
            final = "uncertain"
            reasons = [f"first pass: {row['verdict']} — {row['reason']}"]
            missing = list(row["missing_links"])
            if confirm:
                reasons.append(f"independent confirmation: {confirm['verdict']} — {confirm['reason']}")
                missing.extend(confirm["missing_links"])
            if row["verdict"] == "confirmed" and confirm and confirm["verdict"] != "confirmed":
                missing.append("independent_decision_disagreement")
            uncertain_by_episode[row["episode_id"]].append({
                "card_id": row["card_id"], "reason": " | ".join(reasons),
                "missing_links": sorted(set(missing)), "needs_source_audit": row["needs_source_audit"] or bool(confirm and confirm["needs_source_audit"]),
            })
        decisions.append({
            "episode_id": row["episode_id"], "card_id": row["card_id"],
            "first_pass_verdict": row["verdict"],
            "second_pass_verdict": confirm["verdict"] if confirm else None,
            "final_verdict": final,
            "first_pass": row, "second_pass": confirm,
        })
    (ROOT / "new-card-consensus-v2.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in decisions)
    )

    combined = []
    pattern_sets: dict[str, set[str]] = defaultdict(set)
    new_reviews: dict[str, set[str]] = defaultdict(set)
    new_papers: dict[str, set[str]] = defaultdict(set)
    newly_covered = old_unmapped_covered = 0
    for row in existing:
        new = confirmed_by_episode[row["episode_id"]]
        memberships = row["memberships"] + new
        for membership in memberships:
            pattern_sets[membership["pattern_id"]].add(row["episode_id"])
        for membership in new:
            new_reviews[membership["pattern_id"]].add(row["review_id"])
            new_papers[membership["pattern_id"]].add(row["paper_id"])
        newly_covered += bool(new) and not row["memberships"]
        old_unmapped_covered += bool(new) and row["unmapped_logic"] is not None
        combined.append({
            "episode_id": row["episode_id"], "paper_id": row["paper_id"],
            "review_id": row["review_id"],
            "pattern_ids": [item["pattern_id"] for item in memberships],
            "memberships": memberships,
            "new_consensus_memberships": new,
            "uncertain_new_cards": uncertain_by_episode[row["episode_id"]],
            "prior_unmapped_logic": row["unmapped_logic"],
            "needs_source_audit": row["needs_source_audit"] or any(item["needs_source_audit"] for item in uncertain_by_episode[row["episode_id"]]),
        })
    (ROOT / "atlas-13-consensus-v2-membership.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined)
    )
    all_ids = [f"A-P{i:02d}" for i in range(1, 11)] + [f"N-P{i:02d}" for i in range(1, 4)]
    overlaps = []
    for left, right in combinations(all_ids, 2):
        both = pattern_sets[left] & pattern_sets[right]
        if both:
            overlaps.append({"left": left, "right": right, "episode_count": len(both)})
    final_counts = Counter(row["final_verdict"] for row in decisions)
    first_counts = Counter(row["first_pass_verdict"] for row in decisions)
    second_counts = Counter(row["second_pass_verdict"] for row in decisions if row["second_pass_verdict"])
    patterns = {
        card: {
            "episode_count": len(pattern_sets[card]),
            "review_count": len(new_reviews[card]),
            "paper_count": len(new_papers[card]),
        }
        for card in ("N-P01", "N-P02", "N-P03")
    }
    summary = {
        "scope": "ICLR 2026 1,000-review discovery sample",
        "population_prevalence_permitted": False,
        "atlas_status": "conservative candidate extension; N-P02 remains provisional",
        "episode_count": len(combined),
        "candidate_pair_count": len(decisions),
        "first_pass_verdict_counts": dict(first_counts),
        "confirmation_pass_verdict_counts": dict(second_counts),
        "final_verdict_counts": dict(final_counts),
        "confirmed_new_membership_count": sum(len(value) for value in confirmed_by_episode.values()),
        "episodes_with_confirmed_new_membership": sum(bool(value) for value in confirmed_by_episode.values()),
        "episodes_newly_covered": newly_covered,
        "old_unmapped_covered": old_unmapped_covered,
        "uncertain_new_pair_count": final_counts["uncertain"],
        "patterns": patterns,
        "new_existing_overlaps": [row for row in overlaps if row["left"].startswith("A-") and row["right"].startswith("N-")],
    }
    (ROOT / "atlas-13-consensus-v2-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Conservative candidate 13-card Atlas — v2 consensus", "",
        "> Discovery sample only; population prevalence is not permitted.", "",
        f"- Candidate pairs re-evaluated: {summary['candidate_pair_count']}",
        f"- First-pass confirmed: {summary['first_pass_verdict_counts'].get('confirmed', 0)}",
        f"- Confirmed by an independent second pass: {summary['confirmed_new_membership_count']}",
        f"- Final uncertain pairs: {summary['uncertain_new_pair_count']}",
        f"- Episodes newly covered: {summary['episodes_newly_covered']}",
        f"- Prior unmapped episodes covered: {summary['old_unmapped_covered']}",
        "", "| Card | Confirmed episodes | Reviews | Papers |", "|---|---:|---:|---:|",
    ]
    for card, values in patterns.items():
        lines.append(f"| {card} | {values['episode_count']} | {values['review_count']} | {values['paper_count']} |")
    lines.extend([
        "", "N-P01 and N-P03 retain repeated but narrow support. N-P02 has only two two-pass-confirmed cases and remains provisional rather than a settled production card.", "",
    ])
    (ROOT / "atlas-13-consensus-v2-report.md").write_text("\n".join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
