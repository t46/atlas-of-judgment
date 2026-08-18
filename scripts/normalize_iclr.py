"""Normalize raw OpenReview forums without imposing an analysis taxonomy.

This layer standardizes only provenance, message types, authorship roles, reply
edges, and text representation. Reviewer reasoning remains untouched for the
later open-ended memo pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


DEFAULT_RAW_DATABASE = Path("data/raw/iclr/openreview.sqlite3")
DEFAULT_OUTPUT_DATABASE = Path("data/processed/iclr/analysis.sqlite3")

KIND_PATTERNS = (
    ("official_review", re.compile(r"official[_ ]review", re.I)),
    ("meta_review", re.compile(r"meta[_ ]review|metareview", re.I)),
    ("desk_rejection", re.compile(r"desk[_ ]reject", re.I)),
    ("withdrawal", re.compile(r"withdraw", re.I)),
    ("decision", re.compile(r"acceptance[_ ]decision|decision", re.I)),
    ("official_comment", re.compile(r"official[_ ]comment", re.I)),
    ("public_comment", re.compile(r"public[_ ]comment", re.I)),
    ("comment", re.compile(r"comment", re.I)),
)


def unwrap(value: Any) -> Any:
    if isinstance(value, dict):
        if "value" in value:
            return unwrap(value["value"])
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    return value


def invitation_list(note: dict[str, Any]) -> list[str]:
    invitations = note.get("invitations")
    if invitations:
        return [str(item) for item in invitations]
    invitation = note.get("invitation")
    return [str(invitation)] if invitation else []


def invitation_label(invitation: str) -> str:
    if "/-/" in invitation:
        return invitation.rsplit("/-/", 1)[1]
    return invitation.rsplit("/", 1)[-1]


def classify_kind(invitations: Iterable[str]) -> str:
    labels = [invitation_label(item) for item in invitations]
    meaningful = [label for label in labels if label.lower() != "edit"]
    joined = " ".join(meaningful or labels)
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(joined):
            return kind
    return "unknown"


def classify_role(kind: str, signatures: list[str]) -> str:
    joined = " ".join(signatures)
    lowered = joined.lower()
    if "/authors" in lowered or lowered.endswith("authors"):
        return "author"
    if "/reviewer_" in lowered or "/reviewers" in lowered:
        return "reviewer"
    if "/area_chair_" in lowered or "/area_chairs" in lowered:
        return "area_chair"
    if "/program_chair" in lowered:
        return "program_chair"
    if kind == "official_review":
        return "reviewer"
    if kind == "meta_review":
        return "area_chair"
    if kind in {"decision", "desk_rejection"}:
        return "program_chair"
    if any(signature.startswith("~") for signature in signatures):
        return "public"
    return "unknown"


def scalar_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        text = "\n".join(item.strip() for item in value if item.strip())
        return text or None
    return None


def content_text(content: dict[str, Any]) -> str:
    sections = []
    for key, value in content.items():
        text = scalar_text(value)
        if text is not None:
            sections.append(f"[{key}]\n{text}")
    return "\n\n".join(sections)


def first_text(content: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = scalar_text(content.get(key))
        if value:
            return value
    return None


def connect_output(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS papers (
            year INTEGER NOT NULL,
            forum_id TEXT NOT NULL,
            api_version TEXT NOT NULL,
            title TEXT,
            abstract TEXT,
            content_json TEXT NOT NULL,
            reply_count INTEGER NOT NULL,
            review_count INTEGER NOT NULL DEFAULT 0,
            comment_count INTEGER NOT NULL DEFAULT 0,
            has_meta_review INTEGER NOT NULL DEFAULT 0,
            decision TEXT,
            withdrawn INTEGER NOT NULL DEFAULT 0,
            desk_rejected INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (year, forum_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            year INTEGER NOT NULL,
            note_id TEXT NOT NULL,
            forum_id TEXT NOT NULL,
            replyto TEXT,
            kind TEXT NOT NULL,
            role TEXT NOT NULL,
            signature TEXT,
            cdate INTEGER,
            mdate INTEGER,
            invitations_json TEXT NOT NULL,
            content_json TEXT NOT NULL,
            content_text TEXT NOT NULL,
            PRIMARY KEY (year, note_id),
            FOREIGN KEY (year, forum_id) REFERENCES papers(year, forum_id)
        );

        CREATE INDEX IF NOT EXISTS messages_forum_idx
            ON messages(year, forum_id, cdate);
        CREATE INDEX IF NOT EXISTS messages_replyto_idx
            ON messages(year, replyto);
        CREATE INDEX IF NOT EXISTS messages_kind_idx
            ON messages(year, kind);
        CREATE INDEX IF NOT EXISTS messages_role_idx
            ON messages(year, role);
        """
    )
    connection.commit()
    return connection


def normalize_year(
    raw: sqlite3.Connection,
    output: sqlite3.Connection,
    year: int,
) -> None:
    expected_row = raw.execute(
        "SELECT stored_submissions, completed FROM collection_status WHERE year = ?",
        (year,),
    ).fetchone()
    if not expected_row or not expected_row[1]:
        raise RuntimeError(f"{year}: raw collection is not complete")
    expected = int(expected_row[0])

    print(f"{year}: normalizing {expected:,} forums", flush=True)
    with output:
        output.execute("DELETE FROM messages WHERE year = ?", (year,))
        output.execute("DELETE FROM papers WHERE year = ?", (year,))

        cursor = raw.execute(
            """
            SELECT forum_id, api_version, submission_json, replies_json, reply_count
            FROM forums
            WHERE year = ?
            ORDER BY forum_id
            """,
            (year,),
        )
        paper_rows = []
        message_rows = []
        for index, row in enumerate(cursor, start=1):
            forum_id, api_version, submission_json, replies_json, reply_count = row
            submission = json.loads(submission_json)
            submission_content = unwrap(submission.get("content") or {})
            review_count = 0
            comment_count = 0
            has_meta_review = False
            withdrawn = False
            desk_rejected = False
            decision: str | None = None
            decision_date = -1
            for reply in json.loads(replies_json):
                invitations = invitation_list(reply)
                kind = classify_kind(invitations)
                signatures = [str(item) for item in (reply.get("signatures") or [])]
                content = unwrap(reply.get("content") or {})
                cdate = reply.get("cdate") or reply.get("tcdate")
                if kind == "official_review":
                    review_count += 1
                if kind in {"official_comment", "public_comment", "comment"}:
                    comment_count += 1
                has_meta_review = has_meta_review or kind == "meta_review"
                withdrawn = withdrawn or kind == "withdrawal"
                desk_rejected = desk_rejected or kind == "desk_rejection"
                if kind == "decision" and (cdate or -1) >= decision_date:
                    decision = first_text(content, "decision", "recommendation")
                    decision_date = cdate or -1
                message_rows.append(
                    (
                        year,
                        str(reply["id"]),
                        str(reply.get("forum") or forum_id),
                        reply.get("replyto"),
                        kind,
                        classify_role(kind, signatures),
                        signatures[0] if signatures else None,
                        cdate,
                        reply.get("mdate") or reply.get("tmdate"),
                        json.dumps(invitations, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                        content_text(content),
                    )
                )

            paper_rows.append(
                (
                    year,
                    forum_id,
                    api_version,
                    first_text(submission_content, "title"),
                    first_text(submission_content, "abstract"),
                    json.dumps(submission_content, ensure_ascii=False, separators=(",", ":")),
                    reply_count,
                    review_count,
                    comment_count,
                    int(has_meta_review),
                    decision,
                    int(withdrawn),
                    int(desk_rejected),
                )
            )

            if len(paper_rows) >= 500:
                insert_rows(output, paper_rows, message_rows)
                paper_rows.clear()
                message_rows.clear()
            if index % 5_000 == 0:
                print(f"{year}: normalized {index:,}/{expected:,}", flush=True)

        insert_rows(output, paper_rows, message_rows)
    papers = output.execute(
        "SELECT COUNT(*) FROM papers WHERE year = ?", (year,)
    ).fetchone()[0]
    messages = output.execute(
        "SELECT COUNT(*) FROM messages WHERE year = ?", (year,)
    ).fetchone()[0]
    reviews = output.execute(
        "SELECT COUNT(*) FROM messages WHERE year = ? AND kind = 'official_review'",
        (year,),
    ).fetchone()[0]
    print(
        f"{year}: complete ({papers:,} papers, {messages:,} messages, {reviews:,} reviews)",
        flush=True,
    )


def insert_rows(
    output: sqlite3.Connection,
    paper_rows: list[tuple[Any, ...]],
    message_rows: list[tuple[Any, ...]],
) -> None:
    output.executemany(
        """
        INSERT INTO papers (
            year, forum_id, api_version, title, abstract, content_json, reply_count,
            review_count, comment_count, has_meta_review, decision, withdrawn, desk_rejected
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        paper_rows,
    )
    output.executemany(
        """
        INSERT INTO messages (
            year, note_id, forum_id, replyto, kind, role, signature,
            cdate, mdate, invitations_json, content_json, content_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        message_rows,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(range(2018, 2027)))
    parser.add_argument("--raw-database", type=Path, default=DEFAULT_RAW_DATABASE)
    parser.add_argument("--output-database", type=Path, default=DEFAULT_OUTPUT_DATABASE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = sqlite3.connect(f"file:{args.raw_database}?mode=ro", uri=True)
    output = connect_output(args.output_database)
    try:
        for year in args.years:
            normalize_year(raw, output, year)
    finally:
        output.close()
        raw.close()


if __name__ == "__main__":
    main()
