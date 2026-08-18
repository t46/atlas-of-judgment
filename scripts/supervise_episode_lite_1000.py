"""Run fresh Codex agents for Episode Lite shards with durable supervision.

Every shard is handled by a new ephemeral ``codex exec`` process.  Successful
outputs are validated and skipped on resume.  Failed or timed-out shards are
recorded and are not retried automatically, matching the project's policy of
handling failures in a later explicit pass.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_PILOT_DIR = Path("data/analysis/iclr/episode-lite-1000")
DEFAULT_STATE = DEFAULT_PILOT_DIR / "supervisor-state.json"
DEFAULT_LOG_DIR = DEFAULT_PILOT_DIR / "logs"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT_SECONDS = 20 * 60


@dataclass(frozen=True)
class RunResult:
    shard: int
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


def output_paths(pilot_dir: Path, shard: int) -> tuple[Path, Path, Path]:
    suffix = f"{shard:02d}"
    return (
        pilot_dir / f"episodes-shard-{suffix}.jsonl",
        pilot_dir / f"coverage-shard-{suffix}.json",
        pilot_dir / f"patterns-shard-{suffix}.md",
    )


def validation_command(repo: Path, pilot_dir: Path, shard: int) -> list[str]:
    return [
        "uv",
        "run",
        "python",
        "scripts/validate_episode_lite_1000.py",
        "--pilot-dir",
        str(pilot_dir),
        "--only-shard",
        str(shard),
    ]


def is_valid(repo: Path, pilot_dir: Path, shard: int) -> bool:
    if not all(path.exists() for path in output_paths(pilot_dir, shard)):
        return False
    result = subprocess.run(
        validation_command(repo, pilot_dir, shard),
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def agent_prompt(shard: int) -> str:
    suffix = f"{shard:02d}"
    return f"""Process exactly shard {suffix} of the ICLR Episode Lite pilot.

Read only:
- data/analysis/iclr/episode-lite-1000/AGENT_PROTOCOL.md
- data/analysis/iclr/episode-lite-1000/source-shard-{suffix}.md
- schemas/evaluation-episode-v0.2.json

Do not read any other shard output, coverage file, or pattern report. Write only:
- data/analysis/iclr/episode-lite-1000/episodes-shard-{suffix}.jsonl
- data/analysis/iclr/episode-lite-1000/coverage-shard-{suffix}.json
- data/analysis/iclr/episode-lite-1000/patterns-shard-{suffix}.md

Analyze all five reviews independently. Extract each independently warranted
evaluation-logic chain; do not default to one episode per review (2–6 is
typical). Copy paper and review IDs exactly. Prefer canonical primary evidence
whenever the memo cites L###. Every inspected object, observation, reasoning
bridge, request, and signature must name the concrete method component,
experiment, comparison, quantity, claim, or failure mode stated in this review.
Do not write placeholders such as "the concrete issue described in the memo",
"the paper material relevant to this concern", "this observation bears on the
adequacy of the claim", or "address the stated issue". Rephrasing those generic
templates is also invalid: reconstruct the actual observation -> standard ->
judgment logic from the cited lines. Do not reuse an identical claim or
signature sentence across three or more reviews; that indicates the sentence is
not review-specific enough. Do not manufacture a requested change when the
reviewer made none. Do not infer final outcomes from decision metadata.

Run `uv run python scripts/validate_episode_lite_1000.py --only-shard {shard}`
and fix the three files until errors and warnings are both zero. Do not modify
any other file. Your final response should only report counts and output paths.
"""


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": now(), "shards": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_shard(
    repo: Path,
    pilot_dir: Path,
    log_dir: Path,
    shard: int,
    *,
    model: str,
    timeout_seconds: int,
) -> RunResult:
    started_at = now()
    started = time.monotonic()
    log_path = log_dir / f"shard-{shard:02d}.log"
    command = [
        "codex",
        "exec",
        "--enable",
        "code_mode_host",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "-c",
        'model_reasoning_effort="low"',
        "--cd",
        str(repo),
        agent_prompt(shard),
    ]
    return_code: int | None = None
    validation_return_code: int | None = None
    status = "failed"
    message = "agent failed"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=repo,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
        return_code = completed.returncode
        if return_code == 0:
            validation = subprocess.run(
                validation_command(repo, pilot_dir, shard),
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
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
    except Exception as exc:  # durable record for unexpected orchestration failures
        message = f"supervisor exception: {type(exc).__name__}: {exc}"
    return RunResult(
        shard=shard,
        status=status,
        started_at=started_at,
        finished_at=now(),
        elapsed_seconds=round(time.monotonic() - started, 3),
        return_code=return_code,
        validation_return_code=validation_return_code,
        log_path=str(log_path.resolve()),
        message=message,
    )


def supervise(
    repo: Path,
    pilot_dir: Path,
    state_path: Path,
    log_dir: Path,
    shards: list[int],
    *,
    model: str,
    concurrency: int,
    timeout_seconds: int,
    retry_failed: bool,
) -> dict[str, Any]:
    pilot_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(state_path)
    lock = threading.Lock()
    pending = []
    for shard in shards:
        if is_valid(repo, pilot_dir, shard):
            prior = state["shards"].get(str(shard), {})
            state["shards"][str(shard)] = {
                **prior,
                "status": "complete",
                "message": "pre-existing outputs validated",
                "checked_at": now(),
            }
            continue
        prior_status = state["shards"].get(str(shard), {}).get("status")
        if prior_status in {"failed", "timeout"} and not retry_failed:
            continue
        pending.append(shard)
    write_state(state_path, state)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_shard = {
            executor.submit(
                run_shard,
                repo,
                pilot_dir,
                log_dir,
                shard,
                model=model,
                timeout_seconds=timeout_seconds,
            ): shard
            for shard in pending
        }
        for future in concurrent.futures.as_completed(future_to_shard):
            result = future.result()
            with lock:
                state["shards"][str(result.shard)] = result.__dict__
                write_state(state_path, state)
            print(
                f"shard={result.shard:02d} status={result.status} "
                f"elapsed={result.elapsed_seconds:.1f}s message={result.message}",
                flush=True,
            )
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=200)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    shards = list(range(args.start, args.end + 1))
    if args.dry_run:
        pending = [
            shard for shard in shards if not is_valid(repo, args.pilot_dir, shard)
        ]
        print(json.dumps({"pending": pending, "count": len(pending)}))
        return
    supervise(
        repo,
        args.pilot_dir,
        args.state,
        args.log_dir,
        shards,
        model=args.model,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        retry_failed=args.retry_failed,
    )


if __name__ == "__main__":
    main()
