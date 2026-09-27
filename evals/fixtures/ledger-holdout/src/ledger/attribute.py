"""Attribution rules for ledger entries."""

from pathlib import Path

from ledger.config import DEFAULT_CURRENCY
from ledger.ingest import read_entries


def currency_of(entry: dict[str, object]) -> str:
    value = entry.get("currency")
    if isinstance(value, str) and value:
        return value
    return DEFAULT_CURRENCY


def total_for(path: Path, entity: str) -> int:
    total = 0
    for entry in read_entries(path):
        if entry.get("entity") == entity:
            total += int(entry.get("amount", 0))
    return total
