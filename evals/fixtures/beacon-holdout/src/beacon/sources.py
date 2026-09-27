"""Source readers for the beacon pipeline."""

from pathlib import Path


def load_source(path: Path) -> str:
    """Read a whole source file into memory."""

    return path.read_text(encoding="utf-8")


def discover(data_root: Path) -> list[Path]:
    """Find text sources directly under the data root."""

    return sorted(data_root.glob("*.txt"))
