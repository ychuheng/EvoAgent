"""Command-line entry point for the Fieldnotes fixture."""

import argparse
from pathlib import Path

from fieldnotes.render import render_notes
from fieldnotes.store import add_note, load_notes


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=Path("notes.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("title")
    add.add_argument("body")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    if args.command == "add":
        add_note(args.file, args.title, args.body)
        return "saved"
    return render_notes(load_notes(args.file))


if __name__ == "__main__":
    print(main())
