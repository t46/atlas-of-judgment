"""Run fresh Luna-low compact extraction with inline input and structured output."""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import re
import subprocess
import time
import uuid
from pathlib import Path


DEFAULT_DIR = Path("data/analysis/iclr/review-logic-compact-holdout")
BATCH_SCHEMA = Path("schemas/review-logic-compact-batch-v0.1.json")
CODEX = Path("/opt/homebrew/bin/codex")
ATOMIC_REF = re.compile(r"^([RI]-[^:]+:L)(\d+)$")
RANGE_REF = re.compile(r"^([RI]-[^:]+:L)(\d+)-L?(\d+)$")


def build_prompt(directory: Path, item: dict) -> str:
    protocol = (directory / "PROTOCOL.md").read_text(encoding="utf-8")
    source = (directory / item["source_markdown"]).read_text(encoding="utf-8")
    first_review = source.find("## Review 01:")
    if first_review >= 0:
        source = source[first_review:]
    metadata = (directory / item["source_metadata"]).read_text(encoding="utf-8")
    return f"""You are a fresh, independent executor. Do not use tools. Read the complete
protocol and source packet below, then return only the JSON object required by
the supplied output schema.

Requirements checklist fixed before execution:
1. [critical] Exactly one schema-valid record per source review, in source order.
2. [critical] Preserve every independently warranted inspected-object ->
   observation -> reasoning/standard -> judgment chain; do not collapse or use
   an implicit unit quota.
3. [critical] Every unit uses valid review-specific primary or wrapper evidence.
   A valid wrapper reference fully satisfies this requirement when primary text
   is absent; record the provenance gap in missing_links, but do not mark this
   requirement partial merely because primary text is unavailable. Every
   evidence_refs item must be one atomic `R-<review_id>:L###` or
   `I-<review_id>:L###` reference, never a line range.
4. Preserve positive, negative, conditional, and uncertain reasoning without
   inferring paper outcome or score.
5. Suggested improvements are reviewer-supported or null, never invented.
6. Text is review-specific, compact, and not produced by a shared template.

Populate self_report honestly after extracting all records. Items 1 through 6
must appear once each in order. A critical partial/fail means the task failed;
do not claim pass merely because the JSON is valid.

<protocol>
{protocol}
</protocol>

<source_metadata>
{metadata}
</source_metadata>

<source_packet>
{source}
</source_packet>
"""


def normalize_atomic_refs(payload: dict) -> None:
    """Normalize mechanical evidence-reference and provenance representation."""
    for record in payload.get("records", []):
        for unit in record.get("logic_units", []):
            normalized: list[str] = []
            for ref in unit.get("evidence_refs", []):
                match = RANGE_REF.fullmatch(ref)
                candidates = [ref]
                if match:
                    prefix, start, end = match.groups()
                    candidates = [f"{prefix}{int(start):03d}", f"{prefix}{int(end):03d}"]
                for candidate in candidates:
                    atomic = ATOMIC_REF.fullmatch(candidate)
                    if atomic:
                        prefix, line = atomic.groups()
                        candidate = f"{prefix}{int(line):03d}"
                    if candidate not in normalized:
                        normalized.append(candidate)
            unit["evidence_refs"] = normalized
            if unit.get("support_status") == "reviewer_explicit" and not any(
                ref.startswith("R-") for ref in normalized
            ):
                unit["support_status"] = "memo_inferred"
                missing = unit.setdefault("missing_links", [])
                if "primary_provenance" not in missing:
                    missing.append("primary_provenance")


def write_report(path: Path, payload: dict, elapsed: float) -> None:
    report = payload["self_report"]
    marks = {"pass": "○", "partial": "部分的", "fail": "×"}
    lines = ["# Direct structured-output execution report", "", "## 成果物", "",
             f"- records: {len(payload['records'])}", f"- elapsed_seconds: {elapsed:.3f}",
             "", "## 要件達成", ""]
    for row in report["requirements"]:
        critical = "[critical] " if row["item"] <= 3 else ""
        lines.append(f"{row['item']}. {critical}{marks[row['status']]} {row['reason']}")
    lines.extend(["", "## 不明瞭点", ""])
    lines.extend(f"- {value}" for value in report["ambiguities"] or ["なし"])
    lines.extend(["", "## 裁量補完", ""])
    lines.extend(f"- {value}" for value in report["discretionary_choices"] or ["なし"])
    lines.extend(["", "## 再試行", "", f"- {report['retries']}", ""])
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(path)


def run(repo: Path, directory: Path, item: dict) -> dict:
    scenario = item["scenario"]
    logs = directory / "logs"
    raw = directory / "raw"
    logs.mkdir(exist_ok=True)
    raw.mkdir(exist_ok=True)
    raw_output = raw / f"{scenario}.json"
    log = logs / f"{scenario}.log"
    command = [
        str(CODEX), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"',
        "--output-schema", str((repo / BATCH_SCHEMA).resolve()),
        "--output-last-message", str(raw_output.resolve()), "--cd", str(repo), "-",
    ]
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(
                command, cwd=repo, input=build_prompt(directory, item), text=True,
                stdout=stream, stderr=subprocess.STDOUT, timeout=20 * 60, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"scenario": scenario, "agent_return_code": None,
                    "validation_return_code": None,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "error": "agent timeout after 1200 seconds", "log": str(log)}
    elapsed = time.monotonic() - started
    if result.returncode != 0 or not raw_output.exists():
        return {"scenario": scenario, "agent_return_code": result.returncode,
                "validation_return_code": None, "elapsed_seconds": round(elapsed, 3),
                "log": str(log)}
    payload = json.loads(raw_output.read_text(encoding="utf-8"))
    normalize_atomic_refs(payload)
    output = directory / f"compact-{scenario}.jsonl"
    output_temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    output_temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in payload["records"]),
        encoding="utf-8",
    )
    output_temporary.replace(output)
    write_report(directory / f"compact-{scenario}-report.md", payload, elapsed)
    validation = subprocess.run(
        ["uv", "run", "python", "scripts/validate_review_logic_compact_pilot.py",
         "--directory", str(directory), "--scenario", scenario],
        cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return {"scenario": scenario, "agent_return_code": result.returncode,
            "validation_return_code": validation.returncode,
            "elapsed_seconds": round(elapsed, 3), "log": str(log)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("--workers must be positive")
    repo = Path(__file__).resolve().parents[1]
    lock_stream = (args.directory / "supervisor.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another direct compact supervisor holds the lock")
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(lambda item: run(repo, args.directory, item), manifest["scenarios"]))
    (args.directory / "supervisor-result.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2))
    if any(
        row.get("agent_return_code") != 0 or row.get("validation_return_code") != 0
        for row in results
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
