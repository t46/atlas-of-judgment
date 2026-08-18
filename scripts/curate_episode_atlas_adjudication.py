"""Apply the reviewed cross-pattern boundary synthesis to a valid adjudication.

The global agent produced sound per-pattern decisions but repeatedly generated
mechanical boundary prose. This deterministic curation preserves those
decisions and replaces only the cross-pattern boundary list with contrasts
grounded in the independent challenge outputs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-deep-63/pattern-challenges")
DEFAULT_SOURCE = (
    DEFAULT_DIRECTORY
    / "adjudication-backup-before-boundary-specificity-guard-20260816"
    / "atlas-adjudication.json"
)


BOUNDARIES: list[dict[str, Any]] = [
    {
        "left_pattern_id": "A-P01",
        "right_pattern_id": "A-P04",
        "distinction": (
            "A matched comparison belongs to A-P01 when its endpoint is causal "
            "credit for a component or mechanism; it belongs to A-P04 when its "
            "endpoint is distinctiveness or contribution value against prior work."
        ),
        "decisive_deep_fields": [
            "standards", "comparisons", "counterfactuals", "inference_steps"
        ],
        "episode_ids": [
            "E-Szh0ELyQxL-pdk5mFmAcf-02", "E-NoZgrya6Ua-9s2FsxazuF-01"
        ],
    },
    {
        "left_pattern_id": "A-P01",
        "right_pattern_id": "A-P08",
        "distinction": (
            "An ablation instantiates A-P01 when it isolates which component caused "
            "the gain. The same experimental surface instantiates A-P08 when it "
            "merely completes the evidence package needed for a bounded local claim."
        ),
        "decisive_deep_fields": [
            "standards", "alternative_explanations", "counterfactuals",
            "expected_information_gain"
        ],
        "episode_ids": [
            "E-Szh0ELyQxL-pdk5mFmAcf-02", "E-lKqE7UuMvp-Nc3laCbaw0-03"
        ],
    },
    {
        "left_pattern_id": "A-P02",
        "right_pattern_id": "A-P03",
        "distinction": (
            "A-P02 asks whether missing exposition or provenance prevents an "
            "independent reader from reconstructing the work. A-P03 asks whether "
            "the stated formal premises actually justify the implemented operation, "
            "even when every procedural detail is available."
        ),
        "decisive_deep_fields": [
            "focal_factors", "standards", "assumptions", "inference_steps"
        ],
        "episode_ids": [
            "E-NoZgrya6Ua-GFYMdZgVy0-03", "E-FnaDv6SMd9-p15KyHz8wc-02"
        ],
    },
    {
        "left_pattern_id": "A-P02",
        "right_pattern_id": "A-P06",
        "distinction": (
            "A-P02 repairs access to a procedure, dependency, or provenance trail. "
            "A-P06 instead tests whether an accessible score, benchmark, proxy, or "
            "reference actually represents the construct invoked by the claim."
        ),
        "decisive_deep_fields": [
            "focal_factors", "standards", "comparisons", "repair_conditions"
        ],
        "episode_ids": [
            "E-FdkPOHlChS-ZJ2p9nTWEZ-02", "E-PZ8XoPXnDC-Zhux30bDg0-03"
        ],
    },
    {
        "left_pattern_id": "A-P03",
        "right_pattern_id": "A-P05",
        "distinction": (
            "A-P03 follows a premise-to-operation bridge inside the claimed formal "
            "regime. A-P05 changes the regime—such as prompt, domain, perturbation, "
            "or deployment pipeline—to test whether the empirical claim transfers."
        ),
        "decisive_deep_fields": [
            "standards", "assumptions", "counterfactuals", "inference_steps"
        ],
        "episode_ids": [
            "E-FnaDv6SMd9-p15KyHz8wc-02", "E-79SSF3ppjS-VxJxTVeMS8-04"
        ],
    },
    {
        "left_pattern_id": "A-P04",
        "right_pattern_id": "A-P08",
        "distinction": (
            "A baseline comparison supports A-P04 when it establishes novelty, fair "
            "positioning, or contribution value. It supports A-P08 when it is the "
            "missing control required to make a specific empirical inference credible."
        ),
        "decisive_deep_fields": [
            "standards", "comparisons", "alternative_explanations",
            "expected_information_gain"
        ],
        "episode_ids": [
            "E-NoZgrya6Ua-9s2FsxazuF-01", "E-lKqE7UuMvp-Nc3laCbaw0-03"
        ],
    },
    {
        "left_pattern_id": "A-P05",
        "right_pattern_id": "A-P07",
        "distinction": (
            "A-P05 asks whether the scientific claim survives a changed target "
            "regime. A-P07 asks whether the method remains usable within a resource, "
            "dependency, stability, or end-to-end execution envelope; failure can "
            "occur without refuting transfer."
        ),
        "decisive_deep_fields": [
            "focal_factors", "standards", "assumptions", "repair_conditions"
        ],
        "episode_ids": [
            "E-lKqE7UuMvp-Nc3laCbaw0-02", "E-DgnsohAUMn-PBV6wMlEyh-02"
        ],
    },
    {
        "left_pattern_id": "A-P06",
        "right_pattern_id": "A-P08",
        "distinction": (
            "A-P06 asks what a metric, proxy, benchmark, or label measures. A-P08 "
            "accepts that operationalization provisionally and asks whether enough "
            "claim-specific comparisons or integrity checks were supplied."
        ),
        "decisive_deep_fields": [
            "standards", "comparisons", "assumptions", "inference_steps"
        ],
        "episode_ids": [
            "E-PZ8XoPXnDC-Zhux30bDg0-03", "E-lKqE7UuMvp-Nc3laCbaw0-03"
        ],
    },
    {
        "left_pattern_id": "A-P08",
        "right_pattern_id": "A-P10",
        "distinction": (
            "A-P08 ends when the empirical package becomes sufficient for the local "
            "scientific claim. A-P10 adds a downstream endpoint: whether residual "
            "uncertainty and practical conditions warrant a concrete decision, action, "
            "or deployment conclusion."
        ),
        "decisive_deep_fields": [
            "standards", "comparisons", "inference_steps", "repair_conditions"
        ],
        "episode_ids": ["E-uhv3f80jmG-jd0yD2KP7W-01"],
    },
    {
        "left_pattern_id": "A-P09",
        "right_pattern_id": "A-P10",
        "distinction": (
            "A-P09 grants bounded positive credit because motivation, design, evidence, "
            "and result cohere despite an explicit reservation. A-P10 is narrower: "
            "the unresolved condition must change whether a downstream action or "
            "deployment is warranted."
        ),
        "decisive_deep_fields": [
            "standards", "assumptions", "inference_steps", "repair_conditions"
        ],
        "episode_ids": [
            "E-SzXDuBN8M1-8iQzosHaWT-02", "E-uhv3f80jmG-jd0yD2KP7W-01"
        ],
    },
]


def render_report(payload: dict[str, Any]) -> str:
    decisions = payload["pattern_decisions"]
    names = {row["pattern_id"]: row["recommended_name"] for row in decisions}
    retained = sum(row["decision"] == "retain" for row in decisions)
    revised = sum(row["decision"] == "revise" for row in decisions)
    lines = [
        "# Evaluation Logic Atlas: selected-Deep adjudication",
        "",
        "## Result",
        "",
        (
            f"The global disposition is `{payload['atlas_disposition']}`: all ten "
            f"operative cores survive, with {retained} retained and {revised} requiring "
            "targeted rule or membership revision. No selected evidence supports a "
            "split, merge, or retirement. Selected cases test boundaries; they do not "
            "estimate prevalence or outcome effects."
        ),
        "",
        "## What the Deep evidence says reviewers do",
        "",
        (
            "Reviewers connect a focal object and observation to an operative standard, "
            "then test assumptions and alternatives through a comparison, counterfactual, "
            "or requested repair. What distinguishes apparently similar reviews is the "
            "inference endpoint: the exact proposition the evidence is meant to establish. "
            "Thus an ablation can assign causal credit, complete an empirical package, or "
            "calibrate a downstream decision without expressing the same evaluation logic."
        ),
        "",
        "## Adjudicated cards",
        "",
        "| ID | Operative standard | Decision | Accepted | Disputed | Removed |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in decisions:
        lines.append(
            f"| {row['pattern_id']} | {row['recommended_name']} | "
            f"{row['decision']} | {len(row['accepted_candidate_episode_ids'])} | "
            f"{len(row['disputed_candidate_episode_ids'])} | "
            f"{len(row['removed_candidate_episode_ids'])} |"
        )
    lines.extend(["", "## Decisive cross-pattern boundaries", ""])
    for boundary in payload["cross_pattern_boundaries"]:
        left = boundary["left_pattern_id"]
        right = boundary["right_pattern_id"]
        episodes = ", ".join(f"`{item}`" for item in boundary["episode_ids"])
        lines.append(
            f"- **{left} ({names[left]}) vs {right} ({names[right]})**: "
            f"{boundary['distinction']} Evidence: {episodes}."
        )
    lines.extend(
        [
            "",
            "## Limits",
            "",
            *[f"- {limit}" for limit in payload["limits"]],
            "",
            "## Next step",
            "",
            payload["recommended_next_step"],
            "",
        ]
    )
    return "\n".join(lines)


def curate(directory: Path, source: Path) -> dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    manifest = json.loads(
        (directory / "atlas-adjudication-manifest.json").read_text(encoding="utf-8")
    )
    candidates = {
        key: set(value)
        for key, value in manifest["candidate_episode_ids_by_pattern"].items()
    }
    for boundary in BOUNDARIES:
        left = boundary["left_pattern_id"]
        right = boundary["right_pattern_id"]
        allowed = candidates[left] | candidates[right]
        unknown = set(boundary["episode_ids"]) - allowed
        if unknown:
            raise ValueError(f"{left}/{right} contains noncandidate IDs: {sorted(unknown)}")
    payload["cross_pattern_boundaries"] = BOUNDARIES
    (directory / "atlas-adjudication.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (directory / "atlas-adjudication-report.md").write_text(
        render_report(payload), encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    result = curate(args.directory, args.source)
    print(
        json.dumps(
            {
                "patterns": len(result["pattern_decisions"]),
                "boundaries": len(result["cross_pattern_boundaries"]),
            }
        )
    )


if __name__ == "__main__":
    main()
