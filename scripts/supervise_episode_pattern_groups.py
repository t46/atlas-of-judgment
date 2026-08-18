"""Run one fresh Luna-low Codex agent per Episode Lite synthesis group."""

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


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")
DEFAULT_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True)
class Result:
    group: int
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


def output_paths(directory: Path, group: int) -> tuple[Path, Path]:
    return (
        directory / f"group-patterns-{group:02d}.json",
        directory / f"group-report-{group:02d}.md",
    )


def validation_command(directory: Path, group: int) -> list[str]:
    return [
        "uv", "run", "python", "scripts/validate_episode_pattern_group.py",
        "--directory", str(directory), "--group", str(group),
    ]


def is_valid(repo: Path, directory: Path, group: int) -> bool:
    if not all(path.exists() for path in output_paths(directory, group)):
        return False
    result = subprocess.run(
        validation_command(directory, group), cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def prompt(group: int) -> str:
    suffix = f"{group:02d}"
    return f"""Synthesize provisional reviewer-evaluation logic patterns for group {suffix}.

Read only:
- data/analysis/iclr/episode-lite-1000/synthesis/GROUP_PROTOCOL.md
- data/analysis/iclr/episode-lite-1000/synthesis/group-source-{suffix}.md

Do not read any other group source or output. Write only:
- data/analysis/iclr/episode-lite-1000/synthesis/group-patterns-{suffix}.json
- data/analysis/iclr/episode-lite-1000/synthesis/group-report-{suffix}.md

Work inductively from the Episode Lite chains. Preserve warrants,
counterfactuals, missing links, contrasts, and outliers; do not organize by
research topic or outcome. Membership must cover every source episode according
to the protocol. Run `uv run python scripts/validate_episode_pattern_group.py
--group {group}` and repair both files until validation succeeds. Your final
response should contain only pattern/coverage counts and output paths.
"""


def run_group(
    repo: Path,
    directory: Path,
    log_dir: Path,
    group: int,
    *,
    model: str,
    timeout_seconds: int,
) -> Result:
    started_at = now()
    started = time.monotonic()
    log_path = log_dir / f"group-{group:02d}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", model, "-c", 'model_reasoning_effort="low"',
        "--cd", str(repo), prompt(group),
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
                validation_command(directory, group), cwd=repo,
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
        group, status, started_at, now(), round(time.monotonic() - started, 3),
        return_code, validation_return_code, str(log_path.resolve()), message,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=20 * 60)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    groups = list(range(1, manifest["group_count"] + 1))
    state_path = args.directory / "group-supervisor-state.json"
    log_dir = args.directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists() else {"version": 1, "groups": {}}
    )
    pending = []
    for group in groups:
        if is_valid(repo, args.directory, group):
            state["groups"].setdefault(str(group), {})
            state["groups"][str(group)].update(
                {"status": "complete", "message": "pre-existing outputs validated", "checked_at": now()}
            )
        elif state["groups"].get(str(group), {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(group)
    if args.dry_run:
        print(json.dumps({"pending": pending, "count": len(pending)}))
        return

    state["updated_at"] = now()
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                run_group, repo, args.directory, log_dir, group,
                model=args.model, timeout_seconds=args.timeout_seconds,
            ): group
            for group in pending
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["groups"][str(result.group)] = asdict(result)
                state["updated_at"] = now()
                temporary = state_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
                temporary.replace(state_path)
            print(
                f"group={result.group:02d} status={result.status} "
                f"elapsed={result.elapsed_seconds:.1f}s message={result.message}",
                flush=True,
            )


if __name__ == "__main__":
    main()
