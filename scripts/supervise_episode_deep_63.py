"""Run one fresh Luna-low Codex agent per review for Episode Deep enrichment."""

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


DEFAULT_DIR = Path("data/analysis/iclr/episode-deep-63")
DEFAULT_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True)
class Result:
    unit: int
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


def output_paths(directory: Path, unit: int) -> tuple[Path, Path]:
    return (
        directory / f"deep-review-{unit:02d}.jsonl",
        directory / f"deep-review-{unit:02d}-report.md",
    )


def validation_command(directory: Path, unit: int) -> list[str]:
    return [
        "uv", "run", "python", "scripts/validate_episode_deep_63.py",
        "--directory", str(directory), "--unit", str(unit),
    ]


def is_valid(repo: Path, directory: Path, unit: int) -> bool:
    if not all(path.exists() for path in output_paths(directory, unit)):
        return False
    result = subprocess.run(
        validation_command(directory, unit), cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def prompt(unit: int) -> str:
    suffix = f"{unit:02d}"
    return f"""Deep-enrich the selected evaluation episodes for source review {suffix}.

Read only:
- data/analysis/iclr/episode-deep-63/DEEP_PROTOCOL.md
- data/analysis/iclr/episode-deep-63/source-review-{suffix}.md
- schemas/evaluation-episode-v0.2.json

Do not read Atlas files, candidate-selection metadata, conference outcomes,
other source reviews, or any other Deep output. Write only:
- data/analysis/iclr/episode-deep-63/deep-review-{suffix}.jsonl
- data/analysis/iclr/episode-deep-63/deep-review-{suffix}-report.md

Preserve the Lite spine and decompose only what the source supports. Empty
fields are better than invented warrants, alternatives, or counterfactuals.
Run `uv run python scripts/validate_episode_deep_63.py --unit {unit}` and repair
both files until validation succeeds. Your final response should contain only
episode/claim counts, unsupported-field counts, and output paths.
"""


def run_unit(
    repo: Path,
    directory: Path,
    log_dir: Path,
    unit: int,
    *,
    model: str,
    timeout_seconds: int,
) -> Result:
    started_at = now()
    started = time.monotonic()
    log_path = log_dir / f"deep-review-{unit:02d}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", model, "-c", 'model_reasoning_effort="low"',
        "--cd", str(repo), prompt(unit),
    ]
    return_code: int | None = None
    validation_return_code: int | None = None
    status = "failed"
    message = "agent failed"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                timeout=timeout_seconds, check=False,
            )
        return_code = completed.returncode
        if return_code == 0:
            validation = subprocess.run(
                validation_command(directory, unit), cwd=repo,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            validation_return_code = validation.returncode
            if validation.returncode == 0:
                status = "complete"
                message = "agent and validation succeeded"
            else:
                message = "agent exited successfully but validation failed"
        else:
            message = f"agent exited with code {return_code}"
    except subprocess.TimeoutExpired:
        status = "timeout"
        message = f"agent exceeded {timeout_seconds} seconds"
    except Exception as exc:
        message = f"supervisor exception: {type(exc).__name__}: {exc}"
    return Result(
        unit, status, started_at, now(), round(time.monotonic() - started, 3),
        return_code, validation_return_code, str(log_path.resolve()), message,
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
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=20 * 60)
    parser.add_argument("--unit", type=int, action="append")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    all_units = [row["unit"] for row in manifest["reviews"]]
    units = args.unit or all_units
    unknown = set(units) - set(all_units)
    if unknown:
        raise SystemExit(f"unknown units: {sorted(unknown)}")
    state_path = args.directory / "supervisor-state.json"
    log_dir = args.directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists() else {"version": 1, "units": {}}
    )
    pending = []
    for unit in units:
        if is_valid(repo, args.directory, unit):
            state["units"].setdefault(str(unit), {})
            state["units"][str(unit)].update(
                {"status": "complete", "message": "pre-existing outputs validated", "checked_at": now()}
            )
        elif state["units"].get(str(unit), {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(unit)
    if args.dry_run:
        print(json.dumps({"pending": pending, "count": len(pending)}))
        return

    write_state(state_path, state)
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                run_unit, repo, args.directory, log_dir, unit,
                model=args.model, timeout_seconds=args.timeout_seconds,
            ): unit
            for unit in pending
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["units"][str(result.unit)] = asdict(result)
                write_state(state_path, state)
            print(
                f"unit={result.unit:02d} status={result.status} "
                f"elapsed={result.elapsed_seconds:.1f}s message={result.message}",
                flush=True,
            )


if __name__ == "__main__":
    main()
