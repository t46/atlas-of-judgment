"""Run fresh Luna-low agents for compact review-logic prompt tuning."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import time
from pathlib import Path


DEFAULT_DIR = Path("data/analysis/iclr/review-logic-compact-pilot")
BLINDED_SCENARIO_NAMES = {
    "low-density": "scenario-A",
    "median-density": "scenario-B",
    "high-density": "scenario-C",
}


def prompt(directory: Path, item: dict) -> str:
    scenario = item["scenario"]
    blinded_name = BLINDED_SCENARIO_NAMES.get(scenario, "scenario")
    return f"""You are a fresh executor evaluating the compact reviewer-logic protocol.

Read only:
- {directory.as_posix()}/PROTOCOL.md
- {directory.as_posix()}/{item['source_markdown']}
- {directory.as_posix()}/{item['source_metadata']}
- schemas/review-logic-compact-v0.1.json

Scenario: {blinded_name}; {item['review_count']} source reviews. The source was selected
only to exercise the protocol. Its label conveys nothing about expected unit count.
Do not read any Episode Lite output, reference
count, other scenario, Atlas artifact, score table, or decision data.

Write only:
- {directory.as_posix()}/compact-{scenario}.jsonl
- {directory.as_posix()}/compact-{scenario}-report.md

Requirements checklist fixed before execution:
1. [critical] Exactly one schema-valid record per source review, in source order.
2. [critical] Preserve every independently warranted inspected-object ->
   observation -> reasoning/standard -> judgment chain; do not collapse the
   review into one summary unit.
3. [critical] Every unit uses valid review-specific primary or wrapper evidence.
4. Preserve positive, negative, conditional, and uncertain reasoning without
   inferring paper outcome or score.
5. Suggested improvements are reviewer-supported or null, never invented.
6. Text is review-specific, compact, and not produced by a shared template.

Run `uv run python scripts/validate_review_logic_compact_pilot.py --directory
{directory.as_posix()} --scenario {scenario}` and fix errors/warnings.
Fix local validation defects in place. Never reduce the number of covered
endpoints merely to obtain a passing validator. If any [critical] item would be
partial or failed, the task is not complete even if the validator exits zero.

The report Markdown must contain:
- 成果物
- 要件達成: each item marked ○ / × / 部分的 with reason
- 不明瞭点
- 裁量補完
- 再試行

Do not modify any other file. Final response reports record/unit counts,
validator result, ambiguities, discretionary choices, and retries.
"""


def run(repo: Path, directory: Path, item: dict) -> dict:
    scenario = item["scenario"]
    log = directory / "logs" / f"{scenario}.log"
    started = time.monotonic()
    command = [
        "codex", "exec", "--enable", "code_mode_host", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--model", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"',
        "--cd", str(repo), prompt(directory, item),
    ]
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=repo, stdout=stream, stderr=subprocess.STDOUT, timeout=20 * 60, check=False)
    validation = subprocess.run(
        ["uv", "run", "python", "scripts/validate_review_logic_compact_pilot.py", "--directory", str(directory), "--scenario", scenario],
        cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return {"scenario": scenario, "agent_return_code": result.returncode, "validation_return_code": validation.returncode, "elapsed_seconds": round(time.monotonic() - started, 3), "log": str(log)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    directory = args.directory
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    (directory / "logs").mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda item: run(repo, directory, item), manifest["scenarios"]))
    (directory / "supervisor-result.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
