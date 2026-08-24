"""The deliberation: exemplar forums scored as full per-reviewer trajectories.

Selects ~14 ICLR 2026 forums (3-4 official reviewers, 22-64 units, ratings
joinable via signature suffixes) across five scenarios — a softening, an
entrenchment, a reversal, a split verdict, unanimity — and exports every unit
in order: reviewer, phase (initial / post-response), judgment change, valence,
object, and the unit's judgment text for hover.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/deliberation-data.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"
D = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-direct-v1"
ANALYSIS_DB = PROJECT_ROOT / "data/processed/iclr/analysis.sqlite3"


def main() -> None:
    aconn = sqlite3.connect(f"file:{ANALYSIS_DB}?mode=ro", uri=True)
    rating_of_note = {}
    for note_id, cj in aconn.execute(
        "SELECT note_id, content_json FROM messages WHERE year = 2026 AND kind = 'official_review'"
    ):
        try:
            r = json.loads(cj).get("rating")
        except json.JSONDecodeError:
            continue
        if isinstance(r, int):
            rating_of_note[note_id] = r
    sig_rating: dict[tuple[str, str], int] = {}
    n_comments: dict[str, int] = defaultdict(int)
    titles, decisions = {}, {}
    for forum_id, note_id, kind, signature in aconn.execute(
        "SELECT forum_id, note_id, kind, signature FROM messages WHERE year = 2026"
    ):
        if kind == "official_review" and signature and note_id in rating_of_note:
            sig_rating[(forum_id, signature.rsplit("/", 1)[-1].split("_")[-1])] = rating_of_note[note_id]
        elif kind == "official_comment":
            n_comments[forum_id] += 1
    for forum_id, title, decision in aconn.execute(
        "SELECT forum_id, title, decision FROM papers WHERE year = 2026"
    ):
        titles[forum_id] = title
        decisions[forum_id] = decision
    aconn.close()

    conn = sqlite3.connect(f"file:{D / 'units.sqlite3'}?mode=ro", uri=True)
    forums: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT u.custom_id, u.forum_id, u.reviewer_key, u.unit_index, u.temporal_position,"
        " u.judgment_change, u.valence, l.object_key, u.judgment"
        " FROM units u JOIN unit_labels l ON l.unit_pk = u.unit_pk"
        " WHERE u.year = 2026 AND u.reviewer_role = 'official_reviewer'"
        " AND u.temporal_position IN ('initial_review','post_author_response')"
    ):
        cid, fid, rk, idx, phase, chg, val, obj, jtext = row
        f = forums.setdefault(cid, {"forum_id": fid, "revs": defaultdict(list)})
        f["revs"][rk].append({
            "i": idx, "phase": "post" if phase == "post_author_response" else "init",
            "chg": chg if chg in ("strengthened", "weakened", "reversed", "clarified") else None,
            "v": val, "o": obj, "t": (jtext or "")[:170],
        })
    conn.close()

    scored = []
    for cid, f in forums.items():
        revs = f["revs"]
        if not (3 <= len(revs) <= 4):
            continue
        n = sum(len(u) for u in revs.values())
        if not (22 <= n <= 64):
            continue
        rats = {}
        for rk in revs:
            r = sig_rating.get((f["forum_id"], rk.split("_")[-1]))
            if r is not None:
                rats[rk] = r
        if len(rats) < len(revs):
            continue
        soft = sum(1 for us in revs.values() for u in us if u["chg"] in ("weakened", "reversed"))
        hard = sum(1 for us in revs.values() for u in us if u["chg"] == "strengthened")
        rev = sum(1 for us in revs.values() for u in us if u["chg"] == "reversed")
        post = sum(1 for us in revs.values() for u in us if u["phase"] == "post")
        gap = max(rats.values()) - min(rats.values())
        scored.append({"cid": cid, "f": f, "rats": rats, "soft": soft, "hard": hard,
                       "rev": rev, "post": post, "gap": gap, "n": n})

    def pick(label, key, cnt):
        out = []
        for s in sorted(scored, key=key, reverse=True)[:cnt]:
            if any(s["cid"] == o["cid"] for o in chosen):
                continue
            s["scenario"] = label
            out.append(s)
            chosen.append(s)
        return out

    chosen: list = []
    pick("a softening", lambda s: (s["soft"], s["post"]), 3)
    pick("an entrenchment", lambda s: (s["hard"] if s["soft"] == 0 else -1, s["post"]), 3)
    pick("a reversal", lambda s: (s["rev"], s["post"]), 2)
    pick("a split verdict", lambda s: (s["gap"], s["post"]), 3)
    pick("unanimity", lambda s: (-s["gap"] * 10 + s["post"], s["n"]), 3)

    payload = []
    for s in chosen:
        f = s["f"]
        payload.append({
            "scenario": s["scenario"],
            "forum": f["forum_id"],
            "title": (titles.get(f["forum_id"]) or f["forum_id"])[:96],
            "decision": decisions.get(f["forum_id"]),
            "comments": n_comments.get(f["forum_id"], 0),
            "reviewers": [
                {"key": rk, "rating": s["rats"][rk],
                 "units": sorted(us, key=lambda u: u["i"])}
                for rk, us in sorted(f["revs"].items(), key=lambda kv: -s["rats"][kv[0]])
            ],
        })
        print(f'{s["scenario"]:>16s}  {f["forum_id"]}  n={s["n"]:2d} post={s["post"]:2d} soft={s["soft"]} hard={s["hard"]} gap={s["gap"]}  {payload[-1]["title"][:40]}')

    # ---- the population: the same primitives over every 2026 panel with
    # >=2 rating-matched official reviewers (no size filter), so the case
    # files can be placed against how deliberations usually go ----
    pop = {"n_panels": 0, "softening": 0, "entrenchment": 0, "reversal": 0,
           "split": 0, "unanimity": 0, "no_movement": 0}
    move_units = Counter()
    for cid, f in forums.items():
        revs = f["revs"]
        if len(revs) < 2:
            continue
        rats = {}
        for rk in revs:
            r = sig_rating.get((f["forum_id"], rk.split("_")[-1]))
            if r is not None:
                rats[rk] = r
        if len(rats) < 2:
            continue
        soft = sum(1 for us in revs.values() for u in us if u["chg"] in ("weakened", "reversed"))
        hard = sum(1 for us in revs.values() for u in us if u["chg"] == "strengthened")
        rev = sum(1 for us in revs.values() for u in us if u["chg"] == "reversed")
        gap = max(rats.values()) - min(rats.values())
        pop["n_panels"] += 1
        if soft:
            pop["softening"] += 1
        if hard and not soft:
            pop["entrenchment"] += 1
        if rev:
            pop["reversal"] += 1
        if gap >= 4:
            pop["split"] += 1
        if gap == 0:
            pop["unanimity"] += 1
        if not any(u["chg"] for us in revs.values() for u in us):
            pop["no_movement"] += 1
        for us in revs.values():
            for u in us:
                if u["phase"] == "post" and u["chg"]:
                    move_units[u["chg"]] += 1

    (V / "deliberation-data.json").write_text(
        json.dumps({"forums": payload, "population": pop, "move_units": dict(move_units)}) + "\n"
    )
    print(f"{len(payload)} exemplar deliberations")
    print("population:", pop)
    print("move units:", dict(move_units))


if __name__ == "__main__":
    main()
