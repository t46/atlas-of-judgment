"""Prepare an isolated retry run for incomplete Direct Qwen requests.

The source run is never modified. Retry requests retain their original
``custom_id`` so successful outputs can later be merged with an exact mapping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from scripts.qwen_review_logic_batch import (
    atomic_json,
    estimated_request_cost,
    resolve_local_refs,
)
from scripts.qwen_reviewer_logic_direct import connect, initialize_state, now


RETRIABLE = """
    status='failed' AND (
        provider_status IS NULL
        OR provider_status != 200
        OR error LIKE 'parse/validation exception:%'
    )
"""


def retry_category(provider_status: int | None, error: str | None) -> str:
    if provider_status is None:
        return "interrupted"
    if provider_status != 200:
        return "provider_non200"
    if (error or "").startswith("parse/validation exception:"):
        return "parse_or_truncation"
    raise ValueError("row does not match a supported retry category")


def selected_rows(source_run: Path) -> list[tuple[Any, ...]]:
    source_state = sqlite3.connect(
        f"file:{(source_run / 'state.sqlite3').resolve()}?mode=ro", uri=True
    )
    try:
        return source_state.execute(
            "SELECT custom_id,source_job_id,paper_id,forum_id,year,memo_chars,"
            "provider_status,error FROM requests WHERE " + RETRIABLE +
            " ORDER BY custom_id"
        ).fetchall()
    finally:
        source_state.close()


def prepare(args: argparse.Namespace) -> None:
    repo = Path(__file__).resolve().parents[1]
    source_run = args.source_run.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to replace existing retry run: {output}")
    source_manifest = json.loads(
        (source_run / "manifest.json").read_text(encoding="utf-8")
    )
    rows = selected_rows(source_run)
    if not rows:
        raise RuntimeError("source run has no retriable failed requests")

    protocol_path = source_run / "PROTOCOL.md"
    schema_path = source_run / Path(source_manifest["schema"]).name
    protocol = protocol_path.read_text(encoding="utf-8")
    schema = resolve_local_refs(json.loads(schema_path.read_text(encoding="utf-8")))
    fixed_chars = len(protocol) + len(json.dumps(schema, ensure_ascii=False)) + 2_000
    estimated_cost = 0.0
    tier_counts: dict[int, int] = {}
    categories: dict[str, int] = {}
    for row in rows:
        memo_chars, provider_status, error = row[5], row[6], row[7]
        category = retry_category(provider_status, error)
        categories[category] = categories.get(category, 0) + 1
        request_chars = fixed_chars + round(memo_chars * 1.25)
        cost, _, tier = estimated_request_cost(
            request_chars, 1, args.estimated_output_tokens_per_forum, "realtime"
        )
        estimated_cost += cost
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    if estimated_cost > args.cost_cap_usd:
        raise RuntimeError(
            f"estimated retry cost ${estimated_cost:.2f} exceeds cap ${args.cost_cap_usd:.2f}"
        )

    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    try:
        for name in ("provider", "outputs", "validations"):
            (temporary / name).mkdir()
        shutil.copyfile(protocol_path, temporary / "PROTOCOL.md")
        shutil.copyfile(schema_path, temporary / schema_path.name)
        state = connect(temporary / "state.sqlite3")
        initialize_state(state)
        state.executemany(
            "INSERT INTO requests(custom_id,source_job_id,paper_id,forum_id,year,memo_chars,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            [(*row[:6], now()) for row in rows],
        )
        state.commit()
        state.close()

        database = Path(source_manifest["database"])
        manifest = {
            "version": 1,
            "scope": "ICLR 2018-2026 Direct retry for incomplete forum outputs",
            "selection": "failed requests with interrupted, provider non-200, or incomplete JSON",
            "source_run": str(source_run),
            "source_state_sha256": hashlib.sha256(
                (source_run / "state.sqlite3").read_bytes()
            ).hexdigest(),
            "retry_categories": categories,
            "database": str(database),
            "database_size": database.stat().st_size,
            "database_mtime_ns": database.stat().st_mtime_ns,
            "protocol": str(protocol_path),
            "schema": str(schema_path),
            "model": source_manifest["model"],
            "base_url": source_manifest["base_url"],
            "enable_thinking": False,
            "structured_output": "json_schema_strict",
            "request_count": len(rows),
            "estimated_output_tokens_per_forum": args.estimated_output_tokens_per_forum,
            "max_output_tokens": args.max_output_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
            "estimated_request_input_tiers": {
                f"up_to_{limit}_tokens": count
                for limit, count in sorted(tier_counts.items())
            },
            "declared_cost_cap_usd": args.cost_cap_usd,
            "protocol_sha256": hashlib.sha256(protocol.encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "created_at": now(),
        }
        atomic_json(temporary / "manifest.json", manifest)
        temporary.replace(output)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--source-run", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--max-output-tokens", type=int, default=24_000)
    result.add_argument("--estimated-output-tokens-per-forum", type=int, default=20_000)
    result.add_argument("--cost-cap-usd", type=float, required=True)
    return result


if __name__ == "__main__":
    prepare(parser().parse_args())
