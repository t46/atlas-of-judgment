"""Create verified, rotating online backups of the ICLR 2026 production DB."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/analysis/iclr/production-2026.sqlite3"
DEFAULT_BACKUP_DIR = ROOT / "data/backups/iclr-2026"
BACKUP_PREFIX = "production-2026-"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def exclusive_backup_lock(backup_dir: Path):
    """Allow only one backup worker for a destination directory."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    lock_path = backup_dir / ".backup.lock"
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"another backup worker holds {lock_path}") from error
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def inspect_backup(path: Path) -> dict[str, object]:
    with closing(readonly_connection(path)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        complete_jobs = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='complete'"
        ).fetchone()[0]
        memos = connection.execute("SELECT COUNT(*) FROM memos").fetchone()[0]
        state = connection.execute(
            "SELECT spent_microusd, reserved_microusd, updated_at FROM budget_state"
        ).fetchone()
    return {
        "created_at": now_iso(),
        "source_updated_at": state["updated_at"],
        "jobs": jobs,
        "complete_jobs": complete_jobs,
        "memos": memos,
        "spent_usd": state["spent_microusd"] / 1_000_000,
        "reserved_usd": state["reserved_microusd"] / 1_000_000,
        "size_bytes": path.stat().st_size,
        "integrity_check": integrity,
    }


def rotate_backups(backup_dir: Path, keep: int) -> list[Path]:
    snapshots = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.sqlite3"))
    removed: list[Path] = []
    for snapshot in snapshots[:-keep]:
        metadata = snapshot.with_suffix(".json")
        snapshot.unlink()
        metadata.unlink(missing_ok=True)
        removed.append(snapshot)
    return removed


def backup_once(source: Path, backup_dir: Path, keep: int) -> tuple[Path, dict[str, object]]:
    """Copy a live SQLite database without modifying it, then verify and adopt it."""
    if not source.is_file():
        raise FileNotFoundError(source)
    if keep <= 0:
        raise ValueError("keep must be positive")

    backup_dir.mkdir(parents=True, exist_ok=True)
    name = f"{BACKUP_PREFIX}{timestamp()}"
    final_path = backup_dir / f"{name}.sqlite3"
    final_metadata = backup_dir / f"{name}.json"
    temporary_path = backup_dir / f".{name}.{os.getpid()}.tmp.sqlite3"
    temporary_metadata = backup_dir / f".{name}.{os.getpid()}.tmp.json"

    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = readonly_connection(source)
        destination_connection = sqlite3.connect(temporary_path, timeout=30)
        source_connection.backup(destination_connection, pages=2_048, sleep=0.05)
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None

        metadata = inspect_backup(temporary_path)
        temporary_metadata.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Both renames are on the same filesystem and therefore atomic. Existing
        # generations are removed only after a new verified snapshot is adopted.
        temporary_path.replace(final_path)
        temporary_metadata.replace(final_metadata)
        removed = rotate_backups(backup_dir, keep)
        if removed:
            print(
                "rotated: " + ", ".join(path.name for path in removed),
                flush=True,
            )
        return final_path, metadata
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary_path.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)


def full_analysis_complete(source: Path) -> bool:
    """Stop the periodic worker only after the final stage exists and is complete."""
    with closing(readonly_connection(source)) as connection:
        synthesis_exists = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM jobs WHERE stage='paper_synthesis')"
        ).fetchone()[0]
        incomplete = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status!='complete'"
        ).fetchone()[0]
    return bool(synthesis_exists) and incomplete == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--interval-hours", type=float, default=6.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.keep <= 0:
        parser.error("--keep must be positive")
    if args.interval_hours <= 0:
        parser.error("--interval-hours must be positive")
    return args


def main() -> None:
    args = parse_args()
    with exclusive_backup_lock(args.backup_dir):
        while True:
            backup_succeeded = False
            try:
                path, metadata = backup_once(args.source, args.backup_dir, args.keep)
                backup_succeeded = True
                print(
                    f"backup complete: {path} "
                    f"({metadata['complete_jobs']:,} complete jobs, "
                    f"{metadata['size_bytes'] / (1024**2):.1f} MiB, integrity=ok)",
                    flush=True,
                )
            except Exception as error:
                print(
                    f"backup failed at {now_iso()}: {type(error).__name__}: {error}",
                    flush=True,
                )
                if args.once:
                    raise

            if args.once:
                return
            if backup_succeeded and full_analysis_complete(args.source):
                print("final analysis state backed up; periodic worker exiting", flush=True)
                return
            time.sleep(args.interval_hours * 60 * 60)


if __name__ == "__main__":
    main()
