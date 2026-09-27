"""Pipeline orchestration for the beacon fixture."""

from pathlib import Path

from beacon.report import write_report
from beacon.sources import discover, load_source


def count_words(text: str) -> int:
    return len(text.split())


def run(data_root: Path, report_path: Path) -> list[tuple[str, int]]:
    rows = []
    for source in discover(data_root):
        rows.append((source.stem, count_words(load_source(source))))
    write_report(report_path, rows)
    return rows
