"""Command-line entry point for the beacon fixture."""

import argparse
from pathlib import Path

from beacon.pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    rows = run(args.data_root, args.report)
    return len(rows)


if __name__ == "__main__":
    raise SystemExit(main())
