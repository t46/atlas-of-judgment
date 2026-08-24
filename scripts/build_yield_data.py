"""Who yields? Split-verdict forums x post-rebuttal judgment movement (ICLR 2026).

Maps each Direct-track reviewer to their OpenReview note ids (source_note_ids
in the raw forum outputs), joins their official rating, and asks: on forums
where ratings split by >= 4 notches, does the LOW scorer or the HIGH scorer
soften (weakened/reversed) after the author response? Baselines included.

Writes data/analysis/iclr/unit-taxonomy-direct-v1/yield-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECT_RUN = PROJECT_ROOT / "data/analysis/iclr/reviewer-logic-direct-qwen-full-v1"
DIRECT_DIR = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def main() -> None:
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    rating = {}
    for note_id, cj in aconn.execute(
        "SELECT note_id, content_json FROM messages WHERE year = 2026 AND kind = 'official_review'"
    ):
        try:
            r = json.loads(cj).get("rating")
            if isinstance(r, int):
                rating[note_id] = r
        except json.JSONDecodeError:
            continue
    aconn.close()

    # signature-suffix -> rating map (API v2 signatures end with /Reviewer_XXXX)
    aconn2 = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    sig_rating: dict[tuple[str, str], int] = {}
    for forum_id, note_id, signature in aconn2.execute(
        "SELECT forum_id, note_id, signature FROM messages"
        " WHERE year = 2026 AND kind = 'official_review' AND signature IS NOT NULL"
    ):
        if note_id in rating:
            suffix = signature.rsplit("/", 1)[-1]
            sig_rating[(forum_id, suffix.split("_")[-1])] = rating[note_id]
    aconn2.close()

    conn = sqlite3.connect(f"file:{DIRECT_DIR / 'units.sqlite3'}?mode=ro", uri=True)
    pairs = conn.execute(
        "SELECT DISTINCT custom_id, forum_id, reviewer_key FROM units"
        " WHERE year = 2026 AND reviewer_role = 'official_reviewer'"
    ).fetchall()
    reviewer_rating: dict[tuple[str, str], int] = {}
    for cid, forum_id, rkey in pairs:
        r = sig_rating.get((forum_id, rkey.split("_")[-1]))
        if r is not None:
            reviewer_rating[(cid, rkey)] = r
    print(f"mapped {len(reviewer_rating)}/{len(pairs)} reviewer ratings via signatures")

    # per reviewer: post-response movement
    movement: dict[tuple[str, str], dict] = {}
    for cid, rkey, post, soft, strong in conn.execute(
        "SELECT custom_id, reviewer_key,"
        " SUM(temporal_position = 'post_author_response'),"
        " SUM(temporal_position = 'post_author_response' AND judgment_change IN ('weakened','reversed')),"
        " SUM(temporal_position = 'post_author_response' AND judgment_change = 'strengthened')"
        " FROM units WHERE year = 2026 AND reviewer_role = 'official_reviewer'"
        " GROUP BY 1, 2"
    ):
        movement[(cid, rkey)] = {"post": post, "soft": soft > 0, "strong": strong > 0}
    conn.close()

    forums: dict[str, list] = defaultdict(list)
    for key, r in reviewer_rating.items():
        if key in movement:
            forums[key[0]].append((key[1], r, movement[key]))

    def agg(reviewers):
        n = len(reviewers)
        if not n:
            return None
        has_post = sum(1 for _, _, m in reviewers if m["post"])
        return {
            "n": n,
            "post_rate": round(has_post / n, 4),
            "soften": round(sum(1 for _, _, m in reviewers if m["soft"]) / n, 4),
            "strengthen": round(sum(1 for _, _, m in reviewers if m["strong"]) / n, 4),
        }

    all_rev, split_low, split_high, split_mid, unan = [], [], [], [], []
    n_split_forums = 0
    for cid, revs in forums.items():
        if len(revs) < 2:
            continue
        rs = [r for _, r, _ in revs]
        all_rev.extend(revs)
        if max(rs) - min(rs) >= 4:
            n_split_forums += 1
            for rv in revs:
                if rv[1] == min(rs):
                    split_low.append(rv)
                elif rv[1] == max(rs):
                    split_high.append(rv)
                else:
                    split_mid.append(rv)
        elif max(rs) == min(rs):
            unan.extend(revs)

    payload = {
        "n_split_forums": n_split_forums,
        "groups": {
            "all_reviewers": agg(all_rev),
            "split_low_scorer": agg(split_low),
            "split_mid": agg(split_mid),
            "split_high_scorer": agg(split_high),
            "unanimous_forums": agg(unan),
        },
    }
    (DIRECT_DIR / "yield-data.json").write_text(json.dumps(payload) + "\n")
    print(json.dumps(payload, indent=1))


if __name__ == "__main__":
    main()
