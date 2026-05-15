from __future__ import annotations

import argparse
import sys

from .book_reader import build_book_index, extract_unit_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilusion-reader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build a structure index for a book")
    index_parser.add_argument("book")
    index_parser.add_argument("--format", choices=["json", "text"], default="text")

    extract_parser = subparsers.add_parser("extract", help="Extract text for a structural unit")
    extract_parser.add_argument("book")
    extract_parser.add_argument("unit_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "index":
        index = build_book_index(args.book)
        print(index.to_json() if args.format == "json" else index.to_outline())
        return 0

    if args.command == "extract":
        index = build_book_index(args.book)
        unit = index.unit_map().get(args.unit_id)
        if unit is None:
            parser.error(f"unknown unit_id: {args.unit_id}")
        print(extract_unit_text(args.book, unit))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
