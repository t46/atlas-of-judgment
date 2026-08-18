"""Validate Episode Lite pilot outputs and report coverage/provenance metrics."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


DEFAULT_PILOT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_SCHEMA = Path("schemas/evaluation-episode-v0.2.json")
PRIMARY_REF_RE = re.compile(r"^R-(?P<review>[^:]+):L\d{3,}$")
WRAPPER_REF_RE = re.compile(r"^I-(?P<review>[^:]+):L\d{3,}$")
SOURCE_PRIMARY_REF_RE = re.compile(r"\[(R-[^:\]]+:L\d{3,})\]")
GENERIC_PHRASES = (
    "the memo identifies a focus",
    "specific evaluation or explanation gap",
    "identified technical or empirical deficiency",
    "corresponding clarification or validation",
    "resolve the specific evaluation",
    "the proposed method, its reported evidence",
    "the paper's central method",
    "the paper material relevant to this concern",
    "the concrete issue described in this episode",
    "the reviewer-inspected issue described in the memo",
    "the concrete issue described in the memo",
    "the concrete object described in the memo",
    "the concrete design and evidence described in the cited review lines",
    "this observation directly bears on the adequacy, validity, clarity, or practical usefulness",
    "the observation bears directly on the adequacy, credibility, or scope",
    "this observation creates a distinct evaluative issue",
    "this observation bears directly on the stated evaluation standard",
    "whether the paper's claim can be evaluated or supported",
    "an explicit standard implied by the observed gap",
    "the observed limitation or strength determines how well",
    "the observation creates an independently assessable evidentiary or validity concern",
    "the stated observation leads to the evaluation concern",
    "the missing or observed evidence affects whether",
    "the corresponding evaluation claim insufficiently established",
    "the issue blocks acceptance of the claim without targeted clarification",
    "the memo connects the inspected material to the stated evaluation",
    "address the specific issue documented in the cited review line",
    "address the stated issue with supporting explanation, analysis, or revision",
    "test or clarify the identified limitation",
    "provide the missing analysis, evidence, or clarification identified in the cited review lines",
    "the paper-specific method, evaluation, and claim addressed by review",
    "the reported observation is material because it limits direct assessment",
    "the requested baseline, data analysis, or claim qualification",
    "the review identifies a specific evidentiary or scope issue concerning",
    "the requested evidence is needed to determine whether",
    "supports the broader claim",
)
GENERIC_SIGNATURE_PHRASES = (
    "a reviewer connects a concrete paper feature to a judgment about evaluation adequacy",
    "a reviewer connects an inspected issue to an evaluative judgment and targeted follow-up",
    "inspection → observation → evaluative implication → targeted verification",
    "a reviewer links an observed limitation in an evaluation object",
    "a reviewer identifies a specific evidentiary, explanatory, comparative, or presentation gap",
    "a reviewer evaluates a research claim by connecting an observed evidence gap",
    "a reviewer connects an inspected property to evidence limits or a strength",
    "a reported observation may fail to establish the intended construct",
    "evidence about a distinct evaluation dimension supports a conditional judgment",
    "a concrete evaluation concern links an observation to a judgment",
    "incomplete evidence or clarification weakens an evaluation claim",
    "a claim is judged incomplete when relevant evidence is missing",
    "an observed methodological issue is connected to an evaluation judgment",
    "an observation supports a judgment through an evaluation implication",
    "a specific limitation or missing evidence weakens the corresponding claim",
    "an evaluation claim is insufficiently established by the inspected evidence",
    "connect a concrete observation to a consequential judgment",
    "a specific inspected aspect yields an observation, an inferential concern",
    "a reviewer connects inspected evidence to an evaluation judgment",
    "a stated evaluation claim remains conditional when the cited evidence or scope is incomplete",
)
GENERIC_TEXT_RE = re.compile(
    r"^(?:object \d+ inspected in the review|the reviewer inspected (?:this |the )?issue described at l\d+)\.?$",
    re.IGNORECASE,
)
WHITESPACE_RE = re.compile(r"\s+")
MAX_CROSS_REVIEW_REUSE = 2


def is_generic_claim_text(text: str) -> bool:
    normalized = text.casefold().strip()
    return any(phrase in normalized for phrase in GENERIC_PHRASES) or bool(
        GENERIC_TEXT_RE.match(normalized)
    )


def is_generic_signature(text: str) -> bool:
    normalized = text.casefold().strip()
    return any(phrase in normalized for phrase in GENERIC_SIGNATURE_PHRASES)


def normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip().casefold()


def iter_labeled_texts(episode: dict[str, Any]) -> list[tuple[str, str]]:
    chain = episode.get("chain", {})
    result: list[tuple[str, str]] = []
    for key in (
        "inspected_objects",
        "observations",
        "reasoning_bridge",
        "judgments",
        "requested_tests_or_changes",
    ):
        result.extend((key, claim.get("text", "")) for claim in chain.get(key, []))
    for key in ("concrete", "abstract"):
        result.append(
            (f"signature_{key}", episode.get("signatures", {}).get(key, ""))
        )
    return result


def find_cross_review_reuse(
    instances: list[tuple[int, str, str, str]],
    *,
    max_review_reuse: int = MAX_CROSS_REVIEW_REUSE,
) -> list[str]:
    reviews_by_text: dict[tuple[int, str, str], set[str]] = defaultdict(set)
    examples: dict[tuple[int, str, str], str] = {}
    for shard, field, text, review_id in instances:
        normalized = normalize_text(text)
        if not normalized:
            continue
        key = (shard, field, normalized)
        reviews_by_text[key].add(review_id)
        examples[key] = text

    warnings = []
    for key, review_ids in sorted(reviews_by_text.items()):
        if len(review_ids) <= max_review_reuse:
            continue
        shard, field, _ = key
        warnings.append(
            f"shard {shard}: exact {field} text reused across "
            f"{len(review_ids)} reviews: {examples[key]}"
        )
    return warnings


def iter_claims(episode: dict[str, Any]) -> list[dict[str, Any]]:
    chain = episode.get("chain", {})
    claims: list[dict[str, Any]] = []
    for key in ("inspected_objects", "observations", "reasoning_bridge"):
        claims.extend(chain.get(key, []))
    claims.extend(chain.get("judgments", []))
    claims.extend(chain.get("requested_tests_or_changes", []))
    return claims


def load_episodes(path: Path) -> list[dict[str, Any]]:
    episodes = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            episodes.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return episodes


def validate(
    pilot_dir: Path,
    schema_path: Path,
    *,
    only_shards: set[int] | None = None,
) -> dict[str, Any]:
    manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    target_shards = only_shards or set(range(1, manifest["shard_count"] + 1))
    selected = {
        item["candidate"]["review_id"]: item
        for item in manifest["reviews"]
        if item["shard"] in target_shards
    }

    errors: list[str] = []
    warnings: list[str] = []
    episodes: list[dict[str, Any]] = []
    coverage_rows: dict[str, dict[str, Any]] = {}
    episode_ids: set[str] = set()
    provenance = Counter()
    status_counts = Counter()
    text_instances: list[tuple[int, str, str, str]] = []
    database = Path(manifest["database"])
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro&immutable=1", uri=True
    )
    valid_primary_refs: dict[str, set[str]] = {}
    valid_wrapper_refs: dict[str, set[str]] = {}
    try:
        for review_id in selected:
            row = connection.execute(
                """
                SELECT j.user_prompt, m.memo
                FROM jobs AS j JOIN memos AS m USING(job_id)
                WHERE j.job_id=?
                """,
                (f"initial:{review_id}",),
            ).fetchone()
            if row is None:
                errors.append(f"source job missing for {review_id}")
                valid_primary_refs[review_id] = set()
                valid_wrapper_refs[review_id] = set()
                continue
            valid_primary_refs[review_id] = set(SOURCE_PRIMARY_REF_RE.findall(row[0]))
            valid_wrapper_refs[review_id] = {
                f"I-{review_id}:L{index:03d}"
                for index, _ in enumerate(row[1].splitlines(), 1)
            }
    finally:
        connection.close()

    for shard in sorted(target_shards):
        if shard < 1 or shard > manifest["shard_count"]:
            errors.append(f"invalid requested shard: {shard}")
            continue
        episode_path = pilot_dir / f"episodes-shard-{shard:02d}.jsonl"
        coverage_path = pilot_dir / f"coverage-shard-{shard:02d}.json"
        pattern_path = pilot_dir / f"patterns-shard-{shard:02d}.md"
        for required_path in (episode_path, coverage_path, pattern_path):
            if not required_path.exists():
                errors.append(f"missing output: {required_path}")
        if not episode_path.exists() or not coverage_path.exists():
            continue

        shard_episodes = load_episodes(episode_path)
        episodes.extend(shard_episodes)
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        if coverage.get("shard") != shard:
            errors.append(f"{coverage_path}: shard number mismatch")
        for row in coverage.get("reviews", []):
            review_id = row.get("review_id")
            if review_id in coverage_rows:
                errors.append(f"duplicate coverage review: {review_id}")
            coverage_rows[review_id] = row
            status_counts[row.get("status", "missing")] += 1
            for required_key in (
                "episode_count",
                "status",
                "review_is_substantive",
                "zero_episode_reason",
                "provenance_failure",
                "notes",
            ):
                if required_key not in row:
                    errors.append(
                        f"{coverage_path}: {review_id} missing {required_key}"
                    )

        for index, episode in enumerate(shard_episodes, 1):
            prefix = f"{episode_path}:record {index}"
            for error in validator.iter_errors(episode):
                location = "/".join(map(str, error.absolute_path))
                errors.append(f"{prefix}:{location}: {error.message}")
            episode_id = episode.get("episode_id")
            if episode_id in episode_ids:
                errors.append(f"{prefix}: duplicate episode_id {episode_id}")
            episode_ids.add(episode_id)
            if episode.get("enrichment_level") != "lite":
                errors.append(f"{prefix}: enrichment_level must be lite")

            source = episode.get("source", {})
            review_id = source.get("review_id")
            paper_id = source.get("paper_id")
            expected_episode_prefix = f"E-{paper_id}-{review_id}-"
            if (
                not isinstance(episode_id, str)
                or not episode_id.startswith(expected_episode_prefix)
                or re.fullmatch(r"\d{2}", episode_id[len(expected_episode_prefix) :])
                is None
            ):
                errors.append(
                    f"{prefix}: noncanonical episode_id {episode_id!r}; "
                    f"expected {expected_episode_prefix}NN with a two-digit suffix"
                )
            if review_id not in selected:
                errors.append(f"{prefix}: review not in manifest: {review_id}")
                continue
            if selected[review_id]["shard"] != shard:
                errors.append(f"{prefix}: review belongs to another shard: {review_id}")
            if paper_id != selected[review_id]["candidate"]["paper_id"]:
                errors.append(f"{prefix}: paper_id mismatch for {review_id}")

            evidence = episode.get("evidence", {})
            evidence_levels = {
                key: value.get("provenance_level") for key, value in evidence.items()
            }
            for claim in iter_claims(episode):
                claim_text = claim.get("text", "")
                if is_generic_claim_text(claim_text):
                    warnings.append(f"{prefix}: generic placeholder claim: {claim_text}")
                for evidence_key in claim.get("evidence_refs", []):
                    if evidence_key not in evidence:
                        errors.append(
                            f"{prefix}: dangling evidence key {evidence_key}"
                        )
                if (
                    claim.get("status") == "reviewer_explicit"
                    and claim.get("evidence_refs")
                    and not any(
                        evidence_levels.get(key) == "primary"
                        for key in claim["evidence_refs"]
                    )
                ):
                    warnings.append(
                        f"{prefix}: reviewer_explicit claim lacks primary evidence"
                    )
            for evidence_key, source_ref in evidence.items():
                level = source_ref.get("provenance_level")
                ref = source_ref.get("ref", "")
                provenance[level] += 1
                pattern = PRIMARY_REF_RE if level == "primary" else WRAPPER_REF_RE
                if level in {"primary", "analytic_wrapper"}:
                    match = pattern.match(ref)
                    if not match or match.group("review") != review_id:
                        warnings.append(
                            f"{prefix}: noncanonical {level} ref {evidence_key}={ref}"
                        )
                    elif level == "primary" and ref not in valid_primary_refs[review_id]:
                        errors.append(f"{prefix}: primary ref does not exist: {ref}")
                    elif (
                        level == "analytic_wrapper"
                        and ref not in valid_wrapper_refs[review_id]
                    ):
                        errors.append(f"{prefix}: wrapper ref does not exist: {ref}")
            notes = episode.get("quality", {}).get("notes", [])
            if not notes or not notes[0].startswith("Boundary rationale:"):
                warnings.append(f"{prefix}: missing boundary rationale")
            if (
                not any(level == "primary" for level in evidence_levels.values())
                and "primary_provenance"
                not in episode.get("quality", {}).get("missing_links", [])
            ):
                warnings.append(
                    f"{prefix}: no primary evidence and missing_links does not declare it"
                )
            for signature_name in ("concrete", "abstract"):
                signature = episode.get("signatures", {}).get(signature_name, "")
                if is_generic_signature(signature):
                    warnings.append(
                        f"{prefix}: generic {signature_name} signature: {signature}"
                    )
            for field, value in iter_labeled_texts(episode):
                text_instances.append((shard, field, value, review_id))

    warnings.extend(find_cross_review_reuse(text_instances))

    episode_counts = Counter(episode["source"]["review_id"] for episode in episodes)
    for review_id, item in selected.items():
        row = coverage_rows.get(review_id)
        if row is None:
            errors.append(f"missing coverage row for {review_id}")
            continue
        actual = episode_counts[review_id]
        if row.get("episode_count") != actual:
            errors.append(
                f"coverage count mismatch for {review_id}: "
                f"declared={row.get('episode_count')} actual={actual}"
            )
        expected_status = "zero" if actual == 0 else "complete"
        if row.get("status") not in {expected_status, "error"}:
            errors.append(
                f"coverage status mismatch for {review_id}: {row.get('status')}"
            )

    bucket_episode_counts: dict[str, int] = defaultdict(int)
    for episode in episodes:
        review_id = episode["source"]["review_id"]
        bucket = selected[review_id]["candidate"]["decision_bucket"]
        bucket_episode_counts[bucket] += 1
    result = {
        "selected_reviews": len(selected),
        "covered_reviews": len(coverage_rows),
        "episodes": len(episodes),
        "reviews_with_zero_episodes": sum(
            1 for review_id in selected if episode_counts[review_id] == 0
        ),
        "episodes_per_review_mean": len(episodes) / len(selected) if selected else 0,
        "episodes_by_decision_bucket": dict(sorted(bucket_episode_counts.items())),
        "coverage_status": dict(status_counts),
        "provenance": dict(provenance),
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": warnings,
        "errors": errors,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--only-shard",
        type=int,
        action="append",
        help="Validate only this shard; repeat for an incremental batch.",
    )
    args = parser.parse_args()
    result = validate(
        args.pilot_dir,
        args.schema,
        only_shards=set(args.only_shard) if args.only_shard else None,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(1 if result["error_count"] or result["warning_count"] else 0)


if __name__ == "__main__":
    main()
