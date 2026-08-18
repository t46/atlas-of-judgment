"""Run durable fresh-agent extraction for full ICLR 2026 Episode Lite shards."""

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


DEFAULT_DIRECTORY = Path("data/analysis/iclr/episode-lite-2026-full")
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT = 30 * 60


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


def output_paths(directory: Path, shard: int) -> tuple[Path, Path]:
    suffix = f"{shard:05d}"
    return (
        directory / f"episodes-shard-{suffix}.jsonl",
        directory / f"coverage-shard-{suffix}.json",
    )


def validation_command(repo: Path, directory: Path, shard: int) -> list[str]:
    return [
        "uv", "run", "python", "scripts/validate_episode_lite_2026_full.py",
        "--directory", str(directory), "--only-shard", str(shard),
    ]


def is_valid(repo: Path, directory: Path, shard: int) -> bool:
    if not all(path.exists() for path in output_paths(directory, shard)):
        return False
    result = subprocess.run(
        validation_command(repo, directory, shard),
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def agent_prompt(shard: int, review_count: int, relative_directory: Path) -> str:
    suffix = f"{shard:05d}"
    base = relative_directory.as_posix()
    return f"""Extract Episode Lite for exactly full-corpus shard {suffix} ({review_count} reviews).

Read only:
- {base}/AGENT_PROTOCOL.md
- {base}/source-shard-{suffix}.md
- {base}/source-shard-{suffix}.json
- schemas/evaluation-episode-v0.2.json

Do not read the manifest, any other shard, prior pilot output, Atlas cards,
scores, decisions, or downstream classifications. Write only:
- {base}/episodes-shard-{suffix}.jsonl
- {base}/coverage-shard-{suffix}.json

Analyze all {review_count} reviews independently. Extract every independently
warranted observation -> evaluative reason -> judgment chain; do not enforce an
episode quota. Copy IDs exactly and use actual review-specific content, not
templates or generic placeholders. Prefer canonical primary refs whenever the
memo cites L###. Do not invent requests or missing links. Do not assign Atlas
or taxonomy labels.

Before emitting JSON, enumerate each memo's independently warranted chains.
Do not compress a whole review into one episode when separate strengths,
comparisons, mechanism questions, scope limits, robustness concerns, or
presentation blockages have distinct reasoning bridges. Every abstract
signature must preserve the object class, observation, operative
standard/counterfactual, and judgment endpoint; never reuse one stock sentence
across reviews or append IDs/ordinals to fake specificity. Claims supported
only by `I-` wrapper evidence are not `reviewer_explicit`. Copy each cited
wrapper line's exact visible body into the evidence record's `text`.

Do not write a script, loop, keyword selector, or template filler to generate
semantic episode fields. Read and author each review's chains individually.
Mechanical JSON parsing and validation are allowed only after the semantic
records have been authored.

Run `uv run python scripts/validate_episode_lite_2026_full.py --only-shard {shard}`
and fix both outputs until errors and warnings are zero. Do not modify any other
file. Final response: counts and the two output paths only.
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
    directory: Path,
    log_dir: Path,
    shard: int,
    review_count: int,
    relative_directory: Path,
    *,
    model: str,
    effort: str,
    timeout_seconds: int,
) -> RunResult:
    started_at, started = now(), time.monotonic()
    log_path = log_dir / f"shard-{shard:05d}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--cd", str(repo), agent_prompt(shard, review_count, relative_directory),
    ]
    return_code = validation_return_code = None
    status, message = "failed", "agent failed"
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
                validation_command(repo, directory, shard),
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
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
    return RunResult(
        shard, status, started_at, now(), round(time.monotonic() - started, 3),
        return_code, validation_return_code, str(log_path.resolve()), message,
    )


def supervise(
    repo: Path,
    directory: Path,
    state_path: Path,
    log_dir: Path,
    shards: list[int],
    *,
    model: str,
    effort: str,
    concurrency: int,
    timeout_seconds: int,
    retry_failed: bool,
) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    relative_directory = directory.resolve().relative_to(repo.resolve())
    counts = {row["shard"]: row["review_count"] for row in manifest["shards"]}
    unknown = sorted(set(shards) - set(counts))
    if unknown:
        raise ValueError(f"shards absent from manifest: {unknown[:10]}")
    log_dir.mkdir(parents=True, exist_ok=True)
    state, lock, pending = load_state(state_path), threading.Lock(), []
    for shard in shards:
        if is_valid(repo, directory, shard):
            prior = state["shards"].get(str(shard), {})
            state["shards"][str(shard)] = {
                **prior, "status": "complete", "message": "pre-existing outputs validated", "checked_at": now(),
            }
            continue
        prior_status = state["shards"].get(str(shard), {}).get("status")
        if prior_status in {"failed", "timeout"} and not retry_failed:
            continue
        pending.append(shard)
    write_state(state_path, state)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                run_shard, repo, directory, log_dir, shard, counts[shard], relative_directory,
                model=model, effort=effort, timeout_seconds=timeout_seconds,
            ): shard
            for shard in pending
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["shards"][str(result.shard)] = asdict(result)
                write_state(state_path, state)
            print(
                f"shard={result.shard:05d} status={result.status} "
                f"elapsed={result.elapsed_seconds:.1f}s message={result.message}",
                flush=True,
            )
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--shard", type=int, action="append")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    end = args.end or manifest["shard_count"]
    shards = args.shard or list(range(args.start, end + 1))
    state = args.state or args.directory / "supervisor-state.json"
    logs = args.log_dir or args.directory / "logs"
    if args.dry_run:
        pending = [shard for shard in shards if not is_valid(repo, args.directory, shard)]
        print(json.dumps({"pending_count": len(pending), "pending_preview": pending[:20]}))
        return
    supervise(
        repo, args.directory, state, logs, shards,
        model=args.model, effort=args.effort, concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds, retry_failed=args.retry_failed,
    )


if __name__ == "__main__":
    main()
