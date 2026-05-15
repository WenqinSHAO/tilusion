from __future__ import annotations

import argparse
import sys

from .book_reader import build_book_index, extract_unit_text
from .extraction import DeepSeekBackend, MockExtractionBackend, run_local_bundle_extraction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilusion-reader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build a structure index for a book")
    index_parser.add_argument("book")
    index_parser.add_argument("--format", choices=["json", "text"], default="text")

    extract_parser = subparsers.add_parser("extract", help="Extract text for a structural unit")
    extract_parser.add_argument("book")
    extract_parser.add_argument("unit_id")

    run_pass_parser = subparsers.add_parser("run-pass", help="Run one extraction pipeline pass")
    run_pass_parser.add_argument("book")
    run_pass_parser.add_argument("unit_id")
    run_pass_parser.add_argument("--pass", dest="pass_name", choices=["local-bundle"], default="local-bundle")
    run_pass_parser.add_argument("--backend", choices=["mock", "deepseek"], default="mock")
    run_pass_parser.add_argument("--model", default="deepseek-v4-flash")
    run_pass_parser.add_argument("--thinking", action="store_true")
    run_pass_parser.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    run_pass_parser.add_argument("--max-tokens", type=int, default=4096)
    run_pass_parser.add_argument("--no-cache", action="store_true")

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

    if args.command == "run-pass":
        backend = (
            MockExtractionBackend()
            if args.backend == "mock"
            else DeepSeekBackend(
                args.model,
                thinking=args.thinking,
                reasoning_effort=args.reasoning_effort,
                max_tokens=args.max_tokens,
            )
        )
        result = run_local_bundle_extraction(
            args.book,
            args.unit_id,
            backend=backend,
            use_cache=not args.no_cache,
        )
        print(result.to_json())
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
