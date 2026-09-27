"""Pipeline orchestration for the beacon fixture."""

from pathlib import Path

from beacon.report import write_report
from beacon.sources import discover, load_source


def count_words(text: str) -> int:
    return len(text.split())


def average_words(rows: list[tuple[str, int]]) -> float:
    """已知缺陷：rows 为空时直接除，会抛 ZeroDivisionError。"""

    total = sum(count for _, count in rows)
    return total / len(rows)


def run(data_root: Path, report_path: Path) -> list[tuple[str, int]]:
    rows = []
    for source in discover(data_root):
        rows.append((source.stem, count_words(load_source(source))))
    write_report(report_path, rows)
    return rows
