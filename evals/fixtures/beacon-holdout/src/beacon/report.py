"""Report rendering for the beacon pipeline."""

from pathlib import Path


def render(rows: list[tuple[str, int]]) -> str:
    return "\n".join(f"{name}: {count}" for name, count in rows)


def write_report(path: Path, rows: list[tuple[str, int]]) -> None:
    path.write_text(render(rows) + "\n", encoding="utf-8")
