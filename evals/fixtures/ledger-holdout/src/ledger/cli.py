"""Command-line entry point for the ledger fixture."""

import argparse
from pathlib import Path

from ledger.config import RETRY_LIMIT, resolve_ledger_file
from ledger.ingest import append_entry
from ledger.summary import render_summary


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("add", "summary"))
    parser.add_argument("--file", type=Path)
    parser.add_argument("--entity")
    parser.add_argument("--amount", type=int)
    args = parser.parse_args(argv)
    path = args.file or resolve_ledger_file()
    if args.command == "add":
        append_entry(path, {"entity": args.entity, "amount": args.amount})
        return f"saved with retry limit {RETRY_LIMIT}"
    return render_summary(path)


if __name__ == "__main__":
    print(main())
