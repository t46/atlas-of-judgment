"""Resume-safe orchestrator for the complete ICLR 2026 layered analysis."""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data/analysis/iclr/production-2026.sqlite3"
RUNNER = ROOT / "scripts/run_deepseek_pilot.py"
FOLLOWUPS = ROOT / "scripts/prepare_pilot_followups.py"
SOURCE = ROOT / "data/processed/iclr/analysis.sqlite3"
PROJECT_ID = os.environ.get("DEEPSEEK_PROJECT_ID", "iclr-2026-full-layered-v1")

STAGE_CONFIG = {
    "initial_blind": {"chunk": 5_000, "length_step": 2_000, "length_cap": 8_000},
    "trajectory": {"chunk": 2_000, "length_step": 2_000, "length_cap": 12_000},
    "paper_synthesis": {"chunk": 500, "length_step": 2_000, "length_cap": 12_000},
}


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def count_status(connection: sqlite3.Connection, stage: str, status: str) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage=? AND status=?",
        (stage, status),
    ).fetchone()[0]


def print_stage_status(connection: sqlite3.Connection, stage: str) -> None:
    rows = connection.execute(
        "SELECT status, COUNT(*) count FROM jobs WHERE stage=? GROUP BY status",
        (stage,),
    ).fetchall()
    summary = ", ".join(f"{row['status']}={row['count']:,}" for row in rows)
    print(f"{stage}: {summary or 'no jobs'}", flush=True)


def run_runner(
    *,
    database: Path,
    stage: str,
    max_jobs: int,
    concurrency: int,
    budget_usd: float,
    provider_floor_usd: float,
) -> None:
    environment = os.environ.copy()
    environment["DEEPSEEK_PROJECT_ID"] = PROJECT_ID
    subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--database",
            str(database),
            "--stage",
            stage,
            "--max-jobs",
            str(max_jobs),
            "--concurrency",
            str(concurrency),
            "--progress-every",
            "100",
            "--balance-check-every",
            "500",
            "--minimum-provider-balance-usd",
            str(provider_floor_usd),
            "--thinking",
            "disabled",
            "--budget-usd",
            str(budget_usd),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )


def drain_pending(
    connection: sqlite3.Connection,
    *,
    database: Path,
    stage: str,
    concurrency: int,
    budget_usd: float,
    provider_floor_usd: float,
) -> None:
    chunk = STAGE_CONFIG[stage]["chunk"]
    while True:
        pending = count_status(connection, stage, "pending")
        if not pending:
            return
        complete_before = count_status(connection, stage, "complete")
        print(
            f"{stage}: starting bounded chunk of {min(chunk, pending):,}; "
            f"{pending:,} pending",
            flush=True,
        )
        run_runner(
            database=database,
            stage=stage,
            max_jobs=min(chunk, pending),
            concurrency=concurrency,
            budget_usd=budget_usd,
            provider_floor_usd=provider_floor_usd,
        )
        complete_after = count_status(connection, stage, "complete")
        pending_after = count_status(connection, stage, "pending")
        print_stage_status(connection, stage)
        if pending_after and complete_after == complete_before:
            raise RuntimeError(
                f"{stage}: no progress with {pending_after:,} pending; "
                "budget or provider-balance gate may have stopped the run"
            )


def retry_failures(
    connection: sqlite3.Connection,
    *,
    database: Path,
    stage: str,
    concurrency: int,
    budget_usd: float,
    provider_floor_usd: float,
    attempts: int = 3,
) -> None:
    for attempt in range(1, attempts + 1):
        failed = count_status(connection, stage, "failed")
        if not failed:
            return
        print(f"{stage}: retrying {failed:,} failed jobs [{attempt}/{attempts}]", flush=True)
        with connection:
            connection.execute(
                "UPDATE jobs SET status='pending' WHERE stage=? AND status='failed'",
                (stage,),
            )
        drain_pending(
            connection,
            database=database,
            stage=stage,
            concurrency=max(1, min(concurrency, 8)),
            budget_usd=budget_usd,
            provider_floor_usd=provider_floor_usd,
        )
    failed = count_status(connection, stage, "failed")
    if failed:
        raise RuntimeError(f"{stage}: {failed:,} jobs still failed after retries")


def retry_truncated(
    connection: sqlite3.Connection,
    *,
    database: Path,
    stage: str,
    concurrency: int,
    budget_usd: float,
    provider_floor_usd: float,
) -> None:
    config = STAGE_CONFIG[stage]
    while True:
        rows = connection.execute(
            """
            SELECT j.job_id, j.max_tokens
            FROM jobs j JOIN api_calls a USING(job_id)
            WHERE j.stage=? AND j.status='complete' AND a.finish_reason='length'
            """,
            (stage,),
        ).fetchall()
        if not rows:
            return
        if any(row["max_tokens"] >= config["length_cap"] for row in rows):
            capped = [row["job_id"] for row in rows if row["max_tokens"] >= config["length_cap"]]
            raise RuntimeError(
                f"{stage}: {len(capped):,} jobs remain truncated at the "
                f"{config['length_cap']:,}-token cap"
            )
        print(f"{stage}: selectively retrying {len(rows):,} truncated jobs", flush=True)
        with connection:
            connection.executemany(
                "UPDATE jobs SET status='pending', max_tokens=? WHERE job_id=?",
                [
                    (
                        min(row["max_tokens"] + config["length_step"], config["length_cap"]),
                        row["job_id"],
                    )
                    for row in rows
                ],
            )
        drain_pending(
            connection,
            database=database,
            stage=stage,
            concurrency=max(1, min(concurrency, 16)),
            budget_usd=budget_usd,
            provider_floor_usd=provider_floor_usd,
        )
        retry_failures(
            connection,
            database=database,
            stage=stage,
            concurrency=concurrency,
            budget_usd=budget_usd,
            provider_floor_usd=provider_floor_usd,
        )


def ensure_stage_complete(
    connection: sqlite3.Connection,
    *,
    database: Path,
    stage: str,
    concurrency: int,
    budget_usd: float,
    provider_floor_usd: float,
) -> None:
    print_stage_status(connection, stage)
    drain_pending(
        connection,
        database=database,
        stage=stage,
        concurrency=concurrency,
        budget_usd=budget_usd,
        provider_floor_usd=provider_floor_usd,
    )
    retry_failures(
        connection,
        database=database,
        stage=stage,
        concurrency=concurrency,
        budget_usd=budget_usd,
        provider_floor_usd=provider_floor_usd,
    )
    retry_truncated(
        connection,
        database=database,
        stage=stage,
        concurrency=concurrency,
        budget_usd=budget_usd,
        provider_floor_usd=provider_floor_usd,
    )
    print_stage_status(connection, stage)


def prepare_followup(database: Path, stage: str) -> None:
    with sqlite3.connect(database) as existing_connection:
        existing = existing_connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage = ?", (stage,)
        ).fetchone()[0]
    if existing:
        print(f"{stage}: {existing:,} jobs already exist; skipping regeneration", flush=True)
        return
    subprocess.run(
        [
            sys.executable,
            str(FOLLOWUPS),
            "--source",
            str(SOURCE),
            "--pilot",
            str(database),
            "--stage",
            stage,
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--budget-usd", type=float, default=290.0)
    parser.add_argument("--provider-floor-usd", type=float, default=100.0)
    args = parser.parse_args()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("Set DEEPSEEK_API_KEY")

    connection = connect(args.database)
    try:
        ensure_stage_complete(
            connection,
            database=args.database,
            stage="initial_blind",
            concurrency=args.concurrency,
            budget_usd=args.budget_usd,
            provider_floor_usd=args.provider_floor_usd,
        )
        prepare_followup(args.database, "trajectory")
        ensure_stage_complete(
            connection,
            database=args.database,
            stage="trajectory",
            concurrency=args.concurrency,
            budget_usd=args.budget_usd,
            provider_floor_usd=args.provider_floor_usd,
        )
        prepare_followup(args.database, "paper_synthesis")
        ensure_stage_complete(
            connection,
            database=args.database,
            stage="paper_synthesis",
            concurrency=args.concurrency,
            budget_usd=args.budget_usd,
            provider_floor_usd=args.provider_floor_usd,
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"production database integrity check failed: {integrity}")
        print("ICLR 2026 full layered analysis complete", flush=True)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
