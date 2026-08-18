"""Run one fresh, ephemeral agent per new-card screening shard."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_DIR = Path(
    "data/analysis/iclr/episode-reclassification-3135/new-card-screening"
)
DEFAULT_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True)
class Result:
    shard: int
    reasoning_effort: str
    status: str
    started_at: str
    finished_at: str
    elapsed_seconds: float
    return_code: int | None
    validation_return_code: int | None
    log_path: str
    message: str


def now() -> str:
    return datetime.now(UTC).isoformat()


def output_paths(directory: Path, shard: int) -> tuple[Path, Path]:
    return (
        directory / f"screen-shard-{shard:03d}.jsonl",
        directory / f"screen-shard-{shard:03d}-report.md",
    )


def validation_command(directory: Path, shard: int) -> list[str]:
    return [
        "uv", "run", "python", "scripts/validate_new_card_screening_3135.py",
        "--directory", str(directory), "--shard", str(shard),
    ]


def is_valid(repo: Path, directory: Path, shard: int) -> bool:
    if not all(path.exists() for path in output_paths(directory, shard)):
        return False
    return subprocess.run(
        validation_command(directory, shard), cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def prompt(directory: Path, shard: int) -> str:
    target = str(directory)
    return f"""Screen every Episode Lite record in shard {shard:03d} against three proposed cards.

Read only:
- {target}/SCREENING_PROTOCOL.md
- {target}/source-shard-{shard:03d}.md

Do not read existing Atlas memberships, other shards, conference outcomes,
Deep outputs, global adjudication, or previous screens. Write only:
- {target}/screen-shard-{shard:03d}.jsonl
- {target}/screen-shard-{shard:03d}-report.md

Evaluate all three cards independently. A card name or keyword is never enough:
apply each hard gate and its nearest-card exclusions. Zero and multi-label results
are valid. Run `uv run python scripts/validate_new_card_screening_3135.py
--directory {target} --shard {shard}` and repair both outputs until validation
succeeds. Return only counts and output paths.
"""


def run_shard(
    repo: Path, directory: Path, log_dir: Path, shard: int, *,
    model: str, reasoning_effort: str, timeout_seconds: int,
) -> Result:
    started_at = now()
    started = time.monotonic()
    log_path = log_dir / f"screen-shard-{shard:03d}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", model, "-c", f'model_reasoning_effort="{reasoning_effort}"',
        "--cd", str(repo), prompt(directory, shard),
    ]
    return_code = validation_return_code = None
    status, message = "failed", "agent failed"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                timeout=timeout_seconds, check=False,
            )
        return_code = completed.returncode
        if return_code == 0:
            validation = subprocess.run(
                validation_command(directory, shard), cwd=repo,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            validation_return_code = validation.returncode
            if validation.returncode == 0:
                status, message = "complete", "agent and validation succeeded"
            else:
                message = "agent exited successfully but validation failed"
        else:
            message = f"agent exited with code {return_code}"
    except subprocess.TimeoutExpired:
        status, message = "timeout", f"agent exceeded {timeout_seconds} seconds"
    except Exception as exc:
        message = f"supervisor exception: {type(exc).__name__}: {exc}"
    return Result(
        shard, reasoning_effort, status, started_at, now(),
        round(time.monotonic() - started, 3), return_code,
        validation_return_code, str(log_path.resolve()), message,
    )


def write_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=20 * 60)
    parser.add_argument("--shard", type=int, action="append")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    all_shards = [row["shard"] for row in manifest["shards"]]
    shards = args.shard or all_shards
    unknown = set(shards) - set(all_shards)
    if unknown:
        raise SystemExit(f"unknown shards: {sorted(unknown)}")
    state_path = args.directory / "supervisor-state.json"
    log_dir = args.directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists() else {"version": 1, "shards": {}}
    )
    state["run_config"] = {
        "model": args.model, "reasoning_effort": args.reasoning_effort,
        "concurrency": args.concurrency, "timeout_seconds": args.timeout_seconds,
    }
    pending: list[int] = []
    for shard in shards:
        key = str(shard)
        if is_valid(repo, args.directory, shard):
            state["shards"].setdefault(key, {})
            state["shards"][key].update({
                "status": "complete", "message": "pre-existing outputs validated",
                "checked_at": now(),
            })
        elif state["shards"].get(key, {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(shard)
    if args.dry_run:
        print(json.dumps({"pending": pending, "count": len(pending)}))
        return
    write_state(state_path, state)
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                run_shard, repo, args.directory, log_dir, shard,
                model=args.model, reasoning_effort=args.reasoning_effort,
                timeout_seconds=args.timeout_seconds,
            ): shard for shard in pending
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["shards"][str(result.shard)] = asdict(result)
                write_state(state_path, state)
            print(
                f"shard={result.shard:03d} status={result.status} "
                f"elapsed={result.elapsed_seconds:.1f}s message={result.message}",
                flush=True,
            )


if __name__ == "__main__":
    main()
