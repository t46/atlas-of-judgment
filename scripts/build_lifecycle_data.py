"""The fate of an objection: raised -> revisited -> held / softened / hardened.

Unit of analysis: a (forum, reviewer, object) triple where the reviewer's
initial review contained at least one negative unit on that object ("an
objection raised"). We then ask whether the same reviewer produced any
post-author-response unit on the same object ("revisited"), and if so
whether any of those units record a weakened/reversed judgment ("softened")
or a strengthened one ("hardened").

All ICLR years pooled (Direct track, official reviewers).

Writes data/analysis/iclr/unit-taxonomy-2026-v1/lifecycle-data.json.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"


def main() -> None:
    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)

    raised: set = set()
    for f, r, o in conn.execute(
        "SELECT DISTINCT u.custom_id, u.reviewer_key, l.object_key FROM units u"
        " JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'initial_review'"
        " AND u.valence = 'negative'"
    ):
        raised.add((f, r, o))

    revisited: dict = defaultdict(lambda: {"any": False, "soft": False, "hard": False})
    for f, r, o, soft, hard in conn.execute(
        "SELECT u.custom_id, u.reviewer_key, l.object_key,"
        " SUM(u.judgment_change IN ('weakened','reversed')),"
        " SUM(u.judgment_change = 'strengthened')"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.reviewer_role = 'official_reviewer' AND u.temporal_position = 'post_author_response'"
        " GROUP BY 1, 2, 3"
    ):
        k = (f, r, o)
        revisited[k]["any"] = True
        revisited[k]["soft"] = soft > 0
        revisited[k]["hard"] = hard > 0
    conn.close()

    per_obj: dict = defaultdict(lambda: {"raised": 0, "revisited": 0, "soft": 0, "hard": 0})
    for k in raised:
        o = k[2]
        st = per_obj[o]
        st["raised"] += 1
        rv = revisited.get(k)
        if rv and rv["any"]:
            st["revisited"] += 1
            if rv["soft"]:
                st["soft"] += 1
            if rv["hard"]:
                st["hard"] += 1

    objects = {}
    for o, st in sorted(per_obj.items(), key=lambda kv: -kv[1]["raised"]):
        objects[o] = {
            "raised": st["raised"],
            "revisit_rate": round(st["revisited"] / st["raised"], 4),
            "soften_given_revisit": round(st["soft"] / st["revisited"], 4) if st["revisited"] else None,
            "harden_given_revisit": round(st["hard"] / st["revisited"], 4) if st["revisited"] else None,
            "soften_overall": round(st["soft"] / st["raised"], 4),
        }
        print(f"{o:26s} raised={st['raised']:6d} revisit={st['revisited']/st['raised']:.1%}"
              f" soften|rev={st['soft']/max(1,st['revisited']):.1%} harden|rev={st['hard']/max(1,st['revisited']):.1%}")

    (V / "lifecycle-data.json").write_text(json.dumps({"objects": objects}) + "\n")


if __name__ == "__main__":
    main()
