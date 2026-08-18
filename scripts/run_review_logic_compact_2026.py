"""Durable full-corpus ICLR 2026 compact reviewer-logic extraction."""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

try:
    from scripts.prepare_review_logic_compact_pilot import protocol as compact_protocol
    from scripts.supervise_review_logic_compact_direct import (
        BATCH_SCHEMA,
        CODEX,
        normalize_atomic_refs,
        write_report,
    )
    from scripts.validate_review_logic_compact_pilot import (
        GENERIC,
        PRIMARY_REF,
        SCORE_LEAKAGE,
        WRAPPER_REF,
    )
except ModuleNotFoundError:  # direct `python scripts/...` execution
    from prepare_review_logic_compact_pilot import protocol as compact_protocol
    from supervise_review_logic_compact_direct import (
        BATCH_SCHEMA,
        CODEX,
        normalize_atomic_refs,
        write_report,
    )
    from validate_review_logic_compact_pilot import (
        GENERIC,
        PRIMARY_REF,
        SCORE_LEAKAGE,
        WRAPPER_REF,
    )


DEFAULT_SOURCE = Path("data/analysis/iclr/episode-lite-2026-full-v3")
DEFAULT_OUTPUT = Path("data/analysis/iclr/review-logic-compact-2026")
RECORD_SCHEMA = Path("schemas/review-logic-compact-v0.1.json")
SOURCE_REF = re.compile(r"\[([RI]-[^:\]]+:L\d{3,})\]")


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize(source: Path, output: Path) -> tuple[dict, sqlite3.Connection]:
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    for name in ("outputs", "raw", "reports", "validations", "logs"):
        (output / name).mkdir(exist_ok=True)
    protocol = compact_protocol()
    protocol_path = output / "PROTOCOL.md"
    if not protocol_path.exists():
        protocol_path.write_text(protocol, encoding="utf-8")
    elif protocol_path.read_text(encoding="utf-8") != protocol:
        raise RuntimeError("existing PROTOCOL.md differs from the current extraction protocol")
    manifest_path = output / "manifest.json"
    expected_manifest = {
        "version": 1,
        "scope": "ICLR 2026 completed initial_blind reviewer-activity memos",
        "source": str(source.resolve()),
        "source_manifest": str((source / "manifest.json").resolve()),
        "record_schema": str(RECORD_SCHEMA.resolve()),
        "batch_schema": str(BATCH_SCHEMA.resolve()),
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "review_count": source_manifest["review_count"],
        "shard_count": source_manifest["shard_count"],
        "outcome_blind": True,
        "taxonomy_free": True,
        "source_manifest_sha256": digest(source / "manifest.json"),
        "protocol_sha256": hashlib.sha256(protocol.encode("utf-8")).hexdigest(),
        "record_schema_sha256": digest(RECORD_SCHEMA),
        "batch_schema_sha256": digest(BATCH_SCHEMA),
        "created_at": now(),
    }
    if not manifest_path.exists():
        manifest = expected_manifest
        atomic_json(manifest_path, manifest)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        immutable_keys = (
            "source", "review_count", "shard_count", "model", "reasoning_effort",
            "source_manifest_sha256", "protocol_sha256", "record_schema_sha256",
            "batch_schema_sha256",
        )
        changed = False
        for key in immutable_keys:
            if key not in manifest:
                manifest[key] = expected_manifest[key]
                changed = True
            elif manifest[key] != expected_manifest[key]:
                raise RuntimeError(
                    f"existing run identity mismatch for {key}: "
                    f"{manifest[key]!r} != {expected_manifest[key]!r}"
                )
        if changed:
            atomic_json(manifest_path, manifest)
    connection = sqlite3.connect(output / "state.sqlite3", timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shards (
            shard INTEGER PRIMARY KEY,
            review_count INTEGER NOT NULL,
            memo_chars INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            elapsed_seconds REAL,
            agent_return_code INTEGER,
            validation_error_count INTEGER,
            error TEXT
        )
    """)
    connection.executemany(
        "INSERT OR IGNORE INTO shards(shard,review_count,memo_chars) VALUES(?,?,?)",
        [(row["shard"], row["review_count"], row["memo_chars"])
         for row in source_manifest["shards"]],
    )
    connection.commit()
    return manifest, connection


def build_prompt(protocol: str, source_text: str, metadata_text: str) -> str:
    first_review = source_text.find("## Review 01:")
    if first_review >= 0:
        source_text = source_text[first_review:]
    return f"""You are a fresh, independent executor. Do not use tools. Read the complete
protocol and source packet below, then return only the JSON object required by
the supplied output schema.

Requirements checklist fixed before execution:
1. [critical] Exactly one schema-valid record per source review, in source order.
2. [critical] Preserve every independently warranted inspected-object ->
   observation -> reasoning/standard -> judgment chain; do not collapse or use
   an implicit unit quota.
3. [critical] Every unit uses valid review-specific primary or wrapper evidence.
   A valid wrapper reference fully satisfies this requirement when primary text
   is absent. Every evidence_refs item must be one atomic
   `R-<review_id>:L###` or `I-<review_id>:L###` reference, never a line range.
4. Preserve positive, negative, conditional, and uncertain reasoning without
   inferring paper outcome or score. Never mention a score, rating,
   recommendation, acceptance, rejection, or decision in any semantic field,
   even if the memo mentions one.
5. Suggested improvements are reviewer-supported or null, never invented.
6. Text is review-specific, compact, and not produced by a shared template.

Populate self_report honestly after extracting all records. Items 1 through 6
must appear once each in order. Primary-text absence alone does not make item 3
partial when wrapper evidence is valid. Return exactly the number of records
listed in source_metadata; never add a second record for the same review.

<protocol>
{protocol}
</protocol>

<source_metadata>
{metadata_text}
</source_metadata>

<source_packet>
{source_text}
</source_packet>
"""


def validate_payload(
    payload: dict,
    metadata: dict,
    source_text: str,
    valid_primary: dict[str, set[str]],
) -> list[str]:
    errors: list[str] = []
    schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    records = payload.get("records", [])
    expected = metadata["reviews"]
    expected_ids = [row["review_id"] for row in expected]
    expected_papers = {row["review_id"]: row["paper_id"] for row in expected}
    if [row.get("review_id") for row in records] != expected_ids:
        errors.append("review coverage or source order differs")
    valid_wrapper = {ref for ref in SOURCE_REF.findall(source_text) if ref.startswith("I-")}
    for row_index, record in enumerate(records, 1):
        review_id = record.get("review_id")
        prefix = f"row {row_index}:{review_id}"
        for error in validator.iter_errors(record):
            errors.append(f"{prefix}:{'/'.join(map(str, error.absolute_path))}: {error.message}")
        if review_id not in expected_papers:
            continue
        if record.get("paper_id") != expected_papers[review_id]:
            errors.append(f"{prefix}: paper_id mismatch")
        if SCORE_LEAKAGE.search(record.get("review_logic_summary", "")):
            errors.append(f"{prefix}: summary leaks score/decision")
        units = record.get("logic_units", [])
        expected_units = [f"U-{review_id}-{index:02d}" for index in range(1, len(units) + 1)]
        if [unit.get("unit_id") for unit in units] != expected_units:
            errors.append(f"{prefix}: unit IDs/order differ")
        for unit in units:
            unit_id = unit.get("unit_id")
            for field in ("inspected_object", "observation", "reasoning", "judgment"):
                value = " ".join(str(unit.get(field, "")).casefold().split())
                if any(phrase in value for phrase in GENERIC):
                    errors.append(f"{prefix}:{unit_id}: generic {field}")
            refs = unit.get("evidence_refs", [])
            if not refs:
                errors.append(f"{prefix}:{unit_id}: no evidence refs")
            for ref in refs:
                primary = PRIMARY_REF.fullmatch(ref)
                wrapper = WRAPPER_REF.fullmatch(ref)
                match = primary or wrapper
                valid = bool(
                    match and match.group(1) == review_id and (
                        (primary and ref in valid_primary.get(review_id, set()))
                        or (wrapper and ref in valid_wrapper)
                    )
                )
                if not valid:
                    errors.append(f"{prefix}:{unit_id}: invalid evidence ref {ref}")
            if unit.get("support_status") == "reviewer_explicit" and not any(
                PRIMARY_REF.fullmatch(ref) for ref in refs
            ):
                errors.append(f"{prefix}:{unit_id}: reviewer_explicit lacks primary ref")
    report = payload.get("self_report", {})
    requirements = report.get("requirements", [])
    if [row.get("item") for row in requirements] != list(range(1, 7)):
        errors.append("self-report requirement order differs")
    if any(row.get("status") != "pass" for row in requirements):
        errors.append("self-report declares one or more requirements partial or failed")
    return errors


def normalize_provenance(
    payload: dict,
    valid_primary: dict[str, set[str]],
    valid_wrapper: set[str],
) -> None:
    """Repair mechanical R/I prefix confusion without changing semantic evidence."""
    for record in payload.get("records", []):
        review_id = record.get("review_id", "")
        for unit in record.get("logic_units", []):
            normalized: list[str] = []
            for ref in unit.get("evidence_refs", []):
                candidate = ref
                if ref.startswith(f"R-{review_id}:") and ref not in valid_primary.get(review_id, set()):
                    wrapper = "I-" + ref[2:]
                    if wrapper in valid_wrapper:
                        candidate = wrapper
                if candidate not in normalized:
                    normalized.append(candidate)
            unit["evidence_refs"] = normalized
            has_valid_primary = any(
                ref in valid_primary.get(review_id, set()) for ref in normalized
            )
            if unit.get("support_status") == "reviewer_explicit" and not has_valid_primary:
                unit["support_status"] = "memo_inferred"
                missing = unit.setdefault("missing_links", [])
                if "primary_provenance" not in missing:
                    missing.append("primary_provenance")


def run_shard(
    repo: Path,
    source: Path,
    output: Path,
    database: Path,
    shard: int,
    protocol: str,
) -> dict:
    suffix = f"{shard:05d}"
    source_path = source / f"source-shard-{suffix}.md"
    metadata_path = source / f"source-shard-{suffix}.json"
    source_text = source_path.read_text(encoding="utf-8")
    metadata_text = metadata_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    valid_primary: dict[str, set[str]] = {}
    database_connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro&immutable=1", uri=True
    )
    try:
        for review in metadata["reviews"]:
            review_id = review["review_id"]
            row = database_connection.execute(
                "SELECT user_prompt FROM jobs WHERE job_id=?", (f"initial:{review_id}",)
            ).fetchone()
            valid_primary[review_id] = {
                ref for ref in SOURCE_REF.findall(row[0]) if ref.startswith("R-")
            } if row else set()
    finally:
        database_connection.close()
    raw_output = output / "raw" / f"shard-{suffix}.json"
    attempt_raw = output / "raw" / f".shard-{suffix}.{uuid.uuid4().hex}.attempt.json"
    log = output / "logs" / f"shard-{suffix}.log"
    command = [
        str(CODEX), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"',
        "--output-schema", str((repo / BATCH_SCHEMA).resolve()),
        "--output-last-message", str(attempt_raw.resolve()), "--cd", str(repo), "-",
    ]
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(
                command, cwd=repo, input=build_prompt(protocol, source_text, metadata_text),
                text=True, stdout=stream, stderr=subprocess.STDOUT,
                timeout=20 * 60, check=False,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            if attempt_raw.exists():
                attempt_raw.replace(
                    output / "raw" / f"failed-shard-{suffix}-{uuid.uuid4().hex}.json"
                )
            return {"shard": shard, "ok": False, "agent_return_code": None,
                    "elapsed_seconds": elapsed, "errors": ["agent timeout after 1200 seconds"]}
    elapsed = time.monotonic() - started
    if result.returncode != 0 or not attempt_raw.exists():
        if attempt_raw.exists():
            attempt_raw.replace(
                output / "raw" / f"failed-shard-{suffix}-{uuid.uuid4().hex}.json"
            )
        return {"shard": shard, "ok": False, "agent_return_code": result.returncode,
                "elapsed_seconds": elapsed, "errors": ["agent failed or raw output missing"]}
    try:
        payload = json.loads(attempt_raw.read_text(encoding="utf-8"))
        normalize_atomic_refs(payload)
        valid_wrapper = {
            ref for ref in SOURCE_REF.findall(source_text) if ref.startswith("I-")
        }
        normalize_provenance(payload, valid_primary, valid_wrapper)
        errors = validate_payload(payload, metadata, source_text, valid_primary)
    except Exception as error:  # retained in durable failed queue
        attempt_raw.replace(
            output / "raw" / f"failed-shard-{suffix}-{uuid.uuid4().hex}.json"
        )
        return {"shard": shard, "ok": False, "agent_return_code": result.returncode,
                "elapsed_seconds": elapsed, "errors": [f"parse/validation exception: {error}"]}
    attempt_raw.replace(raw_output)
    output_path = output / "outputs" / f"compact-shard-{suffix}.jsonl"
    atomic_text(
        output_path,
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in payload["records"]),
    )
    write_report(output / "reports" / f"shard-{suffix}.md", payload, elapsed)
    atomic_json(output / "validations" / f"shard-{suffix}.json", {
        "shard": shard, "record_count": len(payload["records"]),
        "unit_count": sum(len(row["logic_units"]) for row in payload["records"]),
        "error_count": len(errors), "errors": errors,
    })
    return {"shard": shard, "ok": not errors, "agent_return_code": result.returncode,
            "elapsed_seconds": elapsed, "errors": errors}


def progress(connection: sqlite3.Connection, manifest: dict, output: Path) -> dict:
    counts = dict(connection.execute("SELECT status,COUNT(*) FROM shards GROUP BY status").fetchall())
    review_counts = dict(connection.execute(
        "SELECT status,COALESCE(SUM(review_count),0) FROM shards GROUP BY status"
    ).fetchall())
    elapsed = connection.execute(
        "SELECT COALESCE(SUM(elapsed_seconds),0), COALESCE(AVG(elapsed_seconds),0) "
        "FROM shards WHERE status IN ('complete','failed')"
    ).fetchone()
    payload = {
        "updated_at": now(), "shards": counts, "reviews": review_counts,
        "total_shards": manifest["shard_count"], "total_reviews": manifest["review_count"],
        "completed_agent_seconds": round(elapsed[0], 3),
        "mean_finished_shard_seconds": round(elapsed[1], 3),
    }
    atomic_json(output / "progress.json", payload)
    return payload


def reconcile_completed(connection: sqlite3.Connection, output: Path) -> None:
    """Fail closed when a completed DB row lacks a complete durable artifact set."""
    for shard, review_count in connection.execute(
        "SELECT shard,review_count FROM shards WHERE status='complete'"
    ).fetchall():
        suffix = f"{shard:05d}"
        paths = {
            "raw": output / "raw" / f"shard-{suffix}.json",
            "output": output / "outputs" / f"compact-shard-{suffix}.jsonl",
            "report": output / "reports" / f"shard-{suffix}.md",
            "validation": output / "validations" / f"shard-{suffix}.json",
        }
        error = None
        try:
            if not all(path.exists() for path in paths.values()):
                error = "completed shard is missing one or more durable artifacts"
            else:
                validation = json.loads(paths["validation"].read_text(encoding="utf-8"))
                records = [line for line in paths["output"].read_text(encoding="utf-8").splitlines() if line.strip()]
                if validation.get("error_count") != 0 or len(records) != review_count:
                    error = "completed shard artifacts fail resume reconciliation"
        except Exception as exception:
            error = f"completed shard artifact reconciliation failed: {exception}"
        if error:
            connection.execute(
                "UPDATE shards SET status='failed',finished_at=?,error=? WHERE shard=?",
                (now(), error, shard),
            )
    connection.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--max-shards", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.max_shards is not None and args.max_shards < 0:
        parser.error("--max-shards must be nonnegative")
    repo = Path(__file__).resolve().parents[1]
    if not CODEX.is_file():
        raise RuntimeError(f"codex executable is missing: {CODEX}")
    subprocess.run(
        [str(CODEX), "login", "status"], check=True, timeout=30,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    source = (repo / args.source).resolve() if not args.source.is_absolute() else args.source
    output = (repo / args.output).resolve() if not args.output.is_absolute() else args.output
    manifest, connection = initialize(source, output)
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    database = Path(source_manifest["database"])
    lock_stream = (output / "supervisor.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another compact-2026 supervisor holds the lock")
    if args.retry_failed:
        connection.execute("UPDATE shards SET status='pending',error=NULL WHERE status='failed'")
    connection.execute(
        "UPDATE shards SET status='failed',finished_at=?,"
        "error='supervisor interrupted while shard was running; deferred for later retry' "
        "WHERE status='running'",
        (now(),),
    )
    connection.commit()
    reconcile_completed(connection, output)
    protocol = (output / "PROTOCOL.md").read_text(encoding="utf-8")
    pending = [row[0] for row in connection.execute(
        "SELECT shard FROM shards WHERE status='pending' ORDER BY shard"
    )]
    if args.max_shards is not None:
        pending = pending[:args.max_shards]
    iterator = iter(pending)
    futures: dict[concurrent.futures.Future, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        def submit_next() -> bool:
            try:
                shard = next(iterator)
            except StopIteration:
                return False
            connection.execute(
                "UPDATE shards SET status='running',attempts=attempts+1,started_at=?,finished_at=NULL,error=NULL WHERE shard=?",
                (now(), shard),
            )
            connection.commit()
            futures[executor.submit(
                run_shard, repo, source, output, database, shard, protocol
            )] = shard
            return True

        for _ in range(min(args.workers, len(pending))):
            submit_next()
        progress(connection, manifest, output)
        while futures:
            done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                shard = futures.pop(future)
                try:
                    result = future.result()
                except Exception as error:
                    result = {"shard": shard, "ok": False, "agent_return_code": None,
                              "elapsed_seconds": 0, "errors": [str(error)]}
                status = "complete" if result["ok"] else "failed"
                connection.execute(
                    "UPDATE shards SET status=?,finished_at=?,elapsed_seconds=?,agent_return_code=?,"
                    "validation_error_count=?,error=? WHERE shard=?",
                    (status, now(), result["elapsed_seconds"], result["agent_return_code"],
                     len(result["errors"]), "\n".join(result["errors"])[:20000], shard),
                )
                connection.commit()
                payload = progress(connection, manifest, output)
                print(json.dumps({"finished_shard": shard, "status": status,
                                  "progress": payload}, ensure_ascii=False), flush=True)
                submit_next()
    final = progress(connection, manifest, output)
    connection.close()
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
