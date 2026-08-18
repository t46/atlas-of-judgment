"""Run one fresh Luna-low agent per provisional Atlas pattern challenge."""

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


DEFAULT_DIR = Path("data/analysis/iclr/episode-deep-63/pattern-challenges")
DEFAULT_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True)
class Result:
    pattern_id: str
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


def outputs(directory: Path, pattern_id: str) -> tuple[Path, Path]:
    return (
        directory / f"pattern-challenge-{pattern_id}.json",
        directory / f"pattern-challenge-{pattern_id}-report.md",
    )


def validation_command(directory: Path, pattern_id: str) -> list[str]:
    return [
        "uv", "run", "python", "scripts/validate_episode_pattern_challenges.py",
        "--directory", str(directory), "--pattern", pattern_id,
    ]


def is_valid(repo: Path, directory: Path, pattern_id: str) -> bool:
    if not all(path.exists() for path in outputs(directory, pattern_id)):
        return False
    return subprocess.run(
        validation_command(directory, pattern_id), cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def prompt(pattern_id: str) -> str:
    return f"""Adversarially challenge provisional Atlas pattern {pattern_id} using selected Episode Deep evidence.

Read only:
- data/analysis/iclr/episode-deep-63/pattern-challenges/PATTERN_CHALLENGE_PROTOCOL.md
- data/analysis/iclr/episode-deep-63/pattern-challenges/pattern-source-{pattern_id}.md

Do not read other pattern sources, challenges, final synthesis, or conference
outcomes. Write only:
- data/analysis/iclr/episode-deep-63/pattern-challenges/pattern-challenge-{pattern_id}.json
- data/analysis/iclr/episode-deep-63/pattern-challenges/pattern-challenge-{pattern_id}-report.md

Try to falsify the pattern boundary before preserving it. Distinguish the
operative standard and inference from superficial request similarity. Run
`uv run python scripts/validate_episode_pattern_challenges.py --pattern
{pattern_id}` and repair both files until validation succeeds. Your final
response should contain only status/verdict counts and output paths.
"""


def run_pattern(repo: Path, directory: Path, log_dir: Path, pattern_id: str, *, model: str, timeout_seconds: int) -> Result:
    started_at = now()
    started = time.monotonic()
    log_path = log_dir / f"pattern-challenge-{pattern_id}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", model, "-c", 'model_reasoning_effort="low"',
        "--cd", str(repo), prompt(pattern_id),
    ]
    return_code: int | None = None
    validation_return_code: int | None = None
    status = "failed"
    message = "agent failed"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT, timeout=timeout_seconds, check=False)
        return_code = completed.returncode
        if return_code == 0:
            validation = subprocess.run(validation_command(directory, pattern_id), cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
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
    return Result(pattern_id, status, started_at, now(), round(time.monotonic()-started, 3), return_code, validation_return_code, str(log_path.resolve()), message)


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
    parser.add_argument("--timeout-seconds", type=int, default=20*60)
    parser.add_argument("--pattern", action="append")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    all_patterns = [row["pattern_id"] for row in manifest["patterns"]]
    patterns = args.pattern or all_patterns
    unknown = set(patterns) - set(all_patterns)
    if unknown:
        raise SystemExit(f"unknown patterns: {sorted(unknown)}")
    state_path = args.directory / "supervisor-state.json"
    log_dir = args.directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"version": 1, "patterns": {}}
    pending = []
    for pattern_id in patterns:
        if is_valid(repo, args.directory, pattern_id):
            state["patterns"].setdefault(pattern_id, {})
            state["patterns"][pattern_id].update({"status": "complete", "message": "pre-existing outputs validated", "checked_at": now()})
        elif state["patterns"].get(pattern_id, {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(pattern_id)
    if args.dry_run:
        print(json.dumps({"pending": pending, "count": len(pending)}))
        return
    write_state(state_path, state)
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(run_pattern, repo, args.directory, log_dir, pattern_id, model=args.model, timeout_seconds=args.timeout_seconds): pattern_id
            for pattern_id in pending
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["patterns"][result.pattern_id] = asdict(result)
                write_state(state_path, state)
            print(f"pattern={result.pattern_id} status={result.status} elapsed={result.elapsed_seconds:.1f}s message={result.message}", flush=True)


if __name__ == "__main__":
    main()
