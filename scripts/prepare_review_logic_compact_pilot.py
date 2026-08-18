"""Prepare three blinded compact-logic prompt-tuning scenarios."""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path


DEFAULT_SOURCE = Path("data/analysis/iclr/episode-lite-2026-full-v3")
DEFAULT_OUTPUT = Path("data/analysis/iclr/review-logic-compact-pilot")
SCENARIO_SETS = {
    "baseline": {"low-density": 10, "median-density": 4, "high-density": 17},
    "holdout": {"holdout-a": 19, "holdout-b": 11, "holdout-c": 8},
}


def protocol() -> str:
    return """# Compact reviewer-logic extraction protocol v0.3

Goal: retain what a human reviewer inspected, observed, used as an evaluative
standard or inference, judged, and suggested—without reproducing a long memo or
assigning a taxonomy.

For every review, emit one compact JSON record with one or more `logic_units`.
A unit is one independently warranted observation -> reasoning -> judgment
chain. There is no expected or preferred number of units per review.

Determine unit boundaries in two passes before writing JSON:

1. Coverage pass: inventory every paper-specific evaluative endpoint supported
   by the memo. Check concrete strengths, weaknesses, questions, comparisons,
   scope or robustness concerns, mechanism challenges, presentation barriers,
   and requested changes. A question counts only when the memo connects it to
   a local uncertainty, condition, or judgment. Do not treat overview,
   implicit-norm, tension, and overall-logic sections as new endpoints when
   they merely restate an earlier paper-specific chain.
2. Boundary pass: merge candidates only when they concern the same inspected
   object, the same material observation, the same operative standard or
   inference, and the same local judgment. Split when a different test,
   counterfactual, comparison, or repair could resolve one endpoint without
   resolving the other. Distinct items inside one memo list remain distinct
   when their evidentiary role or judgment differs.

Use the independent-resolution test aggressively: imagine the authors fully
answering or performing one requested clarification, analysis, comparison, or
experiment. If another local uncertainty or judgment would remain, those are
different units even when they occur in the same paragraph or concern the same
broad component. In particular, do not merge (a) motivation with comparative
evidence, (b) statistical repetition with reporting interpretation, (c)
generalization risk with an external reference point, (d) literature placement
with rate or claim tightness, or (e) scope extension with parameter usability
unless one reviewer-supported response genuinely resolves both endpoints.

Do not impose a quota such as 1, 3, 4, or 6 units. A short review may support
one chain; a dense review may support many. Compactness comes from merging
duplicate restatements and concise field text, not from dropping endpoints.

Field rules:

- `inspected_object`: the concrete claim, method component, comparison,
  experiment, proof step, presentation object, or other object inspected.
- `observation`: what the reviewer found, accepted, questioned, or found absent.
- `reasoning`: why that observation supports the judgment; preserve the
  operative standard, comparator, assumption, counterfactual, or inference.
- `judgment`: the resulting local evaluation, not the paper decision or score.
- `suggested_improvement`: only a reviewer-supported repair, clarification,
  test, comparison, or change; otherwise null.
- `evidence_refs`: valid `R-<review_id>:L###` references quoted by the memo.
  If the memo supplies no valid primary ref, cite the packet wrapper
  `I-<review_id>:L###` and record `primary_provenance` in `missing_links`.
  Each array item is one atomic line reference. Never emit a line range such as
  `L017-L025`; cite one or more individual lines instead.
- `support_status`: reviewer_explicit only when primary evidence supports the
  claim; memo_inferred for memo interpretation; mixed when both are involved.
- `review_logic_summary`: 1–3 sentences describing the reviewer's dominant
  evaluative activity without referring to score, rating, acceptance, or an
  Atlas category.
- `unresolved_tensions`: contradictions or missing bridges in the reviewer's
  own logic. Empty is valid.

Use IDs `U-<review_id>-NN` in review-local order. Preserve positive as well as
negative logic. Questions are not automatically negative judgments. Do not
invent a repair from general knowledge. Do not collapse separate strengths,
comparisons, mechanism challenges, scope concerns, and presentation barriers.

Before emitting a review record, perform a silent coverage check: every
paper-specific strength, weakness, evaluative question, and requested change
in the memo must either map to a unit or be a duplicate restatement of a mapped
unit. For each distinct requested action or answer, identify the unit it would
resolve; if it would resolve none, an endpoint is missing. Never output this
scratch inventory.

Validation repair must not reduce semantic coverage. If a validator reports a
generic phrase, malformed reference, or other local defect, rewrite that field
or reference in place. Never delete units, merge independent endpoints, or
replace review-specific content with placeholders merely to silence a warning.
Phrases such as "apply the requested clarification", "the memo identifies an
issue", or "this matters for the paper" are not compact records; they discard
the reviewer-specific object, observation, standard, or repair.

Output one JSON object per line, in source-review order. Do not emit prose in
the JSONL. Semantic fields must be authored review by review, not generated by
a keyword loop or shared template.
"""


def prepare(source: Path, output: Path, scenarios: dict[str, int]) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to replace compact pilot: {output}")
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    try:
        rows = []
        for name, shard in scenarios.items():
            suffix = f"{shard:05d}"
            source_md = source / f"source-shard-{suffix}.md"
            source_json = source / f"source-shard-{suffix}.json"
            target_md = temporary / f"source-{name}.md"
            target_json = temporary / f"source-{name}.json"
            shutil.copyfile(source_md, target_md)
            shutil.copyfile(source_json, target_json)
            metadata = json.loads(target_json.read_text(encoding="utf-8"))
            reference_path = source / f"episodes-shard-{suffix}.jsonl"
            reference_count = sum(
                bool(line.strip()) for line in reference_path.read_text(encoding="utf-8").splitlines()
            )
            rows.append({
                "scenario": name,
                "source_shard": shard,
                "review_count": len(metadata["reviews"]),
                "reference_episode_count": reference_count,
                "source_markdown": target_md.name,
                "source_metadata": target_json.name,
            })
        manifest = {
            "version": 3,
            "source": str(source.resolve()),
            "schema": str(Path("schemas/review-logic-compact-v0.1.json").resolve()),
            "model": "gpt-5.6-luna",
            "reasoning_effort": "low",
            "scenarios": rows,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (temporary / "PROTOCOL.md").write_text(protocol(), encoding="utf-8")
        temporary.replace(output)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scenario-set", choices=SCENARIO_SETS, default="baseline")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, SCENARIO_SETS[args.scenario_set]), indent=2))


if __name__ == "__main__":
    main()
