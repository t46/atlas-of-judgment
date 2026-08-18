"""Collect public ICLR forums and all replies from OpenReview.

The collector writes one forum per SQLite row and commits each API page in a
transaction. It is therefore safe to interrupt and rerun: the primary key
deduplicates forums and the next request resumes after the largest stored note
ID for that year.

Raw OpenReview note JSON is preserved. Semantic normalization is intentionally
a separate step so historical form differences remain auditable.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import openreview


DEFAULT_YEARS = tuple(range(2018, 2027))
DEFAULT_DATABASE = Path("data/raw/iclr/openreview.sqlite3")


def unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def note_json(note: Any) -> dict[str, Any]:
    if hasattr(note, "to_json"):
        return note.to_json()
    return dict(note)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS forums (
            year INTEGER NOT NULL,
            forum_id TEXT NOT NULL,
            api_version TEXT NOT NULL,
            submission_invitation TEXT NOT NULL,
            submission_json TEXT NOT NULL,
            replies_json TEXT NOT NULL,
            reply_count INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (year, forum_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_status (
            year INTEGER PRIMARY KEY,
            venue_id TEXT NOT NULL,
            api_version TEXT NOT NULL,
            submission_invitation TEXT NOT NULL,
            expected_submissions INTEGER,
            stored_submissions INTEGER NOT NULL DEFAULT 0,
            stored_replies INTEGER NOT NULL DEFAULT 0,
            after_id TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    status_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(collection_status)")
    }
    if "after_id" not in status_columns:
        connection.execute("ALTER TABLE collection_status ADD COLUMN after_id TEXT")
    connection.commit()
    return connection


def retry_call(function: Callable[[], Any], *, attempts: int = 7) -> Any:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return function()
        except Exception as error:
            if attempt == attempts:
                raise
            print(
                f"request failed ({type(error).__name__}); retrying in {delay:.0f}s "
                f"[{attempt}/{attempts}]",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise AssertionError("unreachable")


def venue_config(
    client_v2: openreview.api.OpenReviewClient,
    year: int,
) -> tuple[str, str, str]:
    venue_id = f"ICLR.cc/{year}/Conference"
    try:
        group = retry_call(lambda: client_v2.get_group(venue_id))
        submission = (group.content or {}).get("submission_id")
        if submission:
            return venue_id, "v2", str(unwrap(submission))
    except Exception as error:
        print(
            f"{year}: v2 venue lookup failed ({type(error).__name__}); trying v1",
            file=sys.stderr,
            flush=True,
        )
    return venue_id, "v1", f"{venue_id}/-/Blind_Submission"


def get_page(
    *,
    client_v1: openreview.Client,
    client_v2: openreview.api.OpenReviewClient,
    api_version: str,
    invitation: str,
    page_size: int,
    after: str | None,
    with_count: bool,
) -> Any:
    client = client_v2 if api_version == "v2" else client_v1
    return client.get_notes(
        invitation=invitation,
        limit=page_size,
        after=after,
        details="replies",
        sort="id",
        with_count=with_count,
    )


def stored_stats(connection: sqlite3.Connection, year: int) -> tuple[int, int]:
    row = connection.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(reply_count), 0)
        FROM forums
        WHERE year = ?
        """,
        (year,),
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def save_page(
    connection: sqlite3.Connection,
    *,
    year: int,
    venue_id: str,
    api_version: str,
    invitation: str,
    expected: int | None,
    submissions: list[Any],
    after_id: str,
) -> tuple[int, int]:
    fetched_at = now_iso()
    rows = []
    for submission in submissions:
        document = note_json(submission)
        # ``Note.to_json()`` deliberately omits query-only details. Read the
        # attached attribute first; otherwise every stored forum would appear
        # to have zero replies even though ``details=replies`` was requested.
        details = getattr(submission, "details", None) or document.get("details") or {}
        replies = details.get("replies") or []
        forum_id = str(document.get("forum") or document["id"])
        rows.append(
            (
                year,
                forum_id,
                api_version,
                invitation,
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                json.dumps(replies, ensure_ascii=False, separators=(",", ":")),
                len(replies),
                fetched_at,
            )
        )

    with connection:
        connection.executemany(
            """
            INSERT INTO forums (
                year, forum_id, api_version, submission_invitation,
                submission_json, replies_json, reply_count, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(year, forum_id) DO UPDATE SET
                api_version = excluded.api_version,
                submission_invitation = excluded.submission_invitation,
                submission_json = excluded.submission_json,
                replies_json = excluded.replies_json,
                reply_count = excluded.reply_count,
                fetched_at = excluded.fetched_at
            """,
            rows,
        )
        count, replies = stored_stats(connection, year)
        connection.execute(
            """
            INSERT INTO collection_status (
                year, venue_id, api_version, submission_invitation,
                expected_submissions, stored_submissions, stored_replies,
                after_id, completed, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(year) DO UPDATE SET
                venue_id = excluded.venue_id,
                api_version = excluded.api_version,
                submission_invitation = excluded.submission_invitation,
                expected_submissions = COALESCE(excluded.expected_submissions, expected_submissions),
                stored_submissions = excluded.stored_submissions,
                stored_replies = excluded.stored_replies,
                after_id = excluded.after_id,
                completed = 0,
                updated_at = excluded.updated_at
            """,
            (
                year,
                venue_id,
                api_version,
                invitation,
                expected,
                count,
                replies,
                after_id,
                fetched_at,
            ),
        )
    return count, replies


def collect_year(
    connection: sqlite3.Connection,
    *,
    client_v1: openreview.Client,
    client_v2: openreview.api.OpenReviewClient,
    year: int,
    page_size: int,
    max_forums: int | None,
) -> None:
    venue_id, api_version, invitation = venue_config(client_v2, year)
    stored, stored_replies = stored_stats(connection, year)
    status = connection.execute(
        "SELECT completed, after_id FROM collection_status WHERE year = ?", (year,)
    ).fetchone()
    if status and status[0]:
        print(f"{year}: already complete ({stored:,} forums, {stored_replies:,} replies)")
        return
    after = status[1] if status else None

    print(
        f"{year}: collecting via {api_version}; resuming after {after or 'start'} "
        f"({stored:,} stored)",
        flush=True,
    )
    expected: int | None = None
    first_request = True
    while True:
        response = retry_call(
            lambda: get_page(
                client_v1=client_v1,
                client_v2=client_v2,
                api_version=api_version,
                invitation=invitation,
                page_size=page_size,
                after=after,
                with_count=first_request,
            )
        )
        if first_request:
            submissions, expected = response
            first_request = False
        else:
            submissions = response
        if not submissions:
            break

        previous_stored = stored
        next_after = submissions[-1].id
        stored, stored_replies = save_page(
            connection,
            year=year,
            venue_id=venue_id,
            api_version=api_version,
            invitation=invitation,
            expected=expected,
            submissions=submissions,
            after_id=next_after,
        )
        if stored <= previous_stored:
            raise RuntimeError(
                f"{year}: pagination made no progress after {after!r}; refusing to loop"
            )
        after = next_after
        denominator = f"/{expected:,}" if expected is not None else ""
        print(
            f"{year}: {stored:,}{denominator} forums, {stored_replies:,} replies",
            flush=True,
        )
        if max_forums is not None and stored >= max_forums:
            print(f"{year}: stopped at --max-forums={max_forums:,}", flush=True)
            return
        if len(submissions) < page_size:
            break

    with connection:
        stored, stored_replies = stored_stats(connection, year)
        connection.execute(
            """
            UPDATE collection_status
            SET stored_submissions = ?, stored_replies = ?, completed = 1, updated_at = ?
            WHERE year = ?
            """,
            (stored, stored_replies, now_iso(), year),
        )
    print(f"{year}: complete ({stored:,} forums, {stored_replies:,} replies)", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-forums", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.page_size <= 1000:
        raise SystemExit("--page-size must be between 1 and 1000")
    if not os.environ.get("OPENREVIEW_USERNAME") or not os.environ.get("OPENREVIEW_PASSWORD"):
        raise SystemExit("Set OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD")

    # OpenReview applies a deliberately strict login-specific rate limit. The
    # data requests have separate limits, so retry client initialization rather
    # than requiring an operator to restart a long collection job.
    client_v2 = retry_call(
        lambda: openreview.api.OpenReviewClient(
            baseurl="https://api2.openreview.net",
            username=os.environ["OPENREVIEW_USERNAME"],
            password=os.environ["OPENREVIEW_PASSWORD"],
        )
    )
    client_v1 = openreview.Client(baseurl="https://api.openreview.net", token=client_v2.token)
    connection = connect_database(args.database)
    try:
        for year in sorted(set(args.years)):
            collect_year(
                connection,
                client_v1=client_v1,
                client_v2=client_v2,
                year=year,
                page_size=args.page_size,
                max_forums=args.max_forums,
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
