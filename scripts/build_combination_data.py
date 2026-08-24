"""Stage 2 for the combination clause: named clusters for display.

Names were drafted by a sonnet naming agent over the stage-1 exemplars
(combination-raw.json) and reviewed by the analyst; four near-duplicate
referent clusters merge into one generic charge. The full raw clusters
ship with the dataset so the borders can be redrawn.

Writes data/analysis/iclr/unit-taxonomy-2026-v1/combination-data.json.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V = PROJECT_ROOT / "data/analysis/iclr/unit-taxonomy-2026-v1"

EXC = {
    0: ("The stacking-value clause", "redeemed only when the paper delivers value beyond stacking existing tools — theoretical or practical, but new", "value"),
    1: ("The new-insight clause", "redeemed only by a genuine new insight the parts did not already contain", "insight"),
    2: ("The threshold, restated", "no exception at all — a restatement of the significance bar itself", "threshold"),
    3: ("The clear-differentiation clause", "redeemed only by explicit differentiation from the methods being adapted", "differentiation"),
    4: ("The new-mechanism clause", "redeemed only by a fundamentally new mechanism, not a new arrangement", "mechanism"),
    5: ("The isolated-contribution clause", "redeemed only when the unique contribution can be cleanly isolated from the assembled parts", "isolation"),
    6: ("The reproduction floor", "not a combination exception — a separate boundary: reproducing known results is no contribution", "reproduction"),
    7: ("The integration-innovation clause", "redeemed only by methodological innovation beyond engineering-level integration", "integration"),
}
REF = {
    0: ("The component lineup", "each named part of the method already exists in prior work — the parts are recited", "component_lineup"),
    1: ("The named-precedent match", "the assembly as a whole resembles one specific named prior technique", "named_precedent"),
    2: ("The generic combination charge", "“combines existing techniques” with no part named and no mechanism specified", "generic"),
    3: ("The generic combination charge", "“combines existing techniques” with no part named and no mechanism specified", "generic"),
    4: ("The new-problem, old-tool charge", "the problem is granted as new; the technique applied to it is the offense", "newproblem"),
    5: ("The generic combination charge", "“combines existing techniques” with no part named and no mechanism specified", "generic"),
    6: ("The generic combination charge", "“combines existing techniques” with no part named and no mechanism specified", "generic"),
    7: ("The no-new-insight charge", "known techniques combined without demonstrating any new technical insight", "noinsight"),
}


def merge(raw_clusters, names):
    agg = {}
    for i, c in enumerate(raw_clusters):
        name, d, key = names[i]
        a = agg.setdefault(key, {"name": name, "def": d, "n": 0, "exemplars": []})
        a["n"] += c["n"]
        a["exemplars"].extend(c["exemplars"][:2])
    total = sum(a["n"] for a in agg.values())
    out = sorted(agg.values(), key=lambda a: -a["n"])
    for a in out:
        a["share"] = round(a["n"] / total, 4)
        a["exemplars"] = a["exemplars"][:3]
    return out


def main() -> None:
    raw = json.loads((V / "combination-raw.json").read_text())
    payload = {
        "n_sentences": raw["n_combo_sentences"],
        "n_units": raw["n_combo_units"],
        "conditional_share": raw["conditional_share"],
        "exceptions": merge(raw["exceptions"], EXC),
        "referents": merge(raw["referents"], REF),
    }
    (V / "combination-data.json").write_text(json.dumps(payload))
    for a in payload["exceptions"]:
        print(f"  exc {a['share']:.0%}  {a['name']}")
    for a in payload["referents"]:
        print(f"  ref {a['share']:.0%}  {a['name']}")


if __name__ == "__main__":
    main()
