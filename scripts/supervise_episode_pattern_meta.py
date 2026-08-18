"""Run one fresh Luna-low agent for each meta-pattern packet."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_DIR = Path("data/analysis/iclr/episode-lite-1000/synthesis")


@dataclass(frozen=True)
class Result:
    meta_group: int
    status: str
    elapsed_seconds: float
    return_code: int | None
    validation_return_code: int | None
    log_path: str
    message: str


def prompt(meta_group: int) -> str:
    suffix = f"{meta_group:02d}"
    return f"""Merge group-level reviewer-evaluation patterns for meta group {suffix}.

Read only:
- data/analysis/iclr/episode-lite-1000/synthesis/META_PROTOCOL.md
- data/analysis/iclr/episode-lite-1000/synthesis/meta-source-{suffix}.md

Write only `meta-patterns-{suffix}.json` and `meta-report-{suffix}.md` in the
same synthesis directory. Do not read other meta outputs or raw sources. Merge
only homologous evaluation logic, retain variants and counterexamples, and map
every source pattern. Run `uv run python
scripts/validate_episode_pattern_meta.py --meta-group {meta_group}` and repair
until it succeeds. Final response: counts and output paths only.
"""


def run(repo: Path, directory: Path, meta_group: int, model: str, timeout: int) -> Result:
    started = time.monotonic()
    log_dir = directory / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"meta-{meta_group:02d}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write", "--model", model,
        "-c", 'model_reasoning_effort="low"', "--cd", str(repo), prompt(meta_group),
    ]
    return_code = None
    validation_return_code = None
    status = "failed"
    message = "agent failed"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return_code = result.returncode
        if return_code == 0:
            validation = subprocess.run(
                ["uv", "run", "python", "scripts/validate_episode_pattern_meta.py", "--directory", str(directory), "--meta-group", str(meta_group)],
                cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
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
        message = f"agent exceeded {timeout} seconds"
    except Exception as exc:
        message = f"supervisor exception: {type(exc).__name__}: {exc}"
    return Result(meta_group, status, round(time.monotonic() - started, 3), return_code, validation_return_code, str(log_path.resolve()), message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=20 * 60)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((args.directory / "meta-manifest.json").read_text(encoding="utf-8"))
    state_path = args.directory / "meta-supervisor-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"version": 1, "meta_groups": {}}
    pending = []
    for group in range(1, manifest["meta_group_count"] + 1):
        validation = subprocess.run(
            ["uv", "run", "python", "scripts/validate_episode_pattern_meta.py", "--directory", str(args.directory), "--meta-group", str(group)],
            cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ) if (args.directory / f"meta-patterns-{group:02d}.json").exists() else None
        if validation is not None and validation.returncode == 0:
            state["meta_groups"].setdefault(str(group), {}).update({"status": "complete", "message": "pre-existing outputs validated"})
        elif state["meta_groups"].get(str(group), {}).get("status") in {"failed", "timeout"} and not args.retry_failed:
            continue
        else:
            pending.append(group)
    if args.dry_run:
        print(json.dumps({"pending": pending, "count": len(pending)}))
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(run, repo, args.directory, group, args.model, args.timeout_seconds): group for group in pending}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            state["meta_groups"][str(result.meta_group)] = asdict(result)
            state["updated_at"] = datetime.now(UTC).isoformat()
            temporary = state_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            temporary.replace(state_path)
            print(f"meta={result.meta_group:02d} status={result.status} elapsed={result.elapsed_seconds:.1f}s message={result.message}", flush=True)


if __name__ == "__main__":
    main()
