"""Supervise the resumable ICLR direct analysis and its local backups.

The supervisor is intentionally conservative:

* the existing runner's SQLite lock prevents duplicate API runners;
* interrupted calls are charged fail-closed and requeued by the runner;
* local budget and provider-balance floors prevent new work from being started;
* every adopted backup is copied through SQLite's backup API and integrity
  checked before old generations are rotated.

Run this under a persistent process manager (the project uses tmux) with
``DEEPSEEK_API_KEY`` already injected by 1Password.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data/analysis/iclr/direct-2018-2026.sqlite3"
DEFAULT_BACKUP_DIR = ROOT / "data/backups/iclr-direct-2018-2026"
DEFAULT_LOG = ROOT / "data/logs/iclr-direct-supervisor.log"
DEFAULT_RUN_LOG = ROOT / "data/logs/iclr-direct-2018-2026.log"
DEFAULT_PROJECT = "iclr-2018-2026-direct-v1"
DEFAULT_BUDGET_USD = 210.0
DEFAULT_PROVIDER_FLOOR_USD = 100.0
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_BACKUP_INTERVAL_SECONDS = 6 * 60 * 60
DEFAULT_KEEP_BACKUPS = 3


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def log(message: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()} {message}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


@contextmanager
def supervisor_lock(database: Path):
    path = database.with_suffix(f"{database.suffix}.supervisor.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise RuntimeError(f"another supervisor holds {path}") from error
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def runner_lock_available(database: Path) -> bool:
    path = database.with_suffix(f"{database.suffix}.runner.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True
    finally:
        handle.close()


def status(database: Path, project: str) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, timeout=30
    )
    try:
        rows = dict(
            connection.execute(
                "SELECT status, COUNT(*) FROM jobs WHERE stage='forum_direct' GROUP BY status"
            ).fetchall()
        )
        state = connection.execute(
            """
            SELECT spent_microusd, reserved_microusd, budget_microusd
            FROM budget_state WHERE project_id=?
            """,
            (project,),
        ).fetchone()
        if state is None:
            raise RuntimeError(f"budget project not found: {project}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"source integrity check failed: {integrity}")
        return {
            "complete": int(rows.get("complete", 0)),
            "pending": int(rows.get("pending", 0)),
            "running": int(rows.get("running", 0)),
            "failed": int(rows.get("failed", 0)),
            "spent_usd": state[0] / 1_000_000,
            "reserved_usd": state[1] / 1_000_000,
            "budget_usd": state[2] / 1_000_000,
        }
    finally:
        connection.close()


def requeue_retryable_failures(
    database: Path, *, max_attempts: int = 3
) -> tuple[int, int]:
    """Requeue transport failures, retaining permanently failed jobs."""
    connection = sqlite3.connect(database, timeout=30)
    try:
        with connection:
            candidates = connection.execute(
                """
                SELECT j.job_id,
                       1 + (
                           SELECT COUNT(*) FROM api_call_attempts a
                           WHERE a.job_id = j.job_id
                       ) AS attempts
                FROM jobs j
                JOIN api_calls c ON c.job_id = j.job_id
                WHERE j.stage='forum_direct'
                  AND j.status='failed'
                  AND c.status='failed_charged'
                """
            ).fetchall()
            retryable = [
                (job_id,) for job_id, attempts in candidates if attempts <= max_attempts
            ]
            permanent = len(candidates) - len(retryable)
            connection.executemany(
                "UPDATE jobs SET status='pending' WHERE job_id=?", retryable
            )
        return len(retryable), permanent
    finally:
        connection.close()


def provider_balance_usd(api_key: str) -> float:
    request = Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS URL
        payload = json.load(response)
    if not payload.get("is_available"):
        raise RuntimeError("DeepSeek reports the account as unavailable")
    for balance in payload.get("balance_infos") or []:
        if balance.get("currency") == "USD":
            return float(balance["total_balance"])
    raise RuntimeError("DeepSeek balance response did not contain USD")


def inspect_backup(path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        jobs = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage='forum_direct'"
        ).fetchone()[0]
        complete = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE stage='forum_direct' AND status='complete'"
        ).fetchone()[0]
        memos = db.execute("SELECT COUNT(*) FROM memos").fetchone()[0]
    return {
        "created_at": now_iso(),
        "jobs": jobs,
        "complete_jobs": complete,
        "memos": memos,
        "size_bytes": path.stat().st_size,
        "integrity_check": integrity,
    }


def backup_once(source: Path, directory: Path, keep: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = f"direct-2018-2026-{stamp()}"
    final = directory / f"{name}.sqlite3"
    metadata_path = directory / f"{name}.json"
    temporary = directory / f".{name}.{os.getpid()}.tmp.sqlite3"
    temporary_metadata = directory / f".{name}.{os.getpid()}.tmp.json"
    source_db = sqlite3.connect(
        f"file:{source.resolve()}?mode=ro", uri=True, timeout=30
    )
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(temporary, timeout=30)
        source_db.backup(destination, pages=2_048, sleep=0.05)
        destination.close()
        destination = None
        source_db.close()
        metadata = inspect_backup(temporary)
        temporary_metadata.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(final)
        temporary_metadata.replace(metadata_path)
        snapshots = sorted(directory.glob("direct-2018-2026-*.sqlite3"))
        for old in snapshots[:-keep]:
            old.unlink()
            old.with_suffix(".json").unlink(missing_ok=True)
        return final
    finally:
        if destination is not None:
            destination.close()
        source_db.close()
        temporary.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)


def start_runner(args: argparse.Namespace, run_log_handle):
    command = [
        sys.executable,
        str(ROOT / "scripts/run_deepseek_pilot.py"),
        "--database",
        str(args.database),
        "--stage",
        "forum_direct",
        "--max-jobs",
        "51813",
        "--concurrency",
        str(args.concurrency),
        "--progress-every",
        "500",
        "--balance-check-every",
        "100",
        "--minimum-provider-balance-usd",
        str(args.provider_floor_usd),
        "--budget-usd",
        str(args.budget_usd),
        "--thinking",
        "disabled",
    ]
    environment = os.environ.copy()
    environment["DEEPSEEK_PROJECT_ID"] = args.project
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=run_log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--run-log", type=Path, default=DEFAULT_RUN_LOG)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument(
        "--provider-floor-usd", type=float, default=DEFAULT_PROVIDER_FLOOR_USD
    )
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument(
        "--backup-interval-seconds",
        type=float,
        default=DEFAULT_BACKUP_INTERVAL_SECONDS,
    )
    parser.add_argument("--keep-backups", type=int, default=DEFAULT_KEEP_BACKUPS)
    parser.add_argument("--concurrency", type=int, default=32)
    return parser.parse_args()


def supervise(args: argparse.Namespace) -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY must be injected through 1Password")
    args.database = args.database.resolve()
    args.backup_dir = args.backup_dir.resolve()
    args.log = args.log.resolve()
    args.run_log = args.run_log.resolve()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.run_log.parent.mkdir(parents=True, exist_ok=True)
    child = None
    last_backup = 0.0
    last_provider_check = 0.0
    provider_balance = None

    with supervisor_lock(args.database):
        log("supervisor started", args.log)
        while True:
            current = status(args.database, args.project)
            now = time.monotonic()

            if now - last_backup >= args.backup_interval_seconds:
                try:
                    path = backup_once(args.database, args.backup_dir, args.keep_backups)
                    log(f"backup complete: {path}", args.log)
                except Exception as error:
                    log(f"backup failed: {type(error).__name__}: {error}", args.log)
                last_backup = now

            if now - last_provider_check >= 300:
                try:
                    provider_balance = provider_balance_usd(os.environ["DEEPSEEK_API_KEY"])
                    log(f"provider balance: ${provider_balance:.2f}", args.log)
                except Exception as error:
                    # A failed balance check must not permit a future restart.
                    provider_balance = 0.0
                    log(
                        f"provider balance check failed; fail-closed: "
                        f"{type(error).__name__}: {error}",
                        args.log,
                    )
                last_provider_check = now

            lock_available = runner_lock_available(args.database)
            if current["pending"] == 0 and current["running"] == 0:
                if current["failed"] > 0:
                    log(
                        f"all pending jobs complete; deferring {current['failed']} "
                        "API failures without automatic retry",
                        args.log,
                    )
                else:
                    log("all direct jobs complete", args.log)
                try:
                    path = backup_once(args.database, args.backup_dir, args.keep_backups)
                    log(f"final backup: {path}", args.log)
                except Exception as error:
                    log(f"final backup failed: {type(error).__name__}: {error}", args.log)
                return

            budget_open = (
                current["spent_usd"] + current["reserved_usd"] < current["budget_usd"]
            )
            provider_open = (
                provider_balance is None or provider_balance >= args.provider_floor_usd
            )
            if not budget_open:
                log("local budget exhausted; no new runner will be started", args.log)
                return
            if provider_balance is not None and not provider_open:
                log("provider floor reached; no new runner will be started", args.log)
                return

            if lock_available:
                if child is not None and child.poll() is not None:
                    log(f"runner exited with code {child.returncode}; restarting", args.log)
                if current["pending"] > 0:
                    run_log = args.run_log.open("a", encoding="utf-8")
                    child = start_runner(args, run_log)
                    log(f"runner started pid={child.pid}; status={current}", args.log)
            elif child is not None and child.poll() is not None:
                log(
                    f"runner child exited with code {child.returncode}; "
                    "waiting for SQLite lock to clear",
                    args.log,
                )

            time.sleep(args.interval_seconds)


def main() -> None:
    args = parse_args()
    if args.keep_backups <= 0 or args.concurrency <= 0:
        raise SystemExit("keep-backups and concurrency must be positive")
    supervise(args)


if __name__ == "__main__":
    main()
