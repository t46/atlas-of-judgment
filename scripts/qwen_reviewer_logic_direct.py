"""Durable Qwen real-time runner for reviewer-attributed Direct memos.

One provider request consumes one forum-level DeepSeek memo and emits one
forum record containing separate logic records for each human evaluator.
Provider failures and interrupted requests are retained and never retried
automatically.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from openai import OpenAI

try:
    from scripts.qwen_review_logic_batch import (
        DEFAULT_BASE_URL,
        DEFAULT_MODEL,
        actual_request_cost,
        atomic_json,
        estimated_request_cost,
        resolve_local_refs,
    )
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from qwen_review_logic_batch import (
        DEFAULT_BASE_URL,
        DEFAULT_MODEL,
        actual_request_cost,
        atomic_json,
        estimated_request_cost,
        resolve_local_refs,
    )


DEFAULT_DATABASE = Path("data/analysis/iclr/direct-2018-2026.sqlite3")
DEFAULT_PROTOCOL = Path("data/analysis/iclr/reviewer-logic-direct-qwen/PROTOCOL.md")
DEFAULT_SCHEMA = Path("schemas/reviewer-logic-direct-v0.1.json")
SOURCE_REF_RE = re.compile(
    r"((?:[A-Za-z0-9_-]+):L\d{3,6}"
    r"(?:[-–—](?:(?:[A-Za-z0-9_-]+):L|L)?\d{3,6})?)"
)
RANGE_REF_RE = re.compile(r"^(.+:L)(\d{3,6})-(?:(.+:L|L))?(\d{3,6})$")
NON_REVIEWER_RE = re.compile(r"program[_ ]?chairs?|analyst|narrator|authors?", re.IGNORECASE)
LEAK_RE = re.compile(
    r"\b(?:score|rating|rated|accept(?:ance|ed)?|reject(?:ion|ed)?|"
    r"final decision|recommend(?:ation|ed)?)\b",
    re.IGNORECASE,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def source_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=60)


def initialize_state(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE requests (
            custom_id TEXT PRIMARY KEY,
            source_job_id TEXT NOT NULL UNIQUE,
            paper_id TEXT NOT NULL,
            forum_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            memo_chars INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'prepared',
            provider_status INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            error TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    connection.commit()


def selected_rows(database: Path, pilot_forums: int | None) -> list[tuple[Any, ...]]:
    source = source_connection(database)
    rows = source.execute(
        "SELECT m.job_id,m.paper_id,length(m.memo) "
        "FROM memos m JOIN jobs j ON j.job_id=m.job_id "
        "WHERE m.stage='forum_direct' AND j.status='complete' "
        "ORDER BY m.paper_id"
    ).fetchall()
    source.close()
    if not pilot_forums or pilot_forums >= len(rows):
        return rows

    by_year: dict[int, list[tuple[Any, ...]]] = {}
    for row in rows:
        year = int(str(row[1]).split(":", 1)[0])
        by_year.setdefault(year, []).append(row)
    years = sorted(by_year)
    base, remainder = divmod(pilot_forums, len(years))
    result: list[tuple[Any, ...]] = []
    for offset, year in enumerate(years):
        candidates = sorted(by_year[year], key=lambda item: (item[2], item[0]))
        count = min(len(candidates), base + (1 if offset < remainder else 0))
        if count == 1:
            positions = [len(candidates) // 2]
        elif count:
            positions = [round(index * (len(candidates) - 1) / (count - 1)) for index in range(count)]
        else:
            positions = []
        result.extend(candidates[position] for position in positions)
    return sorted(result, key=lambda item: item[1])


def forum_id(paper_id: str) -> str:
    return paper_id.split(":", 1)[1] if ":" in paper_id else paper_id


def wrapper_prefix(custom_id: str) -> str:
    return "D-" + hashlib.sha256(custom_id.encode()).hexdigest()[:10]


def wrap_memo(custom_id: str, memo: str) -> tuple[str, set[str]]:
    prefix = wrapper_prefix(custom_id)
    lines = memo.splitlines() or [memo]
    refs = {f"{prefix}:L{index:05d}" for index in range(1, len(lines) + 1)}
    wrapped = "\n".join(
        f"[{prefix}:L{index:05d}] {line}" for index, line in enumerate(lines, 1)
    )
    return wrapped, refs


def build_prompt(protocol: str, paper_id: str, forum: str, custom_id: str, memo: str) -> tuple[str, set[str]]:
    wrapped, refs = wrap_memo(custom_id, memo)
    return (
        f"{protocol.rstrip()}\n\n"
        "## Source metadata\n\n"
        f"paper_id: {paper_id}\nforum_id: {forum}\n\n"
        "## Forum-level analysis memo\n\n"
        f"{wrapped}\n",
        refs,
    )


def provider_request(
    prompt: str, model: str, schema: dict[str, Any], max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "enable_thinking": False,
        "max_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "reviewer_logic_direct",
                "description": "Reviewer-attributed evaluation logic from one forum memo",
                "schema": schema,
                "strict": True,
            },
        },
    }


def provider_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove validation keywords rejected by DashScope structured output.

    The original schema remains authoritative during local validation.
    """
    if isinstance(schema, list):
        return [provider_compatible_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: provider_compatible_schema(value)
        for key, value in schema.items()
        if key not in {"uniqueItems", "contains", "minContains", "maxContains"}
    }


def prepare(args: argparse.Namespace) -> None:
    repo = Path(__file__).resolve().parents[1]
    database = (repo / args.database).resolve() if not args.database.is_absolute() else args.database
    protocol_path = (repo / args.protocol).resolve() if not args.protocol.is_absolute() else args.protocol
    schema_path = (repo / args.schema).resolve() if not args.schema.is_absolute() else args.schema
    output = (repo / args.output).resolve() if not args.output.is_absolute() else args.output
    if output.exists():
        raise FileExistsError(f"refusing to replace existing run: {output}")

    rows = selected_rows(database, args.pilot_forums)
    if args.max_forums is not None:
        rows = rows[: args.max_forums]
    if not rows:
        raise RuntimeError("no completed forum_direct memos selected")
    protocol = protocol_path.read_text(encoding="utf-8")
    schema = resolve_local_refs(json.loads(schema_path.read_text(encoding="utf-8")))
    fixed_chars = len(protocol) + len(json.dumps(schema, ensure_ascii=False)) + 2_000
    estimated_cost = 0.0
    tier_counts: dict[int, int] = {}
    for _, _, memo_chars in rows:
        # Numbered wrappers add roughly one fifth to a typical memo. The JSON
        # schema and protocol are included because providers may bill both.
        request_chars = fixed_chars + round(memo_chars * 1.25)
        cost, _, tier = estimated_request_cost(
            request_chars, 1, args.estimated_output_tokens_per_forum, "realtime"
        )
        estimated_cost += cost
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    if estimated_cost > args.cost_cap_usd:
        raise RuntimeError(
            f"estimated cost ${estimated_cost:.2f} exceeds cap ${args.cost_cap_usd:.2f}"
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
        inserted = []
        for index, (job_id, paper_id, memo_chars) in enumerate(rows, 1):
            custom_id = f"forum-{index:06d}"
            year = int(str(paper_id).split(":", 1)[0])
            inserted.append((
                custom_id, job_id, paper_id, forum_id(paper_id), year,
                memo_chars, now(),
            ))
        state.executemany(
            "INSERT INTO requests(custom_id,source_job_id,paper_id,forum_id,year,memo_chars,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            inserted,
        )
        state.commit()
        state.close()
        manifest = {
            "version": 1,
            "scope": "ICLR 2018-2026 reviewer-attributed logic from forum_direct memos",
            "selection": (
                f"stratified pilot of {len(rows)} forums across years and memo lengths"
                if args.pilot_forums else f"all {len(rows)} completed forum_direct memos"
            ),
            "database": str(database),
            "database_size": database.stat().st_size,
            "database_mtime_ns": database.stat().st_mtime_ns,
            "protocol": str(protocol_path),
            "schema": str(schema_path),
            "model": args.model,
            "base_url": args.base_url,
            "enable_thinking": False,
            "structured_output": "json_schema_strict",
            "request_count": len(rows),
            "estimated_output_tokens_per_forum": args.estimated_output_tokens_per_forum,
            "max_output_tokens": args.max_output_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
            "estimated_request_input_tiers": {
                f"up_to_{limit}_tokens": count for limit, count in sorted(tier_counts.items())
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


def client_for(manifest: dict[str, Any]) -> OpenAI:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    return OpenAI(api_key=key, base_url=manifest["base_url"], timeout=600)


def source_memo(database: Path, job_id: str) -> str:
    source = source_connection(database)
    row = source.execute("SELECT memo FROM memos WHERE job_id=?", (job_id,)).fetchone()
    source.close()
    if not row:
        raise KeyError(f"source memo is missing: {job_id}")
    return row[0]


def response_usage(body: dict[str, Any]) -> dict[str, int]:
    usage_raw = body.get("usage") or {}
    return {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }


def parse_response(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    usage = response_usage(body)
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("provider response has no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("provider response content is missing")
    return json.loads(content, strict=False), usage


def semantic_text(payload: dict[str, Any]) -> Iterable[str]:
    for reviewer in payload.get("reviewer_records", []):
        yield reviewer.get("review_logic_summary", "")
        yield from reviewer.get("unresolved_tensions", [])
        for unit in reviewer.get("logic_units", []):
            for field in (
                "inspected_object", "observation", "reasoning", "judgment",
                "suggested_improvement", "update_trigger",
            ):
                value = unit.get(field)
                if isinstance(value, str):
                    yield value
    yield from payload.get("forum_tensions", [])


def normalize_payload(
    payload: dict[str, Any], source_refs: set[str], source_text: str,
) -> None:
    """Repair mechanical representation without changing semantic content."""
    merged: dict[str, dict[str, Any]] = {}
    for reviewer in payload.get("reviewer_records", []):
        key = str(reviewer.get("reviewer_key", ""))
        if NON_REVIEWER_RE.search(key):
            continue
        if key not in merged:
            merged[key] = reviewer
            continue
        target = merged[key]
        target["source_note_ids"] = list(dict.fromkeys(
            target.get("source_note_ids", []) + reviewer.get("source_note_ids", [])
        ))
        target["logic_units"].extend(reviewer.get("logic_units", []))
        target["unresolved_tensions"] = list(dict.fromkeys(
            target.get("unresolved_tensions", []) + reviewer.get("unresolved_tensions", [])
        ))
    payload["reviewer_records"] = list(merged.values())

    wrapper_lines: list[tuple[str, str]] = []
    for line in source_text.splitlines():
        match = re.match(r"^\[(D-[A-Za-z0-9_-]+:L\d{3,6})\]\s?(.*)$", line)
        if match:
            wrapper_lines.append((match.group(1), match.group(2)))

    # Turn provider-emitted line ranges into atomic endpoint references.
    for reviewer_index, reviewer in enumerate(payload.get("reviewer_records", []), 1):
        for unit_index, unit in enumerate(reviewer.get("logic_units", []), 1):
            unit["unit_id"] = f"U-{reviewer_index:02d}-{unit_index:02d}"
            normalized: list[str] = []
            for reference in unit.get("evidence_refs", []):
                reference = (
                    str(reference).strip().replace("[", "").replace("]", "")
                    .replace("–", "-").replace("—", "-")
                )
                match = RANGE_REF_RE.fullmatch(reference)
                candidates = [reference]
                if match:
                    prefix, start, end_prefix, end = match.groups()
                    width = max(len(start), len(end))
                    effective_end_prefix = prefix if end_prefix in (None, "L") else end_prefix
                    candidates = [
                        f"{prefix}{int(start):0{width}d}",
                        f"{effective_end_prefix}{int(end):0{width}d}",
                    ]
                for candidate in candidates:
                    bare_line = re.fullmatch(r"L(\d{2,6})", candidate)
                    if bare_line and wrapper_lines:
                        prefix = wrapper_lines[0][0].rsplit("L", 1)[0]
                        candidate = f"{prefix}L{int(bare_line.group(1)):05d}"
                    wrapper_line = re.fullmatch(r"(D-[A-Za-z0-9_-]+:L)(\d{2,6})", candidate)
                    if wrapper_line:
                        candidate = f"{wrapper_line.group(1)}{int(wrapper_line.group(2)):05d}"
                    if candidate not in source_refs and f"M-{candidate}" in source_refs:
                        candidate = f"M-{candidate}"
                    if candidate not in source_refs:
                        raw_note_id = candidate.split(":L", 1)[0]
                        note_ids = [raw_note_id]
                        if not raw_note_id.startswith("M-"):
                            note_ids.append(f"M-{raw_note_id}")
                        fallback = next(
                            (
                                wrapper_ref for wrapper_ref, text in wrapper_lines
                                if any(note_id in text for note_id in note_ids)
                            ),
                            None,
                        )
                        if fallback:
                            candidate = fallback
                            unit["support_status"] = "memo_inferred"
                            missing = unit.setdefault("missing_links", [])
                            if "primary_provenance" not in missing:
                                missing.append("primary_provenance")
                    if candidate not in normalized:
                        normalized.append(candidate)
            unit["evidence_refs"] = normalized


def source_references(source_text: str) -> set[str]:
    """Collect atomic refs, expanding any range written in the source memo."""
    refs: set[str] = set()
    for raw in SOURCE_REF_RE.findall(source_text):
        value = raw.replace("–", "-").replace("—", "-")
        match = RANGE_REF_RE.fullmatch(value)
        if not match:
            refs.add(value)
            continue
        prefix, start, end_prefix, end = match.groups()
        width = max(len(start), len(end))
        effective_end_prefix = prefix if end_prefix in (None, "L") else end_prefix
        if effective_end_prefix == prefix and int(end) >= int(start):
            refs.update(f"{prefix}{line:0{width}d}" for line in range(int(start), int(end) + 1))
        else:
            refs.add(f"{prefix}{int(start):0{width}d}")
            refs.add(f"{effective_end_prefix}{int(end):0{width}d}")
    return refs


def validate_payload(
    payload: dict[str, Any], schema: dict[str, Any], paper_id: str,
    forum: str, valid_refs: set[str], source_text: str,
) -> tuple[list[str], list[str]]:
    errors = [
        f"schema: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]
    if payload.get("paper_id") != paper_id:
        errors.append("paper_id does not match source")
    if payload.get("forum_id") != forum:
        errors.append("forum_id does not match source")
    reviewer_keys: set[str] = set()
    unit_ids: set[str] = set()
    source_refs = valid_refs | source_references(source_text)
    for reviewer in payload.get("reviewer_records", []):
        key = reviewer.get("reviewer_key", "")
        if key in reviewer_keys:
            errors.append(f"duplicate reviewer_key: {key}")
        reviewer_keys.add(key)
        for unit in reviewer.get("logic_units", []):
            unit_id = unit.get("unit_id", "")
            if unit_id in unit_ids:
                errors.append(f"duplicate unit_id: {unit_id}")
            unit_ids.add(unit_id)
            for reference in unit.get("evidence_refs", []):
                if reference not in source_refs:
                    errors.append(f"invalid evidence ref: {reference}")
    warnings: list[str] = []
    leaking = sorted({match.group(0) for text in semantic_text(payload) for match in LEAK_RE.finditer(text)})
    if leaking:
        warnings.append("score/decision leakage in semantic fields: " + ", ".join(leaking))
    return errors, warnings


def execute(client: OpenAI, request: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=request["model"],
            messages=request["messages"],
            max_tokens=request["max_tokens"],
            response_format=request["response_format"],
            extra_body={"enable_thinking": False},
        )
        return {"provider_status": 200, "body": response.model_dump(mode="json"), "error": None}
    except Exception as error:
        return {
            "provider_status": getattr(error, "status_code", None),
            "body": None,
            "error": str(error),
        }


def status_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    statuses = dict(connection.execute("SELECT status,count(*) FROM requests GROUP BY status"))
    usage_rows = connection.execute(
        "SELECT coalesce(prompt_tokens,0),coalesce(completion_tokens,0) "
        "FROM requests WHERE provider_status=200"
    ).fetchall()
    prompt_tokens = sum(row[0] for row in usage_rows)
    completion_tokens = sum(row[1] for row in usage_rows)
    return {
        "statuses": statuses,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "actual_cost_usd": round(sum(
            actual_request_cost(prompt, completion, "realtime")
            for prompt, completion in usage_rows
        ), 6),
    }


def run(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    database = Path(manifest["database"])
    schema_path = output / Path(manifest["schema"]).name
    if hashlib.sha256(schema_path.read_bytes()).hexdigest() != manifest["schema_sha256"]:
        raise RuntimeError("prepared schema changed after cost approval")
    schema = resolve_local_refs(json.loads(schema_path.read_text(encoding="utf-8")))
    protocol_path = output / "PROTOCOL.md"
    protocol = protocol_path.read_text(encoding="utf-8")
    if hashlib.sha256(protocol.encode()).hexdigest() != manifest["protocol_sha256"]:
        raise RuntimeError("prepared protocol changed after cost approval")
    database_stat = database.stat()
    if (
        database_stat.st_size != manifest["database_size"]
        or database_stat.st_mtime_ns != manifest["database_mtime_ns"]
    ):
        raise RuntimeError("source Direct database changed after preparation")
    state = connect(output / "state.sqlite3")
    lock = (output / "realtime-runner.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        state.close()
        lock.close()
        raise SystemExit("another Direct realtime runner holds the lock")
    state.execute(
        "UPDATE requests SET status='failed',error='runner interrupted; deferred',updated_at=? "
        "WHERE status='running'", (now(),)
    )
    state.commit()
    initial_progress = status_payload(state)
    cap = float(manifest["declared_cost_cap_usd"])
    if initial_progress["actual_cost_usd"] >= cap:
        print(json.dumps({"status": "cost_cap_reached", **initial_progress}, indent=2))
        state.close()
        lock.close()
        return
    rows = state.execute(
        "SELECT custom_id,source_job_id,paper_id,forum_id,memo_chars FROM requests "
        "WHERE status='prepared' ORDER BY custom_id"
    ).fetchall()
    if args.max_requests is not None:
        rows = rows[: args.max_requests]
    client = client_for(manifest)
    provider_schema = provider_compatible_schema(schema)
    processed = 0
    spent = float(initial_progress["actual_cost_usd"])
    prompt_total = int(initial_progress["prompt_tokens"])
    completion_total = int(initial_progress["completion_tokens"])
    consecutive_provider_failures = 0

    def make_task(row: tuple[Any, ...]) -> tuple[str, str, str, set[str], dict[str, Any]]:
        custom_id, job_id, paper_id, forum, expected_chars = row
        memo = source_memo(database, job_id)
        if len(memo) != expected_chars:
            raise RuntimeError(f"source memo changed for {job_id}")
        prompt, refs = build_prompt(protocol, paper_id, forum, custom_id, memo)
        return custom_id, paper_id, forum, refs, provider_request(
            prompt, manifest["model"], provider_schema,
            int(manifest.get("max_output_tokens", 12_000)),
        )

    futures: dict[concurrent.futures.Future, tuple[str, str, str, set[str], str]] = {}
    iterator = iter(rows)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            def submit_next() -> bool:
                try:
                    row = next(iterator)
                except StopIteration:
                    return False
                custom_id, paper_id, forum, refs, request = make_task(row)
                state.execute(
                    "UPDATE requests SET status='running',error=NULL,updated_at=? WHERE custom_id=?",
                    (now(), custom_id),
                )
                state.commit()
                future = executor.submit(execute, client, request)
                futures[future] = (custom_id, paper_id, forum, refs, request["messages"][0]["content"])
                return True

            for _ in range(args.workers):
                if not submit_next():
                    break
            while futures:
                done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    custom_id, paper_id, forum, refs, source_text = futures.pop(future)
                    result = future.result()
                    atomic_json(output / "provider" / f"{custom_id}.json", result)
                    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    errors: list[str] = []
                    warnings: list[str] = []
                    if result["provider_status"] == 200:
                        usage = response_usage(result["body"])
                        try:
                            payload, _ = parse_response(result["body"])
                            source_refs = refs | source_references(source_text)
                            normalize_payload(payload, source_refs, source_text)
                            errors, warnings = validate_payload(
                                payload, schema, paper_id, forum, refs, source_text
                            )
                            atomic_json(output / "outputs" / f"{custom_id}.json", payload)
                        except Exception as error:
                            errors = [f"parse/validation exception: {error}"]
                    else:
                        errors = [result["error"] or "provider request failed"]
                    atomic_json(output / "validations" / f"{custom_id}.json", {
                        "errors": errors,
                        "warnings": warnings,
                    })
                    state.execute(
                        "UPDATE requests SET status=?,provider_status=?,prompt_tokens=?,completion_tokens=?,"
                        "total_tokens=?,error=?,updated_at=? WHERE custom_id=?",
                        (
                            "complete" if not errors else "failed", result["provider_status"],
                            usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"],
                            "\n".join(errors)[:20_000] if errors else None, now(), custom_id,
                        ),
                    )
                    state.commit()
                    processed += 1
                    if result["provider_status"] == 200:
                        consecutive_provider_failures = 0
                        prompt_total += usage["prompt_tokens"]
                        completion_total += usage["completion_tokens"]
                        spent += actual_request_cost(
                            usage["prompt_tokens"], usage["completion_tokens"], "realtime"
                        )
                    else:
                        consecutive_provider_failures += 1
                    progress = {
                        "statuses": dict(state.execute(
                            "SELECT status,count(*) FROM requests GROUP BY status"
                        )),
                        "prompt_tokens": prompt_total,
                        "completion_tokens": completion_total,
                        "actual_cost_usd": round(spent, 6),
                    }
                    if spent >= cap:
                        print(json.dumps({"status": "cost_cap_reached", **progress}), flush=True)
                        rows = []
                        iterator = iter(())
                    elif consecutive_provider_failures >= args.workers:
                        print(json.dumps({
                            "status": "provider_failure_circuit_open",
                            "consecutive_provider_failures": consecutive_provider_failures,
                            **progress,
                        }), flush=True)
                        rows = []
                        iterator = iter(())
                    elif not errors and args.progress_every and processed % args.progress_every == 0:
                        print(json.dumps({"processed_this_run": processed, **progress}), flush=True)
                    elif errors:
                        print(json.dumps({"custom_id": custom_id, "status": "failed", "errors": errors[:3]}), flush=True)
                    submit_next()
        print(json.dumps(status_payload(state), ensure_ascii=False, indent=2))
    finally:
        state.close()
        lock.close()


def status(args: argparse.Namespace) -> None:
    connection = connect(args.output.resolve() / "state.sqlite3")
    print(json.dumps(status_payload(connection), ensure_ascii=False, indent=2))
    connection.close()


def reprocess(args: argparse.Namespace) -> None:
    """Re-run local normalization and validation without an API request."""
    output = args.output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    database = Path(manifest["database"])
    schema_path = output / Path(manifest["schema"]).name
    schema = resolve_local_refs(json.loads(schema_path.read_text(encoding="utf-8")))
    protocol = (output / "PROTOCOL.md").read_text(encoding="utf-8")
    state = connect(output / "state.sqlite3")
    lock = (output / "realtime-runner.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        state.close()
        lock.close()
        raise SystemExit("cannot reprocess while the realtime runner holds the lock")
    rows = state.execute(
        "SELECT custom_id,source_job_id,paper_id,forum_id,memo_chars FROM requests "
        "WHERE status='failed' AND provider_status=200 ORDER BY custom_id"
    ).fetchall()
    if args.max_requests is not None:
        rows = rows[: args.max_requests]
    recovered = 0
    for custom_id, job_id, paper_id, forum, expected_chars in rows:
        memo = source_memo(database, job_id)
        if len(memo) != expected_chars:
            raise RuntimeError(f"source memo changed for {job_id}")
        source_text, wrapper_refs = build_prompt(protocol, paper_id, forum, custom_id, memo)
        result = json.loads((output / "provider" / f"{custom_id}.json").read_text(encoding="utf-8"))
        errors: list[str] = []
        warnings: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            usage = response_usage(result["body"])
            payload, _ = parse_response(result["body"])
            refs = wrapper_refs | source_references(source_text)
            normalize_payload(payload, refs, source_text)
            errors, warnings = validate_payload(
                payload, schema, paper_id, forum, wrapper_refs, source_text
            )
            atomic_json(output / "outputs" / f"{custom_id}.json", payload)
        except Exception as error:
            errors = [f"parse/validation exception: {error}"]
        atomic_json(output / "validations" / f"{custom_id}.json", {
            "errors": errors,
            "warnings": warnings,
        })
        state.execute(
            "UPDATE requests SET status=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,"
            "error=?,updated_at=? WHERE custom_id=?",
            (
                "complete" if not errors else "failed", usage["prompt_tokens"],
                usage["completion_tokens"], usage["total_tokens"],
                "\n".join(errors)[:20_000] if errors else None, now(), custom_id,
            ),
        )
        state.commit()
        recovered += not errors
    print(json.dumps({
        "reprocessed": len(rows),
        "recovered": recovered,
        "progress": status_payload(state),
    }, ensure_ascii=False, indent=2))
    state.close()
    lock.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    prepare_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    prepare_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--model", default=DEFAULT_MODEL)
    prepare_parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    prepare_parser.add_argument("--pilot-forums", type=int)
    prepare_parser.add_argument("--max-forums", type=int)
    prepare_parser.add_argument("--estimated-output-tokens-per-forum", type=int, default=6_500)
    prepare_parser.add_argument("--max-output-tokens", type=int, default=16_000)
    prepare_parser.add_argument("--cost-cap-usd", type=float, required=True)
    prepare_parser.set_defaults(handler=prepare)

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--workers", type=int, default=16)
    run_parser.add_argument("--max-requests", type=int)
    run_parser.add_argument("--progress-every", type=int, default=25)
    run_parser.set_defaults(handler=run)

    status_parser = commands.add_parser("status")
    status_parser.add_argument("--output", type=Path, required=True)
    status_parser.set_defaults(handler=status)

    reprocess_parser = commands.add_parser("reprocess")
    reprocess_parser.add_argument("--output", type=Path, required=True)
    reprocess_parser.add_argument("--max-requests", type=int)
    reprocess_parser.set_defaults(handler=reprocess)
    return root


def main() -> None:
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
