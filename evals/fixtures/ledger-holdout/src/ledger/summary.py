"""Render a stable text summary of ledger entries."""

from pathlib import Path

from ledger.attribute import total_for
from ledger.ingest import read_entries


def render_summary(path: Path) -> str:
    entities = sorted({str(entry.get("entity")) for entry in read_entries(path)})
    lines = []
    for entity in entities:
        lines.append(f"{entity}: {total_for(path, entity)}")
    return "\n".join(lines)
