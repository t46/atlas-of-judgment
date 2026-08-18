"""Migrate malformed Episode Lite IDs to source-derived stable IDs.

The corrections are explicit so the migration is reproducible and auditable.
Only current canonical Lite/Deep analysis artifacts are changed; archives,
backups, logs, and prompt-tuning fixtures remain historical snapshots.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOTS = (
    Path("data/analysis/iclr/episode-lite-1000"),
    Path("data/analysis/iclr/episode-deep-63"),
)
EXCLUDED_PARTS = {"logs", "prompt-tuning"}
INCLUDED_SUFFIXES = {".json", ".jsonl", ".md"}

CORRECTIONS = {
    "E-gJjRdLG5MY-uYsBdhyDD8-NN": "E-gJjRdLG5MY-uYsBdhyDD8-01",
    "E-bFYfV6c9zu-Lza38FQrrx-NN": "E-bFYfV6c9zu-Lza38FQrrx-01",
    "E-3GxK1wN8Qp-4IiYxGzGmr-01": "E-eYLIjg9Sj2-4IiYxGzGmr-01",
    "E-3GxK1wN8Qp-4IiYxGzGmr-02": "E-eYLIjg9Sj2-4IiYxGzGmr-02",
    "E-3GxK1wN8Qp-4IiYxGzGmr-03": "E-eYLIjg9Sj2-4IiYxGzGmr-03",
    "E-599n8b5rUz-599n8b5rUz-01": "E-1yXsMYyZVj-599n8b5rUz-01",
    "E-599n8b5rUz-599n8b5rUz-02": "E-1yXsMYyZVj-599n8b5rUz-02",
    "E-599n8b5rUz-599n8b5rUz-03": "E-1yXsMYyZVj-599n8b5rUz-03",
    "E-5vH1M5oYI2-5vH1M5oYI2-02": "E-bknuCt8MI7-5vH1M5oYI2-02",
    "E-5vH1M5oYI2-5vH1M5oYI2-03": "E-bknuCt8MI7-5vH1M5oYI2-03",
    "E-5vH1M5oYI2-5vH1M5oYI2-04": "E-bknuCt8MI7-5vH1M5oYI2-04",
    "E-5vH1M5oYI2-fqXGkWKEW6-01": "E-bknuCt8MI7-5vH1M5oYI2-01",
    "E-I7WpRpgKJ3-JrWpxBqqFk-01": "E-JrWpxBqqFk-I7WpRpgKJ3-01",
    "E-I7WpRpgKJ3-JrWpxBqqFk-02": "E-JrWpxBqqFk-I7WpRpgKJ3-02",
    "E-VJZ477R89-1hg95hE2Id-01": "E-VJZ477R89F-1hg95hE2Id-01",
    "E-VJZ477R89-1hg95hE2Id-02": "E-VJZ477R89F-1hg95hE2Id-02",
    "E-VJZ477R89-1hg95hE2Id-03": "E-VJZ477R89F-1hg95hE2Id-03",
    "E-WRHTlvNhxB-WRHTlvNhxB-01": "E-B7otvE3ExU-WRHTlvNhxB-01",
    "E-WRHTlvNhxB-WRHTlvNhxB-02": "E-B7otvE3ExU-WRHTlvNhxB-02",
    "E-WRHTlvNhxB-WRHTlvNhxB-03": "E-B7otvE3ExU-WRHTlvNhxB-03",
    "E-btEiAfnLs-Zy2BjeU5vc-01": "E-btEiAfnLsX-Zy2BjeU5vc-01",
    "E-qe92CyrdjQ-qe92CyrdjQ-01": "E-M5LifvgXs9-qe92CyrdjQ-01",
    "E-qe92CyrdjQ-qe92CyrdjQ-02": "E-M5LifvgXs9-qe92CyrdjQ-02",
    "E-qe92CyrdjQ-qe92CyrdjQ-03": "E-M5LifvgXs9-qe92CyrdjQ-03",
    "E-qe92CyrdjQ-qe92CyrdjQ-04": "E-M5LifvgXs9-qe92CyrdjQ-04",
    "E-qe92CyrdjQ-qe92CyrdjQ-05": "E-M5LifvgXs9-qe92CyrdjQ-05",
    "E-xxkIl8HUy2-xxkIl8HUy2-01": "E-n3u7PK2kyd-xxkIl8HUy2-01",
    "E-xxkIl8HUy2-xxkIl8HUy2-02": "E-n3u7PK2kyd-xxkIl8HUy2-02",
}


def included(path: Path) -> bool:
    if path.suffix not in INCLUDED_SUFFIXES:
        return False
    return not any(
        part in EXCLUDED_PARTS
        or part.startswith("archive")
        or "backup" in part
        for part in path.parts
    )


def migrate() -> dict[str, object]:
    if len(set(CORRECTIONS.values())) != len(CORRECTIONS):
        raise ValueError("correction targets must be unique")
    changed_files: list[str] = []
    replacement_counts = {old: 0 for old in CORRECTIONS}
    for root in ROOTS:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not included(path):
                continue
            text = path.read_text(encoding="utf-8")
            updated = text
            for old, new in CORRECTIONS.items():
                count = updated.count(old)
                if count:
                    replacement_counts[old] += count
                    updated = updated.replace(old, new)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
                changed_files.append(str(path))
    return {
        "correction_count": len(CORRECTIONS),
        "changed_file_count": len(changed_files),
        "replacement_count": sum(replacement_counts.values()),
        "unused_corrections": [
            old for old, count in replacement_counts.items() if count == 0
        ],
        "changed_files": changed_files,
    }


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=False, indent=2))
