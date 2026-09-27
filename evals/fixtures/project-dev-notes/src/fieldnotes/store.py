"""Read and write a list of notes in a JSON file."""

import json
from pathlib import Path


def load_notes(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def add_note(path: Path, title: str, body: str) -> None:
    notes = load_notes(path)
    notes.append({"title": title, "body": body})
    path.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
