"""Run fresh agents for candidate-card refinement v2 shards."""

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


DEFAULT_DIR = Path("data/analysis/iclr/episode-reclassification-3135/new-card-refinement-v2")


@dataclass(frozen=True)
class Result:
    shard: int
    status: str
    elapsed_seconds: float
    return_code: int | None
    validation_return_code: int | None
    finished_at: str
    log_path: str


def now() -> str:
    return datetime.now(UTC).isoformat()


def validation(repo: Path, directory: Path, shard: int) -> int:
    return subprocess.run(
        ["uv", "run", "python", "scripts/validate_new_card_refinement_v2.py", "--directory", str(directory), "--shard", str(shard)],
        cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode


def valid(repo: Path, directory: Path, shard: int) -> bool:
    return validation(repo, directory, shard) == 0


def prompt(directory: Path, shard: int) -> str:
    target = str(directory)
    return f"""Refine every candidate card/episode pair in shard {shard:03d} under audited v2 boundaries.

Read only {target}/REFINEMENT_PROTOCOL.md and
{target}/source-shard-{shard:03d}.jsonl. Do not read v1 decisions, other shards,
conference outcomes, challenge-audit verdicts, or prior outputs. Write only
{target}/refined-shard-{shard:03d}.jsonl and
{target}/refined-shard-{shard:03d}-report.md.

Default to exclusion when any required gate component is absent. Never invent a
remedy, task-to-design side, or intact-inspectability premise. Run uv run python
scripts/validate_new_card_refinement_v2.py --directory {target} --shard {shard}
and repair until validation succeeds. Return only counts and output paths.
"""


def run(repo: Path, directory: Path, shard: int, timeout: int) -> Result:
    started = time.monotonic()
    log = directory / "logs" / f"refined-shard-{shard:03d}.log"
    code = validation_code = None
    status = "failed"
    try:
        with log.open("w", encoding="utf-8") as handle:
            completed = subprocess.run([
                "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "workspace-write",
                "--model", "gpt-5.6-luna", "-c", 'model_reasoning_effort="medium"',
                "--cd", str(repo), prompt(directory, shard),
            ], cwd=repo, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        code = completed.returncode
        validation_code = validation(repo, directory, shard)
        if code == 0 and validation_code == 0:
            status = "complete"
    except subprocess.TimeoutExpired:
        status = "timeout"
    return Result(shard, status, round(time.monotonic() - started, 3), code, validation_code, now(), str(log.resolve()))


def write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--shard", type=int, action="append")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    all_shards = [row["shard"] for row in manifest["shards"]]
    shards = args.shard or all_shards
    state_path = args.directory / "supervisor-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"version": 1, "shards": {}}
    pending = []
    for shard in shards:
        if valid(repo, args.directory, shard):
            state["shards"].setdefault(str(shard), {})["status"] = "complete"
        elif state["shards"].get(str(shard), {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(shard)
    (args.directory / "logs").mkdir(parents=True, exist_ok=True)
    write_state(state_path, state)
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run, repo, args.directory, shard, args.timeout_seconds): shard for shard in pending}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                state["shards"][str(result.shard)] = asdict(result)
                write_state(state_path, state)
            print(f"shard={result.shard:03d} status={result.status} elapsed={result.elapsed_seconds:.1f}s", flush=True)


if __name__ == "__main__":
    main()
