"""Run fresh skeptical auditors for the candidate new cards."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_DIR = Path("data/analysis/iclr/episode-reclassification-3135/new-card-challenge-audit")


def job_prompt(directory: Path, card: str, auditor: str) -> str:
    target = str(directory)
    return f"""Conduct a skeptical challenge audit for candidate card {card}.

Read only:
- {target}/PROTOCOL-{card}.md
- {target}/challenge-{card}.jsonl

Do not read other auditors, other card packets, conference outcomes, or summary
counts. Write only:
- {target}/audit-{card}-{auditor}.jsonl
- {target}/audit-{card}-{auditor}-report.md

Attempt to falsify every proposed membership and distinguish genuine multi-label
logic from collision with an existing card. Run uv run python
scripts/validate_new_card_challenge_audit.py --directory {target} --card {card}
--auditor {auditor} and repair until validation succeeds. Return only verdict
counts, candidate-level recommendation, and output paths.
"""


def run(repo: Path, directory: Path, card: str, auditor: str, timeout: int) -> dict[str, object]:
    started = time.monotonic()
    log = directory / f"audit-{card}-{auditor}.log"
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", "gpt-5.6-luna", "-c", 'model_reasoning_effort="medium"',
        "--cd", str(repo), job_prompt(directory, card, auditor),
    ]
    status, code = "failed", None
    try:
        with log.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(command, cwd=repo, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        code = completed.returncode
        validation = subprocess.run(
            ["uv", "run", "python", "scripts/validate_new_card_challenge_audit.py", "--directory", str(directory), "--card", card, "--auditor", auditor],
            cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if code == 0 and validation.returncode == 0:
            status = "complete"
    except subprocess.TimeoutExpired:
        status = "timeout"
    return {
        "card": card, "auditor": auditor, "status": status,
        "return_code": code, "elapsed_seconds": round(time.monotonic() - started, 3),
        "finished_at": datetime.now(UTC).isoformat(), "log": str(log.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    jobs = [("N-P01", "a"), ("N-P02", "a"), ("N-P02", "b"), ("N-P03", "a")]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run, repo, args.directory, card, auditor, args.timeout_seconds): (card, auditor) for card, auditor in jobs}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            (args.directory / "supervisor-state.json").write_text(json.dumps({"results": results}, indent=2) + "\n")
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
