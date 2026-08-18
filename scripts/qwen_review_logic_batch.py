"""Durable Qwen Batch API runner for compact reviewer-logic extraction.

The runner intentionally keeps Qwen artifacts separate from the earlier Luna
run.  It has four explicit phases: prepare local JSONL, submit, synchronize,
and compare against an optional reference run.  Failed requests are retained
for later inspection and are never retried automatically.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from openai import OpenAI

try:
    from scripts.run_review_logic_compact_2026 import (
        SOURCE_REF,
        build_prompt,
        normalize_provenance,
        validate_payload,
    )
    from scripts.supervise_review_logic_compact_direct import (
        normalize_atomic_refs,
        write_report,
    )
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from run_review_logic_compact_2026 import (
        SOURCE_REF,
        build_prompt,
        normalize_provenance,
        validate_payload,
    )
    from supervise_review_logic_compact_direct import (
        normalize_atomic_refs,
        write_report,
    )


DEFAULT_SOURCE = Path("data/analysis/iclr/episode-lite-2026-full-v3")
DEFAULT_PROTOCOL = Path("data/analysis/iclr/review-logic-compact-2026/PROTOCOL.md")
DEFAULT_REFERENCE = Path("data/analysis/iclr/review-logic-compact-2026")
# Batch accepts the rolling Flash alias; the dated 2026-07-15 snapshot works
# for real-time calls but is rejected during Batch file validation.
DEFAULT_MODEL = "qwen3.7-flash"
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
BATCH_SCHEMA = Path("schemas/review-logic-compact-batch-v0.1.json")
RECORD_SCHEMA = Path("schemas/review-logic-compact-v0.1.json")
MAX_REQUESTS_PER_FILE = 50_000
MAX_BYTES_PER_FILE = 450 * 1024 * 1024  # headroom below Qwen's 500 MB limit
# Batch prices are 50% of qwen3.7-flash real-time list prices.  The tier is
# selected from each request's input length, not from the whole batch.
BATCH_PRICE_TIERS = (
    (32_000, 0.015, 0.065),
    (256_000, 0.050, 0.200),
    (1_000_000, 0.100, 0.400),
)
REALTIME_PRICE_TIERS = tuple(
    (limit, input_price * 2, output_price * 2)
    for limit, input_price, output_price in BATCH_PRICE_TIERS
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def resolve_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local $defs references for broad provider compatibility."""
    definitions = schema.get("$defs", {})

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if reference:
            prefix = "#/$defs/"
            if not reference.startswith(prefix):
                raise ValueError(f"unsupported non-local schema reference: {reference}")
            name = reference[len(prefix):]
            if name not in definitions:
                raise KeyError(f"missing schema definition: {name}")
            expanded = copy.deepcopy(definitions[name])
            expanded.update({key: item for key, item in value.items() if key != "$ref"})
            return visit(expanded)
        return {
            key: visit(item)
            for key, item in value.items()
            if key not in {"$schema", "$id", "$defs"}
        }

    return visit(schema)


def qwen_request(prompt: str, model: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "enable_thinking": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "review_logic_compact_batch",
                    "description": "Compact evidence-grounded reviewer evaluation logic records",
                    "schema": schema,
                    "strict": True,
                },
            },
        },
    }


def constrain_record_count(schema: dict[str, Any], review_count: int) -> dict[str, Any]:
    """Make source coverage a provider-enforced structural constraint."""
    constrained = copy.deepcopy(schema)
    records = constrained["properties"]["records"]
    records["minItems"] = review_count
    records["maxItems"] = review_count
    return constrained


def estimated_request_cost(
    prompt_chars: int,
    review_count: int,
    output_tokens_per_review: int,
    billing_mode: str = "batch",
) -> tuple[float, int, int]:
    """Conservative tier-aware estimate using four characters per token."""
    input_tokens = (prompt_chars + 3) // 4
    output_tokens = review_count * output_tokens_per_review
    tiers = BATCH_PRICE_TIERS if billing_mode == "batch" else REALTIME_PRICE_TIERS
    for limit, input_price, output_price in tiers:
        if input_tokens <= limit:
            return (
                (input_tokens * input_price + output_tokens * output_price) / 1_000_000,
                input_tokens,
                limit,
            )
    raise ValueError(f"estimated request input exceeds the 1M context: {input_tokens}")


def actual_request_cost(
    prompt_tokens: int,
    completion_tokens: int,
    billing_mode: str = "batch",
) -> float:
    tiers = BATCH_PRICE_TIERS if billing_mode == "batch" else REALTIME_PRICE_TIERS
    for limit, input_price, output_price in tiers:
        if prompt_tokens <= limit:
            return (
                prompt_tokens * input_price + completion_tokens * output_price
            ) / 1_000_000
    raise ValueError(f"provider request input exceeds the 1M context: {prompt_tokens}")


def connect_state(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_state(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE requests (
            custom_id TEXT PRIMARY KEY,
            shard INTEGER NOT NULL UNIQUE,
            review_count INTEGER NOT NULL,
            prompt_chars INTEGER NOT NULL,
            input_file INTEGER NOT NULL,
            line_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'prepared',
            provider_status INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            error TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE batch_files (
            input_file INTEGER PRIMARY KEY,
            local_path TEXT NOT NULL UNIQUE,
            request_count INTEGER NOT NULL,
            review_count INTEGER NOT NULL,
            byte_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            remote_file_id TEXT,
            batch_id TEXT,
            output_file_id TEXT,
            error_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'prepared',
            error TEXT,
            updated_at TEXT NOT NULL
        );
    """)
    connection.commit()


def source_shards(source_manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(row["shard"]): row for row in source_manifest["shards"]}


def stratified_reference_shards(reference: Path, target_reviews: int) -> list[int]:
    """Select completed Luna shards across the memo-density range."""
    database = reference / "state.sqlite3"
    if not database.exists():
        raise FileNotFoundError(f"reference state is missing: {database}")
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT shard,review_count,memo_chars FROM shards "
        "WHERE status='complete' ORDER BY memo_chars,shard"
    ).fetchall()
    connection.close()
    if not rows:
        raise RuntimeError("reference run has no completed shards")

    # Walk evenly across the sorted density distribution, then add nearby
    # unselected rows until the requested review count is met.
    expected_shards = min(len(rows), max(1, (target_reviews + 7) // 8))
    if expected_shards == 1:
        selected_indices = [len(rows) // 2]
    else:
        selected_indices = [
            round(index * (len(rows) - 1) / (expected_shards - 1))
            for index in range(expected_shards)
        ]
    selected = {int(index) for index in selected_indices}
    for index in range(len(rows)):
        if sum(rows[position][1] for position in selected) >= target_reviews:
            break
        selected.add(index)
    return [int(rows[index][0]) for index in sorted(selected)]


def write_input_files(
    temporary: Path,
    source: Path,
    protocol: str,
    selected_shards: Iterable[int],
    model: str,
    provider_schema: dict[str, Any],
    connection: sqlite3.Connection,
    max_file_bytes: int,
    output_tokens_per_review: int,
    billing_mode: str,
) -> tuple[list[dict[str, Any]], int, int, int, float, dict[int, int]]:
    inputs = temporary / "inputs"
    inputs.mkdir()
    source_rows = source_shards(
        json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    )
    file_rows: list[dict[str, Any]] = []
    stream = None
    file_index = 0
    request_count = file_bytes = file_reviews = line_number = 0
    total_prompt_chars = total_reviews = 0
    total_request_body_chars = 0
    estimated_cost = 0.0
    tier_counts: dict[int, int] = {}
    current_path: Path | None = None

    def close_file() -> None:
        nonlocal stream, request_count, file_bytes, file_reviews, line_number
        if stream is None or current_path is None:
            return
        stream.close()
        file_rows.append({
            "input_file": file_index,
            "local_path": str(current_path.relative_to(temporary)),
            "request_count": request_count,
            "review_count": file_reviews,
            "byte_count": file_bytes,
            "sha256": digest(current_path),
        })
        stream = None
        request_count = file_bytes = file_reviews = line_number = 0

    for shard in selected_shards:
        if shard not in source_rows:
            raise KeyError(f"source manifest has no shard {shard}")
        suffix = f"{shard:05d}"
        source_text = (source / f"source-shard-{suffix}.md").read_text(encoding="utf-8")
        metadata_text = (source / f"source-shard-{suffix}.json").read_text(encoding="utf-8")
        metadata = json.loads(metadata_text)
        prompt = build_prompt(protocol, source_text, metadata_text)
        custom_id = f"shard-{suffix}"
        reviews = len(metadata["reviews"])
        request_schema = constrain_record_count(provider_schema, reviews)
        request = {"custom_id": custom_id, **qwen_request(prompt, model, request_schema)}
        request_body_chars = len(json.dumps(
            request["body"], ensure_ascii=False, separators=(",", ":")
        ))
        encoded = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > 1024 * 1024:
            raise ValueError(f"{custom_id} exceeds Qwen's 1 MB per-line limit")
        if stream is None or (
            request_count > 0
            and (request_count >= MAX_REQUESTS_PER_FILE or file_bytes + len(encoded) > max_file_bytes)
        ):
            close_file()
            file_index += 1
            current_path = inputs / f"batch-{file_index:05d}.jsonl"
            stream = current_path.open("wb")
        assert stream is not None
        stream.write(encoded)
        line_number += 1
        request_count += 1
        file_bytes += len(encoded)
        file_reviews += reviews
        total_reviews += reviews
        total_prompt_chars += len(prompt)
        total_request_body_chars += request_body_chars
        request_cost, _, tier = estimated_request_cost(
            request_body_chars, reviews, output_tokens_per_review, billing_mode
        )
        estimated_cost += request_cost
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        connection.execute(
            "INSERT INTO requests(custom_id,shard,review_count,prompt_chars,input_file,line_number,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (custom_id, shard, reviews, len(prompt), file_index, line_number, now()),
        )
    close_file()
    connection.executemany(
        "INSERT INTO batch_files(input_file,local_path,request_count,review_count,byte_count,sha256,updated_at) "
        "VALUES(:input_file,:local_path,:request_count,:review_count,:byte_count,:sha256,:updated_at)",
        [{**row, "updated_at": now()} for row in file_rows],
    )
    connection.commit()
    return (
        file_rows, total_prompt_chars, total_request_body_chars, total_reviews,
        estimated_cost, tier_counts,
    )


def prepare(args: argparse.Namespace) -> None:
    repo = Path(__file__).resolve().parents[1]
    source = (repo / args.source).resolve() if not args.source.is_absolute() else args.source
    output = (repo / args.output).resolve() if not args.output.is_absolute() else args.output
    protocol_path = (
        (repo / args.protocol).resolve() if not args.protocol.is_absolute() else args.protocol
    )
    reference = (
        (repo / args.reference).resolve() if not args.reference.is_absolute() else args.reference
    )
    if output.exists():
        raise FileExistsError(f"refusing to replace existing run: {output}")
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    all_shards = sorted(source_shards(source_manifest))
    if args.pilot_reviews:
        selected = stratified_reference_shards(reference, args.pilot_reviews)
        selection = f"stratified completed Luna reference, target {args.pilot_reviews} reviews"
    else:
        selected = all_shards[: args.max_shards] if args.max_shards else all_shards
        selection = "all source shards" if not args.max_shards else f"first {args.max_shards} shards"

    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    try:
        for name in ("provider", "raw", "outputs", "reports", "validations"):
            (temporary / name).mkdir()
        protocol = protocol_path.read_text(encoding="utf-8")
        (temporary / "PROTOCOL.md").write_text(protocol, encoding="utf-8")
        shutil.copyfile(repo / BATCH_SCHEMA, temporary / BATCH_SCHEMA.name)
        state = connect_state(temporary / "state.sqlite3")
        initialize_state(state)
        raw_schema = json.loads((repo / BATCH_SCHEMA).read_text(encoding="utf-8"))
        provider_schema = resolve_local_refs(raw_schema)
        (
            files, prompt_chars, request_body_chars, review_count,
            estimated_cost, tier_counts,
        ) = write_input_files(
            temporary, source, protocol, selected, args.model, provider_schema,
            state, args.max_file_bytes, args.estimated_output_tokens_per_review,
            args.billing_mode,
        )
        state.close()
        estimated_input_tokens = round(request_body_chars / 4)
        estimated_output_tokens = review_count * args.estimated_output_tokens_per_review
        manifest = {
            "version": 1,
            "scope": f"ICLR 2026 compact reviewer-logic extraction via Qwen {args.billing_mode} API",
            "selection": selection,
            "source": str(source),
            "source_manifest": str((source / "manifest.json").resolve()),
            "reference": str(reference),
            "model": args.model,
            "base_url": args.base_url,
            "billing_mode": args.billing_mode,
            "enable_thinking": False,
            "structured_output": "json_schema_strict",
            "request_count": len(selected),
            "review_count": review_count,
            "input_file_count": len(files),
            "prompt_chars": prompt_chars,
            "request_body_chars": request_body_chars,
            "estimated_input_tokens_chars_div_4": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_batch_cost_usd": round(estimated_cost, 4),
            "estimated_cost_usd": round(estimated_cost, 4),
            "estimated_request_input_tiers": {
                f"up_to_{limit}_tokens": count for limit, count in sorted(tier_counts.items())
            },
            "declared_cost_cap_usd": args.cost_cap_usd,
            "cost_cap_enforcement": "local estimate gate only; provider billing is usage-based",
            "source_manifest_sha256": digest(source / "manifest.json"),
            "protocol_sha256": hashlib.sha256(protocol.encode("utf-8")).hexdigest(),
            "record_schema_sha256": digest(repo / RECORD_SCHEMA),
            "batch_schema_sha256": digest(repo / BATCH_SCHEMA),
            "created_at": now(),
            "input_files": files,
        }
        if estimated_cost > args.cost_cap_usd:
            raise RuntimeError(
                f"estimated cost ${estimated_cost:.2f} exceeds declared cap ${args.cost_cap_usd:.2f}"
            )
        atomic_json(temporary / "manifest.json", manifest)
        temporary.replace(output)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def client_for(manifest: dict[str, Any]) -> OpenAI:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    return OpenAI(api_key=api_key, base_url=manifest["base_url"], timeout=600)


def verify_run_files(output: Path, manifest: dict[str, Any]) -> None:
    """Fail closed if a prepared provider input changed after cost approval."""
    for row in manifest["input_files"]:
        path = output / row["local_path"]
        if not path.is_file():
            raise RuntimeError(f"prepared input is missing: {path}")
        if path.stat().st_size != row["byte_count"] or digest(path) != row["sha256"]:
            raise RuntimeError(f"prepared input changed after approval: {path}")


def submit(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("billing_mode", "batch") != "batch":
        raise RuntimeError("submit requires a run prepared with --billing-mode batch")
    verify_run_files(output, manifest)
    if manifest["estimated_batch_cost_usd"] > manifest["declared_cost_cap_usd"]:
        raise RuntimeError("manifest estimate exceeds its declared cost cap")
    client = client_for(manifest)
    connection = connect_state(output / "state.sqlite3")
    statuses = ["prepared"]
    if args.include_uncertain:
        statuses.extend(["uploaded", "submission_uncertain"])
    placeholders = ",".join("?" for _ in statuses)
    rows = connection.execute(
        f"SELECT input_file,local_path,remote_file_id,status FROM batch_files "
        f"WHERE status IN ({placeholders}) ORDER BY input_file",
        statuses,
    ).fetchall()
    if args.max_files is not None:
        rows = rows[: args.max_files]
    for input_file, local_path, existing_remote_file_id, _ in rows:
        path = output / local_path
        try:
            if existing_remote_file_id:
                remote_file_id = existing_remote_file_id
            else:
                remote = client.files.create(file=path, purpose="batch")
                remote_file_id = remote.id
                connection.execute(
                    "UPDATE batch_files SET remote_file_id=?,status='uploaded',updated_at=? WHERE input_file=?",
                    (remote_file_id, now(), input_file),
                )
                connection.commit()
            batch = client.batches.create(
                input_file_id=remote_file_id,
                endpoint="/v1/chat/completions",
                completion_window=args.completion_window,
                metadata={
                    "ds_name": f"iclr-review-logic-{input_file:05d}",
                    "ds_description": f"{manifest['model']} compact reviewer logic",
                },
            )
            connection.execute(
                "UPDATE batch_files SET batch_id=?,status=?,updated_at=? WHERE input_file=?",
                (batch.id, batch.status, now(), input_file),
            )
            connection.execute(
                "UPDATE requests SET status='submitted',updated_at=? WHERE input_file=?",
                (now(), input_file),
            )
            connection.commit()
            print(json.dumps({"input_file": input_file, "batch_id": batch.id,
                              "status": batch.status}), flush=True)
        except Exception as error:
            connection.execute(
                "UPDATE batch_files SET status='submission_uncertain',error=?,updated_at=? WHERE input_file=?",
                (str(error)[:20_000], now(), input_file),
            )
            connection.commit()
            raise
    connection.close()


def recover(args: argparse.Namespace) -> None:
    """Recover a remote batch ID after interruption without resubmitting work."""
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    client = client_for(manifest)
    connection = connect_state(output / "state.sqlite3")
    candidates = connection.execute(
        "SELECT input_file,remote_file_id FROM batch_files "
        "WHERE remote_file_id IS NOT NULL AND batch_id IS NULL "
        "AND status IN ('uploaded','submission_uncertain') ORDER BY input_file"
    ).fetchall()
    wanted = {remote_file_id for _, remote_file_id in candidates}
    matches: dict[str, list[Any]] = {file_id: [] for file_id in wanted}
    if wanted:
        # The SDK iterator follows pagination. Batch history is retained by the
        # provider for 30 days, which covers interruption recovery for this run.
        page = client.batches.list(limit=100)
        while True:
            for batch in page:
                input_file_id = getattr(batch, "input_file_id", None)
                if input_file_id in matches:
                    matches[input_file_id].append(batch)
            if not page.has_next_page():
                break
            page = page.get_next_page()
    report = []
    for input_file, remote_file_id in candidates:
        found = matches[remote_file_id]
        if len(found) == 1:
            batch = found[0]
            connection.execute(
                "UPDATE batch_files SET batch_id=?,status=?,error=NULL,updated_at=? WHERE input_file=?",
                (batch.id, batch.status, now(), input_file),
            )
            connection.execute(
                "UPDATE requests SET status='submitted',updated_at=? WHERE input_file=?",
                (now(), input_file),
            )
            report.append({"input_file": input_file, "recovered_batch_id": batch.id,
                           "status": batch.status})
        elif len(found) > 1:
            message = f"multiple remote batches found for input file {remote_file_id}"
            connection.execute(
                "UPDATE batch_files SET error=?,updated_at=? WHERE input_file=?",
                (message, now(), input_file),
            )
            report.append({"input_file": input_file, "error": message})
        else:
            report.append({
                "input_file": input_file,
                "status": "no remote batch found; do not resubmit without explicit review",
            })
    connection.commit()
    connection.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def download_content(client: OpenAI, file_id: str, path: Path) -> None:
    response = client.files.content(file_id)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    if hasattr(response, "write_to_file"):
        response.write_to_file(temporary)
    else:  # compatibility with older OpenAI SDK response wrappers
        temporary.write_bytes(response.content)
    temporary.replace(path)


def valid_primary_refs(database: Path, metadata: dict[str, Any]) -> dict[str, set[str]]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro&immutable=1", uri=True)
    try:
        result = {}
        for review in metadata["reviews"]:
            review_id = review["review_id"]
            row = connection.execute(
                "SELECT user_prompt FROM jobs WHERE job_id=?", (f"initial:{review_id}",)
            ).fetchone()
            result[review_id] = {
                ref for ref in SOURCE_REF.findall(row[0]) if ref.startswith("R-")
            } if row else set()
        return result
    finally:
        connection.close()


def publish_request(
    output: Path,
    source: Path,
    database: Path,
    custom_id: str,
    body: dict[str, Any],
) -> tuple[bool, list[str], dict[str, int]]:
    shard = int(custom_id.removeprefix("shard-"))
    suffix = f"{shard:05d}"
    source_text = (source / f"source-shard-{suffix}.md").read_text(encoding="utf-8")
    metadata = json.loads(
        (source / f"source-shard-{suffix}.json").read_text(encoding="utf-8")
    )
    provider_usage = body.get("usage", {})
    usage = {
        "prompt_tokens": int(provider_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(provider_usage.get("completion_tokens") or 0),
        "total_tokens": int(provider_usage.get("total_tokens") or 0),
    }
    choices = body.get("choices", [])
    if not choices:
        return False, ["provider response has no choices"], usage
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        return False, ["provider response content is missing or non-string"], usage
    try:
        # Qwen occasionally emits literal control characters inside otherwise
        # schema-valid JSON strings. Python's non-strict decoder safely accepts
        # those characters without altering structure or semantic fields.
        payload = json.loads(content, strict=False)
        normalize_atomic_refs(payload)
        primary = valid_primary_refs(database, metadata)
        wrapper = {ref for ref in SOURCE_REF.findall(source_text) if ref.startswith("I-")}
        normalize_provenance(payload, primary, wrapper)
        errors = validate_payload(payload, metadata, source_text, primary)
    except Exception as error:
        return False, [f"parse/validation exception: {error}"], usage

    warnings: list[str] = []
    self_report_error = "self-report declares one or more requirements partial or failed"
    nonpass = [
        row for row in payload.get("self_report", {}).get("requirements", [])
        if row.get("status") != "pass"
    ]
    wrapper_only_misread = bool(nonpass) and all(
        row.get("item") == 3
        and any(
            word in str(row.get("reason", "")).casefold()
            for word in ("wrapper", "primary")
        )
        for row in nonpass
    )
    if wrapper_only_misread and self_report_error in errors:
        errors.remove(self_report_error)
        warnings.append("self-report item 3 misread valid wrapper evidence as partial")

    atomic_json(output / "raw" / f"shard-{suffix}.json", payload)
    atomic_text(
        output / "outputs" / f"compact-shard-{suffix}.jsonl",
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in payload["records"]
        ),
    )
    write_report(output / "reports" / f"shard-{suffix}.md", payload, 0.0)
    atomic_json(output / "validations" / f"shard-{suffix}.json", {
        "shard": shard,
        "record_count": len(payload["records"]),
        "unit_count": sum(len(record["logic_units"]) for record in payload["records"]),
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
    })
    return not errors, errors, usage


def consume_batch_output(
    output: Path,
    manifest: dict[str, Any],
    connection: sqlite3.Connection,
    input_file: int,
    path: Path,
) -> None:
    source = Path(manifest["source"])
    source_manifest = json.loads(Path(manifest["source_manifest"]).read_text(encoding="utf-8"))
    database = Path(source_manifest["database"])
    seen: set[str] = set()
    parse_errors: list[str] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                parse_errors.append(f"line {line_number}: invalid provider JSON: {error}")
                continue
            custom_id = row.get("custom_id", "")
            expected = connection.execute(
                "SELECT 1 FROM requests WHERE custom_id=? AND input_file=?",
                (custom_id, input_file),
            ).fetchone()
            if not expected:
                parse_errors.append(
                    f"line {line_number}: unknown or cross-file custom_id {custom_id!r}"
                )
                continue
            seen.add(custom_id)
            response = row.get("response") or {}
            provider_status = response.get("status_code")
            if provider_status != 200:
                error = row.get("error") or response.get("body") or "provider request failed"
                connection.execute(
                    "UPDATE requests SET status='failed',provider_status=?,error=?,updated_at=? "
                    "WHERE custom_id=?",
                    (provider_status, json.dumps(error, ensure_ascii=False)[:20_000], now(), custom_id),
                )
                continue
            ok, errors, usage = publish_request(
                output, source, database, custom_id, response.get("body") or {}
            )
            connection.execute(
                "UPDATE requests SET status=?,provider_status=?,prompt_tokens=?,completion_tokens=?,"
                "total_tokens=?,error=?,updated_at=? WHERE custom_id=?",
                ("complete" if ok else "failed", provider_status,
                 usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"),
                 "\n".join(errors)[:20_000] if errors else None, now(), custom_id),
            )
    missing = connection.execute(
        "SELECT custom_id FROM requests WHERE input_file=? AND status NOT IN ('complete','failed')",
        (input_file,),
    ).fetchall()
    for (custom_id,) in missing:
        if custom_id not in seen:
            connection.execute(
                "UPDATE requests SET status='failed',error='missing from completed provider output',"
                "updated_at=? WHERE custom_id=?",
                (now(), custom_id),
            )
    if parse_errors:
        atomic_text(
            output / "provider" / f"parse-errors-batch-{input_file:05d}.log",
            "\n".join(parse_errors) + "\n",
        )
    connection.commit()


def progress(output: Path, connection: sqlite3.Connection) -> dict[str, Any]:
    request_counts = dict(connection.execute(
        "SELECT status,COUNT(*) FROM requests GROUP BY status"
    ).fetchall())
    review_counts = dict(connection.execute(
        "SELECT status,COALESCE(SUM(review_count),0) FROM requests GROUP BY status"
    ).fetchall())
    usage = connection.execute(
        "SELECT COALESCE(SUM(prompt_tokens),0),COALESCE(SUM(completion_tokens),0),"
        "COALESCE(SUM(total_tokens),0) FROM requests WHERE provider_status=200"
    ).fetchone()
    successful_usage = connection.execute(
        "SELECT COALESCE(prompt_tokens,0),COALESCE(completion_tokens,0) "
        "FROM requests WHERE provider_status=200"
    ).fetchall()
    manifest_path = output / "manifest.json"
    billing_mode = "batch"
    if manifest_path.exists():
        billing_mode = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "billing_mode", "batch"
        )
    actual_cost = sum(
        actual_request_cost(row[0], row[1], billing_mode) for row in successful_usage
    )
    payload = {
        "updated_at": now(),
        "requests": request_counts,
        "reviews": review_counts,
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                  "total_tokens": usage[2],
                  "estimated_billed_batch_usd": round(actual_cost, 6)},
        "batch_files": [
            {"input_file": row[0], "status": row[1], "batch_id": row[2], "error": row[3]}
            for row in connection.execute(
                "SELECT input_file,status,batch_id,error FROM batch_files ORDER BY input_file"
            )
        ],
    }
    atomic_json(output / "progress.json", payload)
    return payload


def iter_prepared_requests(
    output: Path,
    connection: sqlite3.Connection,
    max_requests: int | None,
) -> Iterable[dict[str, Any]]:
    """Stream prepared request bodies instead of retaining the corpus in RAM."""
    wanted_rows = connection.execute(
        "SELECT custom_id FROM requests WHERE status='prepared' ORDER BY input_file,line_number"
    ).fetchall()
    wanted = {row[0] for row in wanted_rows[:max_requests] if row[0]} if max_requests else {
        row[0] for row in wanted_rows
    }
    yielded: set[str] = set()
    for (local_path,) in connection.execute(
        "SELECT local_path FROM batch_files ORDER BY input_file"
    ):
        with (output / local_path).open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("custom_id") in wanted:
                    yielded.add(row["custom_id"])
                    yield row
    if yielded != wanted:
        missing = sorted(wanted - yielded)
        raise RuntimeError(f"prepared requests missing from input files: {missing[:10]}")


def execute_realtime_request(client: OpenAI, request: dict[str, Any]) -> dict[str, Any]:
    body = request["body"]
    try:
        response = client.chat.completions.create(
            model=body["model"],
            messages=body["messages"],
            response_format=body["response_format"],
            extra_body={"enable_thinking": body.get("enable_thinking", False)},
        )
        return {
            "custom_id": request["custom_id"],
            "provider_status": 200,
            "body": response.model_dump(mode="json"),
            "error": None,
        }
    except Exception as error:
        return {
            "custom_id": request["custom_id"],
            "provider_status": getattr(error, "status_code", None),
            "body": None,
            "error": str(error),
        }


def run_realtime(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("billing_mode") != "realtime":
        raise RuntimeError("run-realtime requires --billing-mode realtime preparation")
    verify_run_files(output, manifest)
    connection = connect_state(output / "state.sqlite3")
    lock_stream = (output / "realtime-runner.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        connection.close()
        lock_stream.close()
        raise SystemExit("another realtime runner holds the lock")
    connection.execute(
        "UPDATE requests SET status='failed',error='realtime runner was interrupted; deferred',"
        "updated_at=? WHERE status='running'",
        (now(),),
    )
    connection.commit()
    requests = iter_prepared_requests(output, connection, args.max_requests)
    client = client_for(manifest)
    source = Path(manifest["source"])
    source_manifest = json.loads(Path(manifest["source_manifest"]).read_text(encoding="utf-8"))
    database = Path(source_manifest["database"])
    spent = sum(
        actual_request_cost(prompt_tokens, completion_tokens, "realtime")
        for prompt_tokens, completion_tokens in connection.execute(
            "SELECT COALESCE(prompt_tokens,0),COALESCE(completion_tokens,0) "
            "FROM requests WHERE provider_status=200"
        )
    )
    cost_cap = float(manifest["declared_cost_cap_usd"])
    cap_reached = spent >= cost_cap

    def mark_running(request: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE requests SET status='running',error=NULL,updated_at=? WHERE custom_id=?",
            (now(), request["custom_id"]),
        )
        connection.commit()

    iterator = iter(requests)
    futures: dict[concurrent.futures.Future, str] = {}
    processed = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            def submit_next() -> bool:
                try:
                    request = next(iterator)
                except StopIteration:
                    return False
                mark_running(request)
                futures[executor.submit(execute_realtime_request, client, request)] = request["custom_id"]
                return True

            for _ in range(args.workers):
                if cap_reached or not submit_next():
                    break
            while futures:
                done, _ = concurrent.futures.wait(
                    futures, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    custom_id = futures.pop(future)
                    result = future.result()
                    provider_path = output / "provider" / f"realtime-{custom_id}.json"
                    atomic_json(provider_path, result)
                    if result["provider_status"] == 200:
                        ok, errors, usage = publish_request(
                            output, source, database, custom_id, result["body"]
                        )
                        spent += actual_request_cost(
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                            "realtime",
                        )
                        cap_reached = spent >= cost_cap
                        connection.execute(
                            "UPDATE requests SET status=?,provider_status=200,prompt_tokens=?,"
                            "completion_tokens=?,total_tokens=?,error=?,updated_at=? WHERE custom_id=?",
                            ("complete" if ok else "failed", usage.get("prompt_tokens"),
                             usage.get("completion_tokens"), usage.get("total_tokens"),
                             "\n".join(errors)[:20_000] if errors else None, now(), custom_id),
                        )
                    else:
                        connection.execute(
                            "UPDATE requests SET status='failed',provider_status=?,error=?,updated_at=? "
                            "WHERE custom_id=?",
                            (result["provider_status"], result["error"][:20_000], now(), custom_id),
                        )
                    connection.commit()
                    processed += 1
                    request_status = connection.execute(
                        "SELECT status FROM requests WHERE custom_id=?", (custom_id,)
                    ).fetchone()[0]
                    if request_status == "failed" or (
                        args.progress_every and processed % args.progress_every == 0
                    ):
                        print(json.dumps({"processed_this_run": processed,
                                          "custom_id": custom_id,
                                          "status": request_status}), flush=True)
                    if args.progress_every and processed % args.progress_every == 0:
                        progress(output, connection)
                    if not cap_reached:
                        submit_next()
            if cap_reached:
                print(json.dumps({"status": "cost_cap_reached",
                                  "estimated_billed_usd": round(spent, 6),
                                  "declared_cost_cap_usd": cost_cap}), flush=True)
        print(json.dumps(progress(output, connection), ensure_ascii=False, indent=2))
    finally:
        connection.close()
        lock_stream.close()


def reprocess_realtime(args: argparse.Namespace) -> None:
    """Re-run local parsing and validation without another provider request."""
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("billing_mode") != "realtime":
        raise RuntimeError("reprocess-realtime requires a realtime run")
    source = Path(manifest["source"])
    source_manifest = json.loads(Path(manifest["source_manifest"]).read_text(encoding="utf-8"))
    database = Path(source_manifest["database"])
    connection = connect_state(output / "state.sqlite3")
    rows = connection.execute(
        "SELECT custom_id FROM requests WHERE status='failed' AND provider_status=200 "
        "ORDER BY input_file,line_number"
    ).fetchall()
    if args.max_requests is not None:
        rows = rows[:args.max_requests]
    report = []
    try:
        for (custom_id,) in rows:
            provider_path = output / "provider" / f"realtime-{custom_id}.json"
            provider = json.loads(provider_path.read_text(encoding="utf-8"))
            ok, errors, usage = publish_request(
                output, source, database, custom_id, provider["body"]
            )
            connection.execute(
                "UPDATE requests SET status=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,"
                "error=?,updated_at=? WHERE custom_id=?",
                (
                    "complete" if ok else "failed",
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    "\n".join(errors)[:20_000] if errors else None,
                    now(),
                    custom_id,
                ),
            )
            connection.commit()
            report.append({"custom_id": custom_id, "status": "complete" if ok else "failed"})
        print(json.dumps({"reprocessed": report, "progress": progress(output, connection)},
                         ensure_ascii=False, indent=2))
    finally:
        connection.close()


def sync_once(output: Path, client: OpenAI, manifest: dict[str, Any], connection: sqlite3.Connection) -> bool:
    active = False
    rows = connection.execute(
        "SELECT input_file,batch_id,status FROM batch_files "
        "WHERE batch_id IS NOT NULL AND status NOT IN "
        "('consumed','cancelled_consumed','failed_consumed','expired_consumed','failed','expired','cancelled') "
        "ORDER BY input_file"
    ).fetchall()
    for input_file, batch_id, _ in rows:
        batch = client.batches.retrieve(batch_id)
        status = batch.status
        output_file_id = getattr(batch, "output_file_id", None)
        error_file_id = getattr(batch, "error_file_id", None)
        batch_errors = getattr(batch, "errors", None)
        if hasattr(batch_errors, "model_dump_json"):
            batch_error_text = batch_errors.model_dump_json()
        elif batch_errors:
            batch_error_text = json.dumps(batch_errors, ensure_ascii=False, default=str)
        else:
            batch_error_text = None
        connection.execute(
            "UPDATE batch_files SET status=?,output_file_id=?,error_file_id=?,error=?,updated_at=? "
            "WHERE input_file=?",
            (status, output_file_id, error_file_id, batch_error_text, now(), input_file),
        )
        connection.commit()
        if status == "completed" or output_file_id:
            provider_output = output / "provider" / f"output-batch-{input_file:05d}.jsonl"
            if output_file_id and not provider_output.exists():
                download_content(client, output_file_id, provider_output)
            if error_file_id:
                provider_error = output / "provider" / f"error-batch-{input_file:05d}.jsonl"
                if not provider_error.exists():
                    download_content(client, error_file_id, provider_error)
            if output_file_id:
                consume_batch_output(output, manifest, connection, input_file, provider_output)
                consumed_status = "consumed" if status == "completed" else f"{status}_consumed"
                connection.execute(
                    "UPDATE batch_files SET status=?,updated_at=? WHERE input_file=?",
                    (consumed_status, now(), input_file),
                )
                connection.commit()
            else:
                connection.execute(
                    "UPDATE batch_files SET status='failed',error='completed provider batch has no output file',"
                    "updated_at=? WHERE input_file=?",
                    (now(), input_file),
                )
                connection.execute(
                    "UPDATE requests SET status='failed',error='completed provider batch has no output file',"
                    "updated_at=? WHERE input_file=? AND status NOT IN ('complete','failed')",
                    (now(), input_file),
                )
                connection.commit()
        elif status in {"failed", "expired", "cancelled"}:
            request_error = batch_error_text or f"provider batch ended with status {status}"
            connection.execute(
                "UPDATE requests SET status='failed',error=?,updated_at=? "
                "WHERE input_file=? AND status NOT IN ('complete','failed')",
                (request_error[:20_000], now(), input_file),
            )
            connection.commit()
        else:
            active = True
    print(json.dumps(progress(output, connection), ensure_ascii=False, indent=2))
    return active


def sync(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    client = client_for(manifest)
    connection = connect_state(output / "state.sqlite3")
    try:
        while True:
            active = sync_once(output, client, manifest, connection)
            if not args.watch or not active:
                break
            time.sleep(args.interval)
    finally:
        connection.close()


def status(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    connection = connect_state(output / "state.sqlite3")
    print(json.dumps(progress(output, connection), ensure_ascii=False, indent=2))
    connection.close()


def read_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    return {
        row["review_id"]: row
        for row in (
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def reference_index(reference: Path) -> dict[str, dict[str, Any]]:
    """Index all available reference records by review, independent of sharding."""
    records: dict[str, dict[str, Any]] = {}
    for path in sorted((reference / "outputs").glob("compact-shard-*.jsonl")):
        for review_id, record in read_jsonl(path).items():
            if review_id in records:
                raise ValueError(f"duplicate reference review_id: {review_id}")
            records[review_id] = record
    return records


def compare(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    reference = Path(manifest["reference"])
    connection = connect_state(output / "state.sqlite3")
    shards = [row[0] for row in connection.execute(
        "SELECT shard FROM requests WHERE status='complete' ORDER BY shard"
    )]
    connection.close()
    baseline = reference_index(reference)
    comparisons: list[dict[str, Any]] = []
    for shard in shards:
        suffix = f"{shard:05d}"
        candidate_path = output / "outputs" / f"compact-shard-{suffix}.jsonl"
        candidate = read_jsonl(candidate_path)
        for review_id in sorted(candidate.keys() & baseline.keys()):
            qwen_units = len(candidate[review_id]["logic_units"])
            luna_units = len(baseline[review_id]["logic_units"])
            comparisons.append({
                "review_id": review_id,
                "qwen_units": qwen_units,
                "luna_units": luna_units,
                "difference": qwen_units - luna_units,
                "ratio": round(qwen_units / luna_units, 3) if luna_units else None,
            })
    differences = [row["difference"] for row in comparisons]
    ratios = sorted(row["ratio"] for row in comparisons if row["ratio"] is not None)
    report = {
        "compared_reviews": len(comparisons),
        "qwen_units": sum(row["qwen_units"] for row in comparisons),
        "luna_units": sum(row["luna_units"] for row in comparisons),
        "mean_unit_difference": round(sum(differences) / len(differences), 3) if differences else None,
        "median_unit_ratio": ratios[len(ratios) // 2] if ratios else None,
        "qwen_fewer_reviews": sum(value < 0 for value in differences),
        "equal_reviews": sum(value == 0 for value in differences),
        "qwen_more_reviews": sum(value > 0 for value in differences),
        "records": comparisons,
        "note": "Unit count is a coverage diagnostic, not a semantic ground truth.",
    }
    atomic_json(output / "comparison-with-luna.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "records"},
                     ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    prepare_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    prepare_parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--model", default=DEFAULT_MODEL)
    prepare_parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    prepare_parser.add_argument("--billing-mode", choices=("batch", "realtime"), default="batch")
    prepare_parser.add_argument("--pilot-reviews", type=int)
    prepare_parser.add_argument("--max-shards", type=int)
    prepare_parser.add_argument("--max-file-bytes", type=int, default=MAX_BYTES_PER_FILE)
    prepare_parser.add_argument("--estimated-output-tokens-per-review", type=int, default=1500)
    prepare_parser.add_argument("--cost-cap-usd", type=float, required=True)
    prepare_parser.set_defaults(function=prepare)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--output", type=Path, required=True)
    submit_parser.add_argument("--max-files", type=int)
    submit_parser.add_argument("--completion-window", default="24h")
    submit_parser.add_argument(
        "--include-uncertain", action="store_true",
        help="explicitly resubmit uploaded/uncertain files after running recover",
    )
    submit_parser.set_defaults(function=submit)

    recover_parser = subparsers.add_parser("recover")
    recover_parser.add_argument("--output", type=Path, required=True)
    recover_parser.set_defaults(function=recover)

    realtime_parser = subparsers.add_parser("run-realtime")
    realtime_parser.add_argument("--output", type=Path, required=True)
    realtime_parser.add_argument("--workers", type=int, default=8)
    realtime_parser.add_argument("--max-requests", type=int)
    realtime_parser.add_argument("--progress-every", type=int, default=100)
    realtime_parser.set_defaults(function=run_realtime)

    reprocess_parser = subparsers.add_parser("reprocess-realtime")
    reprocess_parser.add_argument("--output", type=Path, required=True)
    reprocess_parser.add_argument("--max-requests", type=int)
    reprocess_parser.set_defaults(function=reprocess_realtime)

    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--output", type=Path, required=True)
    sync_parser.add_argument("--watch", action="store_true")
    sync_parser.add_argument("--interval", type=int, default=120)
    sync_parser.set_defaults(function=sync)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--output", type=Path, required=True)
    status_parser.set_defaults(function=status)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--output", type=Path, required=True)
    compare_parser.set_defaults(function=compare)
    return root


def main() -> None:
    args = parser().parse_args()
    if getattr(args, "pilot_reviews", None) is not None and args.pilot_reviews <= 0:
        raise SystemExit("--pilot-reviews must be positive")
    if getattr(args, "max_shards", None) is not None and args.max_shards <= 0:
        raise SystemExit("--max-shards must be positive")
    if getattr(args, "max_file_bytes", 1) <= 0:
        raise SystemExit("--max-file-bytes must be positive")
    if getattr(args, "cost_cap_usd", 1) <= 0:
        raise SystemExit("--cost-cap-usd must be positive")
    if getattr(args, "workers", 1) <= 0:
        raise SystemExit("--workers must be positive")
    args.function(args)


if __name__ == "__main__":
    main()
