"""Run one fresh agent per unmapped-logic discovery group."""

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


DEFAULT_DIRECTORY = Path(
    "data/analysis/iclr/episode-reclassification-3135/unmapped-discovery"
)


@dataclass(frozen=True)
class Result:
    group: int
    reasoning_effort: str
    status: str
    elapsed_seconds: float
    return_code: int | None
    validation_return_code: int | None
    started_at: str
    finished_at: str
    log_path: str
    message: str


def now() -> str:
    return datetime.now(UTC).isoformat()


def validation_command(directory: Path, group: int) -> list[str]:
    return [
        "uv", "run", "python", "scripts/validate_unmapped_logic_discovery.py",
        "--directory", str(directory), "--group", str(group),
    ]


def is_valid(repo: Path, directory: Path, group: int) -> bool:
    paths = [
        directory / f"local-patterns-{group:02d}.json",
        directory / f"local-report-{group:02d}.md",
    ]
    return all(path.exists() for path in paths) and subprocess.run(
        validation_command(directory, group), cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def prompt(directory: Path, group: int) -> str:
    return f"""Discover evaluation logics outside the current Atlas for group {group:02d}.

Read only:
- {directory}/DISCOVERY_PROTOCOL.md
- {directory}/source-group-{group:02d}.md

Do not read prior synthesis outputs, other groups, conference outcomes, or Deep
analyses. Write only:
- {directory}/local-patterns-{group:02d}.json
- {directory}/local-report-{group:02d}.md

Separate genuinely recurring Atlas-external logic from Atlas boundaries,
source-insufficient wrappers, and coherent singletons. Classify by operative
inference endpoint, not topic or wording. Run `uv run python
scripts/validate_unmapped_logic_discovery.py --directory {directory} --group
{group}` and repair both outputs until validation succeeds. Return only counts,
self-reported ambiguities/discretion, retries, and output paths.
"""


def run_group(
    repo: Path, directory: Path, log_dir: Path, group: int, *, model: str,
    reasoning_effort: str, timeout_seconds: int,
) -> Result:
    started_at = now()
    started = time.monotonic()
    log_path = log_dir / f"group-{group:02d}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write", "--model", model,
        "-c", f'model_reasoning_effort="{reasoning_effort}"', "--cd", str(repo),
        prompt(directory, group),
    ]
    return_code = validation_return_code = None
    status, message = "failed", "agent failed"
    try:
        with log_path.open("w") as log:
            completed = subprocess.run(
                command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                timeout=timeout_seconds, check=False,
            )
        return_code = completed.returncode
        if return_code == 0:
            validation_return_code = subprocess.run(
                validation_command(directory, group), cwd=repo,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            ).returncode
            if validation_return_code == 0:
                status, message = "complete", "agent and validation succeeded"
            else:
                message = "agent succeeded but validation failed"
        else:
            message = f"agent exited with code {return_code}"
    except subprocess.TimeoutExpired:
        status, message = "timeout", f"agent exceeded {timeout_seconds} seconds"
    except Exception as exc:
        message = f"supervisor exception: {type(exc).__name__}: {exc}"
    return Result(
        group, reasoning_effort, status, round(time.monotonic() - started, 3),
        return_code, validation_return_code, started_at, now(),
        str(log_path.resolve()), message,
    )


def write_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=20 * 60)
    parser.add_argument("--group", type=int, action="append")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text())
    allowed = {row["group"] for row in manifest["groups"]}
    groups = args.group or sorted(allowed)
    if unknown := set(groups) - allowed:
        raise SystemExit(f"unknown groups: {sorted(unknown)}")
    state_path = args.directory / "supervisor-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"version": 1, "groups": {}}
    state["run_config"] = {
        "model": args.model, "reasoning_effort": args.reasoning_effort,
        "concurrency": args.concurrency, "timeout_seconds": args.timeout_seconds,
    }
    log_dir = args.directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pending = []
    for group in groups:
        key = str(group)
        if is_valid(repo, args.directory, group):
            state["groups"].setdefault(key, {})
            state["groups"][key].update({"status": "complete", "message": "pre-existing output validated"})
        elif state["groups"].get(key, {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(group)
    write_state(state_path, state)
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                run_group, repo, args.directory, log_dir, group, model=args.model,
                reasoning_effort=args.reasoning_effort,
                timeout_seconds=args.timeout_seconds,
            ): group
            for group in pending
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["groups"][str(result.group)] = asdict(result)
                write_state(state_path, state)
            print(
                f"group={result.group:02d} status={result.status} "
                f"elapsed={result.elapsed_seconds:.1f}s message={result.message}",
                flush=True,
            )


if __name__ == "__main__":
    main()
