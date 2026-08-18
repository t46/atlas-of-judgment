"""Run one fresh Luna-low agent for global selected-Deep Atlas adjudication."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_DIR = Path("data/analysis/iclr/episode-deep-63/pattern-challenges")
DEFAULT_MODEL = "gpt-5.6-luna"


def now() -> str:
    return datetime.now(UTC).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout-seconds", type=int, default=20*60)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output_json = args.directory / "atlas-adjudication.json"
    output_report = args.directory / "atlas-adjudication-report.md"
    validation = ["uv", "run", "python", "scripts/validate_episode_atlas_adjudication.py", "--directory", str(args.directory)]
    if not args.force and output_json.exists() and output_report.exists():
        if subprocess.run(validation, cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
            print(json.dumps({"status": "complete", "message": "pre-existing outputs validated"}))
            return
    prompt = """Globally adjudicate the provisional Evaluation Logic Atlas using ten independent selected-Deep pattern challenges.

Read only:
- data/analysis/iclr/episode-deep-63/pattern-challenges/ATLAS_ADJUDICATION_PROTOCOL.md
- data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication-source.md

Write only:
- data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication.json
- data/analysis/iclr/episode-deep-63/pattern-challenges/atlas-adjudication-report.md

Reconcile disagreements rather than voting. Do not infer prevalence or outcome
effects. Mark any split/merge as pending full-corpus reclassification. Run
`uv run python scripts/validate_episode_atlas_adjudication.py` and repair both
files until validation succeeds. Return only disposition/decision counts and
output paths.
"""
    log_dir = args.directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "atlas-adjudication.log"
    started_at = now()
    started = time.monotonic()
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", args.model, "-c", 'model_reasoning_effort="low"',
        "--cd", str(repo), prompt,
    ]
    status = "failed"
    message = "agent failed"
    return_code = None
    validation_code = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout_seconds, check=False)
        return_code = completed.returncode
        if return_code == 0:
            validation_run = subprocess.run(validation, cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            validation_code = validation_run.returncode
            if validation_code == 0:
                status = "complete"
                message = "agent and validation succeeded"
            else:
                message = "agent exited successfully but validation failed"
        else:
            message = f"agent exited with code {return_code}"
    except subprocess.TimeoutExpired:
        status = "timeout"
        message = f"agent exceeded {args.timeout_seconds} seconds"
    except Exception as exc:
        message = f"runner exception: {type(exc).__name__}: {exc}"
    state = {
        "version": 1,
        "status": status,
        "started_at": started_at,
        "finished_at": now(),
        "elapsed_seconds": round(time.monotonic()-started, 3),
        "return_code": return_code,
        "validation_return_code": validation_code,
        "log_path": str(log_path.resolve()),
        "message": message,
    }
    (args.directory / "atlas-adjudication-state.json").write_text(json.dumps(state, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(state))
    raise SystemExit(0 if status == "complete" else 1)


if __name__ == "__main__":
    main()
