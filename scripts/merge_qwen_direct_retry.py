"""Validate and merge an isolated Direct Qwen retry into its source run.

The first-attempt provider artifacts and usage columns are never overwritten.
Retry accounting is recorded in ``retry_attempts`` and retry provider payloads
remain in the retry run. Structured outputs are promoted atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def rows(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return connection.execute(
        "SELECT custom_id,source_job_id,status,provider_status,prompt_tokens,"
        "completion_tokens,total_tokens,error,updated_at FROM requests ORDER BY custom_id"
    ).fetchall()


def retry_ancestry(source_run: Path, retry_run: Path) -> list[str]:
    """Return the retry-to-source lineage, rejecting unrelated run trees."""
    lineage = [str(retry_run.resolve())]
    current = retry_run.resolve()
    seen = {current}
    for _ in range(32):
        manifest_path = current / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(
                f"retry ancestry ended before source run at {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        parent_raw = manifest.get("source_run")
        if not parent_raw:
            raise RuntimeError(
                f"retry ancestry ended before source run at {manifest_path}"
            )
        parent = Path(parent_raw).resolve()
        lineage.append(str(parent))
        if parent == source_run.resolve():
            return lineage
        if parent in seen:
            raise RuntimeError(f"cycle in retry ancestry at {parent}")
        seen.add(parent)
        current = parent
    raise RuntimeError("retry ancestry exceeds 32 levels")


def validate(source_run: Path, retry_run: Path) -> dict[str, Any]:
    lineage = retry_ancestry(source_run, retry_run)

    source = sqlite3.connect(source_run / "state.sqlite3")
    retry = sqlite3.connect(retry_run / "state.sqlite3")
    try:
        unfinished = retry.execute(
            "SELECT status,count(*) FROM requests "
            "WHERE status IN ('prepared','running') GROUP BY status"
        ).fetchall()
        if unfinished:
            raise RuntimeError(f"retry run is not finished: {unfinished}")
        source_unfinished = source.execute(
            "SELECT status,count(*) FROM requests "
            "WHERE status IN ('prepared','running') GROUP BY status"
        ).fetchall()
        if source_unfinished:
            raise RuntimeError(f"source run is not finished: {source_unfinished}")

        source_map = {
            custom_id: (job_id, status)
            for custom_id, job_id, status in source.execute(
                "SELECT custom_id,source_job_id,status FROM requests"
            )
        }
        retry_rows = rows(retry)
        counts = {"complete": 0, "failed_with_output": 0, "failed_without_output": 0}
        errors: list[str] = []
        for row in retry_rows:
            custom_id, job_id, status = row[:3]
            source_identity = source_map.get(custom_id)
            if source_identity is None:
                errors.append(f"missing source request: {custom_id}")
                continue
            if source_identity[0] != job_id:
                errors.append(f"source job mismatch: {custom_id}")
            output = retry_run / "outputs" / f"{custom_id}.json"
            validation = retry_run / "validations" / f"{custom_id}.json"
            if not validation.is_file():
                errors.append(f"missing retry validation: {custom_id}")
                continue
            validation_payload = json.loads(validation.read_text(encoding="utf-8"))
            validation_errors = validation_payload.get("errors") or []
            if status == "complete":
                if not output.is_file():
                    errors.append(f"complete retry lacks output: {custom_id}")
                if validation_errors:
                    errors.append(f"complete retry has validation errors: {custom_id}")
                counts["complete"] += 1
            elif status == "failed" and output.is_file():
                counts["failed_with_output"] += 1
            elif status == "failed":
                counts["failed_without_output"] += 1
            else:
                errors.append(f"unexpected retry status {status!r}: {custom_id}")
        if errors:
            raise RuntimeError("merge validation failed:\n" + "\n".join(errors[:100]))
        return {
            "retry_request_count": len(retry_rows),
            "retry_ancestry": lineage,
            **counts,
            "retry_cost_usd": round(sum(
                (
                    (prompt or 0) * (0.03 if (prompt or 0) <= 32_000 else 0.10)
                    + (completion or 0) * (0.13 if (prompt or 0) <= 32_000 else 0.40)
                ) / 1_000_000
                for _, _, _, provider_status, prompt, completion, _, _, _ in retry_rows
                if provider_status == 200
            ), 6),
        }
    finally:
        source.close()
        retry.close()


def apply_merge(source_run: Path, retry_run: Path, report: dict[str, Any]) -> dict[str, Any]:
    backup = source_run / f"state-pre-retry-merge-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    source = sqlite3.connect(source_run / "state.sqlite3", timeout=60)
    retry = sqlite3.connect(retry_run / "state.sqlite3")
    try:
        source.execute("PRAGMA journal_mode=WAL")
        backup_connection = sqlite3.connect(backup)
        try:
            source.backup(backup_connection)
        finally:
            backup_connection.close()
        retry_rows = rows(retry)

        # Promote complete outputs and retain warning-bearing complete JSON from
        # failed retries. In both cases the retry validation travels with it.
        promoted_outputs = 0
        for row in retry_rows:
            custom_id, _, status = row[:3]
            retry_output = retry_run / "outputs" / f"{custom_id}.json"
            retry_validation = retry_run / "validations" / f"{custom_id}.json"
            if retry_output.is_file():
                atomic_copy(retry_output, source_run / "outputs" / retry_output.name)
                promoted_outputs += 1
            atomic_copy(
                retry_validation, source_run / "validations" / retry_validation.name
            )

        source.execute("""
            CREATE TABLE IF NOT EXISTS retry_attempts (
                retry_run TEXT NOT NULL,
                custom_id TEXT NOT NULL,
                source_job_id TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_status INTEGER,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                error TEXT,
                retry_updated_at TEXT NOT NULL,
                merged_at TEXT NOT NULL,
                PRIMARY KEY(retry_run,custom_id)
            )
        """)
        retry_name = retry_run.name
        merged_at = now()
        source.executemany(
            "INSERT OR REPLACE INTO retry_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (retry_name, *row, merged_at)
                for row in retry_rows
            ],
        )
        complete_ids = [row[0] for row in retry_rows if row[2] == "complete"]
        source.executemany(
            "UPDATE requests SET status='complete',error=NULL,updated_at=? WHERE custom_id=?",
            [(merged_at, custom_id) for custom_id in complete_ids],
        )
        failed_rows = [row for row in retry_rows if row[2] == "failed"]
        source.executemany(
            "UPDATE requests SET error=?,updated_at=? WHERE custom_id=?",
            [
                (f"{retry_name}: {row[7] or 'retry failed'}", merged_at, row[0])
                for row in failed_rows
            ],
        )
        source.commit()

        final_statuses = dict(
            source.execute("SELECT status,count(*) FROM requests GROUP BY status")
        )
        result = {
            **report,
            "source_run": str(source_run),
            "retry_run": str(retry_run),
            "backup": str(backup),
            "promoted_outputs": promoted_outputs,
            "final_statuses": final_statuses,
            "merged_at": merged_at,
        }
        merge_record = source_run / f"merge-{retry_name}.json"
        atomic_json(merge_record, result)
        result["merge_record"] = str(merge_record)
        return result
    except Exception:
        source.rollback()
        raise
    finally:
        source.close()
        retry.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--retry-run", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source_run = args.source_run.resolve()
    retry_run = args.retry_run.resolve()
    report = validate(source_run, retry_run)
    if args.apply:
        report = apply_merge(source_run, retry_run, report)
    else:
        report["mode"] = "dry-run"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
