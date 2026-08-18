"""Inventory public ICLR review data available through OpenReview.

The script intentionally preserves each year's native field names.  It only
summarizes schemas and counts; raw review collection is a separate step.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openreview


DEFAULT_YEARS = tuple(range(2018, 2027))


def unwrap(value: Any) -> Any:
    """Unwrap the API v2 ``{"value": ...}`` content representation."""
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def note_json(note: Any) -> dict[str, Any]:
    return note.to_json() if hasattr(note, "to_json") else dict(note)


def reply_kind(reply: dict[str, Any]) -> str:
    invitations = reply.get("invitations") or [reply.get("invitation", "")]
    names = [str(invitation).rsplit("/", 1)[-1] for invitation in invitations]
    return "+".join(sorted(set(filter(None, names)))) or "unknown"


def content_keys(note: dict[str, Any]) -> list[str]:
    return sorted((note.get("content") or {}).keys())


def summarize_notes(
    *,
    year: int,
    venue_id: str,
    submission_id: str,
    submissions: list[Any],
    sampled: list[Any],
    sampled_replies: list[dict[str, Any]],
    api_version: str,
) -> dict[str, Any]:
    submission_fields: Counter[str] = Counter()
    reply_kinds: Counter[str] = Counter()
    reply_fields: dict[str, Counter[str]] = {}

    for submission in submissions:
        submission_fields.update(content_keys(note_json(submission)))

    for reply in sampled_replies:
        kind = reply_kind(reply)
        reply_kinds[kind] += 1
        reply_fields.setdefault(kind, Counter()).update(content_keys(reply))

    return {
        "year": year,
        "venue_id": venue_id,
        "api_version": api_version,
        "submission_invitation": submission_id,
        "submission_count": len(submissions),
        "submission_fields": dict(submission_fields.most_common()),
        "sampled_submissions": len(sampled),
        "sampled_replies": len(sampled_replies),
        "reply_kinds_in_sample": dict(reply_kinds.most_common()),
        "reply_fields_in_sample": {
            kind: dict(fields.most_common()) for kind, fields in sorted(reply_fields.items())
        },
    }


def inventory_year_v2(
    client: openreview.api.OpenReviewClient, year: int, sample: int
) -> dict[str, Any]:
    venue_id = f"ICLR.cc/{year}/Conference"
    group = client.get_group(venue_id)
    if not group.content or not group.content.get("submission_id"):
        raise LookupError("Venue uses the legacy API")
    submission_id = unwrap(group.content["submission_id"])

    submissions = client.get_all_notes(invitation=submission_id)
    sampled = client.get_notes(
        invitation=submission_id,
        limit=min(sample, len(submissions)),
        details="replies",
    )

    replies = []
    for submission in sampled:
        for reply in submission.details.get("replies", []):
            replies.append(reply)

    return summarize_notes(
        year=year,
        venue_id=venue_id,
        submission_id=submission_id,
        submissions=submissions,
        sampled=sampled,
        sampled_replies=replies,
        api_version="v2",
    )


def inventory_year_v1(client: openreview.Client, year: int, sample: int) -> dict[str, Any]:
    venue_id = f"ICLR.cc/{year}/Conference"
    submission_id = f"{venue_id}/-/Blind_Submission"
    submissions = client.get_all_notes(invitation=submission_id)
    sampled = submissions[:sample]
    replies = []
    for submission in sampled:
        replies.extend(
            note_json(note)
            for note in client.get_notes(forum=submission.id)
            if note.id != submission.id
        )

    return summarize_notes(
        year=year,
        venue_id=venue_id,
        submission_id=submission_id,
        submissions=submissions,
        sampled=sampled,
        sampled_replies=replies,
        api_version="v1",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS)
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("data/inventory/iclr.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not os.environ.get("OPENREVIEW_USERNAME") or not os.environ.get("OPENREVIEW_PASSWORD"):
        raise SystemExit(
            "Set OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD in your shell. "
            "The credentials are sent only to https://api2.openreview.net and are not saved."
        )

    client_v2 = openreview.api.OpenReviewClient(
        baseurl="https://api2.openreview.net",
        username=os.environ["OPENREVIEW_USERNAME"],
        password=os.environ["OPENREVIEW_PASSWORD"],
    )
    # API v1 and v2 accept the same session token. Reusing it avoids a second login
    # and OpenReview's deliberately strict authentication rate limit.
    client_v1 = openreview.Client(baseurl="https://api.openreview.net", token=client_v2.token)
    results = []
    for year in args.years:
        try:
            try:
                result = inventory_year_v2(client_v2, year, args.sample)
            except LookupError:
                result = inventory_year_v1(client_v1, year, args.sample)
            results.append(result)
        except Exception as error:  # Preserve partial inventory across historical API differences.
            results.append(
                {
                    "year": year,
                    "venue_id": f"ICLR.cc/{year}/Conference",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "https://openreview.net/",
        "years": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
