"""Run DeepSeek pilot jobs behind a persistent hard-dollar budget gate."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from openai import OpenAI


DEFAULT_DATABASE = Path("data/analysis/iclr/pilot.sqlite3")
MODEL = "deepseek-v4-flash"
INPUT_CACHE_HIT_USD_PER_MILLION = 0.0028
INPUT_CACHE_MISS_USD_PER_MILLION = 0.14
OUTPUT_USD_PER_MILLION = 0.28
PROJECT_ID = os.environ.get("DEEPSEEK_PROJECT_ID", "iclr-review-analysis")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def exclusive_runner_lock(database: Path):
    """Prevent two local runners from corrupting the shared budget ledger."""
    database.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database.with_suffix(f"{database.suffix}.runner.lock")
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"another DeepSeek runner holds {lock_path}"
            ) from error
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def usd_to_microusd(value: float) -> int:
    return math.ceil(value * 1_000_000)


def provider_balance_usd(api_key: str) -> float:
    request = Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS URL
        payload = json.load(response)
    if not payload.get("is_available"):
        raise RuntimeError("DeepSeek reports the account as unavailable")
    for balance in payload.get("balance_infos") or []:
        if balance.get("currency") == "USD":
            return float(balance["total_balance"])
    raise RuntimeError("DeepSeek balance response did not contain USD")


def cost_microusd(
    *,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
) -> int:
    # A price quoted in USD / 1M tokens is numerically equal to micro-USD/token.
    return math.ceil(
        cache_hit_tokens * INPUT_CACHE_HIT_USD_PER_MILLION
        + cache_miss_tokens * INPUT_CACHE_MISS_USD_PER_MILLION
        + output_tokens * OUTPUT_USD_PER_MILLION
    )


def reservation_microusd(system_prompt: str, user_prompt: str, max_tokens: int) -> int:
    # UTF-8 bytes are a deliberately conservative upper bound for these English
    # prompts. Reserve every input byte at the cache-miss price and every allowed
    # output token at the output price, including hidden reasoning tokens.
    input_upper_bound = len(system_prompt.encode()) + len(user_prompt.encode()) + 256
    return cost_microusd(
        cache_hit_tokens=0,
        cache_miss_tokens=input_upper_bound,
        output_tokens=max_tokens,
    )


def connect_database(path: Path, budget_usd: float) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS budget_state (
            project_id TEXT PRIMARY KEY,
            budget_microusd INTEGER NOT NULL,
            spent_microusd INTEGER NOT NULL DEFAULT 0,
            reserved_microusd INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS api_calls (
            job_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            reserved_microusd INTEGER NOT NULL,
            actual_microusd INTEGER,
            prompt_tokens INTEGER,
            cache_hit_tokens INTEGER,
            cache_miss_tokens INTEGER,
            completion_tokens INTEGER,
            reasoning_tokens INTEGER,
            finish_reason TEXT,
            error TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS api_call_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            reserved_microusd INTEGER NOT NULL,
            actual_microusd INTEGER,
            prompt_tokens INTEGER,
            cache_hit_tokens INTEGER,
            cache_miss_tokens INTEGER,
            completion_tokens INTEGER,
            reasoning_tokens INTEGER,
            finish_reason TEXT,
            error TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            archived_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS api_call_attempts_job_idx
            ON api_call_attempts(job_id, attempt_id);

        CREATE TABLE IF NOT EXISTS memos (
            job_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            review_id TEXT,
            memo TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    budget_microusd = usd_to_microusd(budget_usd)
    existing = connection.execute(
        "SELECT budget_microusd FROM budget_state WHERE project_id = ?", (PROJECT_ID,)
    ).fetchone()
    if existing and existing[0] != budget_microusd:
        raise RuntimeError(
            f"Budget is already fixed at ${existing[0] / 1_000_000:.6f}; "
            "change it explicitly in the ledger before running with a new cap"
        )
    with connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO budget_state (
                project_id, budget_microusd, spent_microusd,
                reserved_microusd, updated_at
            ) VALUES (?, ?, 0, 0, ?)
            """,
            (PROJECT_ID, budget_microusd, now_iso()),
        )
    return connection


def recover_interrupted_calls(connection: sqlite3.Connection) -> None:
    interrupted = connection.execute(
        "SELECT job_id, reserved_microusd FROM api_calls WHERE status = 'running'"
    ).fetchall()
    if not interrupted:
        return
    # A disconnected request may still have been billed. Charge its full
    # reservation to remain fail-closed, then make the job pending again. The
    # caller is an explicit new runner (including the supervisor), so automatic
    # recovery does not silently lose work after a process restart.
    charge = sum(row["reserved_microusd"] for row in interrupted)
    with connection:
        connection.execute(
            """
            UPDATE budget_state
            SET spent_microusd = spent_microusd + ?,
                reserved_microusd = MAX(0, reserved_microusd - ?),
                updated_at = ?
            WHERE project_id = ?
            """,
            (charge, charge, now_iso(), PROJECT_ID),
        )
        connection.execute(
            """
            UPDATE api_calls
            SET status = 'interrupted_charged', actual_microusd = reserved_microusd,
                error = 'Process ended before usage reconciliation; full reservation charged',
                completed_at = ?
            WHERE status = 'running'
            """,
            (now_iso(),),
        )
        connection.executemany(
            "UPDATE jobs SET status = 'pending' WHERE job_id = ?",
            [(row["job_id"],) for row in interrupted],
        )


def reserve_job(connection: sqlite3.Connection, job: sqlite3.Row) -> bool:
    reservation = reservation_microusd(
        job["system_prompt"], job["user_prompt"], job["max_tokens"]
    )
    with connection:
        state = connection.execute(
            """
            SELECT budget_microusd, spent_microusd, reserved_microusd
            FROM budget_state WHERE project_id = ?
            """,
            (PROJECT_ID,),
        ).fetchone()
        if state["spent_microusd"] + state["reserved_microusd"] + reservation > state["budget_microusd"]:
            return False
        # Keep every completed/failed attempt before the current-call slot is
        # reused.  This is essential for measuring prompt tuning and selective
        # retries without weakening the one-row-per-active-call budget logic.
        connection.execute(
            """
            INSERT INTO api_call_attempts (
                job_id, model, status, reserved_microusd, actual_microusd,
                prompt_tokens, cache_hit_tokens, cache_miss_tokens,
                completion_tokens, reasoning_tokens, finish_reason, error,
                started_at, completed_at, archived_at
            )
            SELECT job_id, model, status, reserved_microusd, actual_microusd,
                   prompt_tokens, cache_hit_tokens, cache_miss_tokens,
                   completion_tokens, reasoning_tokens, finish_reason, error,
                   started_at, completed_at, ?
            FROM api_calls
            WHERE job_id = ? AND status != 'running'
            """,
            (now_iso(), job["job_id"]),
        )
        connection.execute(
            """
            UPDATE budget_state
            SET reserved_microusd = reserved_microusd + ?, updated_at = ?
            WHERE project_id = ?
            """,
            (reservation, now_iso(), PROJECT_ID),
        )
        connection.execute(
            "UPDATE jobs SET status = 'running' WHERE job_id = ?",
            (job["job_id"],),
        )
        connection.execute(
            """
            INSERT INTO api_calls (
                job_id, model, status, reserved_microusd, started_at
            ) VALUES (?, ?, 'running', ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                model = excluded.model,
                status = 'running',
                reserved_microusd = excluded.reserved_microusd,
                actual_microusd = NULL,
                prompt_tokens = NULL,
                cache_hit_tokens = NULL,
                cache_miss_tokens = NULL,
                completion_tokens = NULL,
                reasoning_tokens = NULL,
                finish_reason = NULL,
                error = NULL,
                started_at = excluded.started_at,
                completed_at = NULL
            """,
            (job["job_id"], MODEL, reservation, now_iso()),
        )
    return True


def execute_job(
    client: OpenAI,
    job: dict[str, Any],
    thinking: str,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": job["system_prompt"]},
            {"role": "user", "content": job["user_prompt"]},
        ],
        "max_tokens": job["max_tokens"],
        "extra_body": {
            "thinking": {"type": thinking},
            "user_id": PROJECT_ID,
        },
    }
    if thinking == "enabled":
        request["reasoning_effort"] = "high"
    else:
        request["temperature"] = 0.4
    response = client.chat.completions.create(
        **request,
    )
    usage = response.usage.model_dump() if response.usage else {}
    completion_details = usage.get("completion_tokens_details") or {}
    cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
    raw_cache_miss_tokens = usage.get("prompt_cache_miss_tokens")
    cache_miss_tokens = (
        max(int(usage.get("prompt_tokens") or 0) - cache_hit_tokens, 0)
        if raw_cache_miss_tokens is None
        else int(raw_cache_miss_tokens)
    )
    return {
        "memo": response.choices[0].message.content or "",
        "finish_reason": response.choices[0].finish_reason,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
    }


def reconcile_success(
    connection: sqlite3.Connection,
    job: sqlite3.Row,
    result: dict[str, Any],
) -> None:
    call = connection.execute(
        "SELECT reserved_microusd FROM api_calls WHERE job_id = ?", (job["job_id"],)
    ).fetchone()
    actual = cost_microusd(
        cache_hit_tokens=result["cache_hit_tokens"],
        cache_miss_tokens=result["cache_miss_tokens"],
        output_tokens=result["completion_tokens"],
    )
    if actual > call["reserved_microusd"]:
        raise RuntimeError(f"Actual cost exceeded reservation for {job['job_id']}")
    with connection:
        connection.execute(
            """
            UPDATE budget_state
            SET spent_microusd = spent_microusd + ?,
                reserved_microusd = reserved_microusd - ?,
                updated_at = ?
            WHERE project_id = ?
            """,
            (actual, call["reserved_microusd"], now_iso(), PROJECT_ID),
        )
        connection.execute(
            """
            UPDATE api_calls SET
                status = 'complete', actual_microusd = ?, prompt_tokens = ?,
                cache_hit_tokens = ?, cache_miss_tokens = ?, completion_tokens = ?,
                reasoning_tokens = ?, finish_reason = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (
                actual,
                result["prompt_tokens"],
                result["cache_hit_tokens"],
                result["cache_miss_tokens"],
                result["completion_tokens"],
                result["reasoning_tokens"],
                result["finish_reason"],
                now_iso(),
                job["job_id"],
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO memos (
                job_id, stage, paper_id, review_id, memo, model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["job_id"],
                job["stage"],
                job["paper_id"],
                job["review_id"],
                result["memo"],
                MODEL,
                now_iso(),
            ),
        )
        connection.execute(
            "UPDATE jobs SET status = 'complete' WHERE job_id = ?", (job["job_id"],)
        )


def reconcile_failure(
    connection: sqlite3.Connection,
    job: sqlite3.Row,
    error: Exception,
) -> None:
    call = connection.execute(
        "SELECT reserved_microusd FROM api_calls WHERE job_id = ?", (job["job_id"],)
    ).fetchone()
    # Fail closed: a transport failure may occur after provider billing.
    charged = call["reserved_microusd"]
    with connection:
        connection.execute(
            """
            UPDATE budget_state
            SET spent_microusd = spent_microusd + ?,
                reserved_microusd = reserved_microusd - ?, updated_at = ?
            WHERE project_id = ?
            """,
            (charged, charged, now_iso(), PROJECT_ID),
        )
        connection.execute(
            """
            UPDATE api_calls
            SET status = 'failed_charged', actual_microusd = reserved_microusd,
                error = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (f"{type(error).__name__}: {error}", now_iso(), job["job_id"]),
        )
        connection.execute(
            "UPDATE jobs SET status = 'failed' WHERE job_id = ?", (job["job_id"],)
        )


def run(args: argparse.Namespace) -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("Set DEEPSEEK_API_KEY")
    api_key = os.environ["DEEPSEEK_API_KEY"]
    if args.minimum_provider_balance_usd is not None:
        balance = provider_balance_usd(api_key)
        print(f"provider balance before run: ${balance:.2f}", flush=True)
        if balance < args.minimum_provider_balance_usd:
            raise SystemExit(
                f"provider balance ${balance:.2f} is below the configured "
                f"${args.minimum_provider_balance_usd:.2f} floor"
            )
    connection = connect_database(args.database, args.budget_usd)
    recover_interrupted_calls(connection)
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=300.0,
        max_retries=0,
    )
    try:
        query = "SELECT * FROM jobs WHERE status = 'pending'"
        params: list[Any] = []
        if args.stage:
            query += " AND stage = ?"
            params.append(args.stage)
        query += " ORDER BY job_id LIMIT ?"
        params.append(args.max_jobs)
        candidates = connection.execute(query, params).fetchall()

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            active: dict[Future[dict[str, Any]], sqlite3.Row] = {}
            next_index = 0
            stop_submissions = False
            run_completed = 0
            run_failed = 0

            def submit_next() -> bool:
                nonlocal next_index, stop_submissions
                if stop_submissions or next_index >= len(candidates):
                    return False
                job = candidates[next_index]
                next_index += 1
                if not reserve_job(connection, job):
                    print("budget gate closed before the next job", flush=True)
                    stop_submissions = True
                    return False
                future = executor.submit(execute_job, client, dict(job), args.thinking)
                active[future] = job
                return True

            while len(active) < args.concurrency and submit_next():
                pass

            while active:
                try:
                    done, _ = wait(active, return_when=FIRST_COMPLETED)
                except KeyboardInterrupt:
                    if not stop_submissions:
                        stop_submissions = True
                        print(
                            "interrupt received; finishing and reconciling active calls",
                            flush=True,
                        )
                    continue

                for future in done:
                    job = active.pop(future)
                    try:
                        result = future.result()
                        reconcile_success(connection, job, result)
                        run_completed += 1
                        if run_completed % args.progress_every == 0:
                            print(
                                f"progress: {run_completed:,} complete / "
                                f"{run_failed:,} failed in this run; "
                                f"last={job['job_id']} "
                                f"({result['prompt_tokens']:,} in / "
                                f"{result['completion_tokens']:,} out)",
                                flush=True,
                            )
                        if (
                            args.minimum_provider_balance_usd is not None
                            and run_completed % args.balance_check_every == 0
                        ):
                            try:
                                balance = provider_balance_usd(api_key)
                                print(
                                    f"provider balance checkpoint: ${balance:.2f}",
                                    flush=True,
                                )
                                if balance < args.minimum_provider_balance_usd:
                                    print(
                                        "provider balance floor reached; stopping new calls",
                                        flush=True,
                                    )
                                    stop_submissions = True
                            except Exception as error:
                                print(
                                    "provider balance check failed; stopping new calls "
                                    f"fail-closed ({type(error).__name__})",
                                    flush=True,
                                )
                                stop_submissions = True
                    except Exception as error:
                        reconcile_failure(connection, job, error)
                        run_failed += 1
                        print(
                            f"failed {job['job_id']}: {type(error).__name__}",
                            flush=True,
                        )

                while len(active) < args.concurrency and submit_next():
                    pass

        state = connection.execute(
            "SELECT * FROM budget_state WHERE project_id = ?", (PROJECT_ID,)
        ).fetchone()
        completed = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'complete'"
        ).fetchone()[0]
        print(
            json.dumps(
                {
                    "completed_jobs": completed,
                    "budget_usd": state["budget_microusd"] / 1_000_000,
                    "spent_usd": state["spent_microusd"] / 1_000_000,
                    "reserved_usd": state["reserved_microusd"] / 1_000_000,
                },
                indent=2,
            )
        )
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--budget-usd", type=float, default=5.0)
    parser.add_argument(
        "--stage",
        choices=[
            "initial_blind",
            "trajectory",
            "paper_synthesis",
            "forum_direct",
            "method_comparison",
            "method_comparison_batch",
            "method_comparison_global",
        ],
    )
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--progress-every", type=int, default=1)
    parser.add_argument("--balance-check-every", type=int, default=500)
    parser.add_argument("--minimum-provider-balance-usd", type=float)
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default="disabled")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_jobs <= 0:
        raise SystemExit("--max-jobs must be positive")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be positive")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive")
    if args.balance_check_every <= 0:
        raise SystemExit("--balance-check-every must be positive")
    if (
        args.minimum_provider_balance_usd is not None
        and args.minimum_provider_balance_usd < 0
    ):
        raise SystemExit("--minimum-provider-balance-usd cannot be negative")
    if args.budget_usd <= 0:
        raise SystemExit("--budget-usd must be positive")
    with exclusive_runner_lock(args.database):
        run(args)


if __name__ == "__main__":
    main()
