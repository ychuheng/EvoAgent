"""Read and write ledger entries."""

import json
from pathlib import Path


def read_entries(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def append_entry(path: Path, entry: dict[str, object]) -> None:
    entries = read_entries(path)
    entries.append(entry)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
