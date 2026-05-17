from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .book_reader import build_book_index, extract_unit_text
from .extraction import (
    DEFAULT_MAX_TOKENS,
    DeepSeekBackend,
    ExtractionError,
    MockExtractionBackend,
)
from .extraction_pipeline import (
    refresh_chain_validation_cache,
    run_chained_extraction,
    run_segment_extraction_pass,
)
from .extraction_quality import validate_extraction_quality


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
    run_pass_parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    run_pass_parser.add_argument("--cache-dir", default=".tilusion_cache/extraction_passes")
    run_pass_parser.add_argument("--no-cache", action="store_true")

    run_chain_parser = subparsers.add_parser(
        "run-chain", help="Run overview segmentation plus per-segment extraction"
    )
    run_chain_parser.add_argument("book")
    run_chain_parser.add_argument("unit_id")
    run_chain_parser.add_argument("--backend", choices=["mock", "deepseek"], default="mock")
    run_chain_parser.add_argument("--model", default="deepseek-v4-flash")
    run_chain_parser.add_argument("--thinking", action="store_true")
    run_chain_parser.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    run_chain_parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    run_chain_parser.add_argument("--cache-dir", default=".tilusion_cache/extraction_chains")
    run_chain_parser.add_argument("--no-cache", action="store_true")

    refresh_chain_parser = subparsers.add_parser(
        "refresh-chain-validation",
        help="Recompute validation artifacts for an existing chain cache without LLM calls",
    )
    refresh_chain_parser.add_argument("chain_cache_dir")
    refresh_chain_parser.add_argument("--format", choices=["json", "text"], default="text")

    validate_parser = subparsers.add_parser(
        "validate-result", help="Validate an extraction result against source text"
    )
    validate_parser.add_argument("book")
    validate_parser.add_argument("unit_id")
    validate_parser.add_argument("result_json")
    validate_parser.add_argument("--format", choices=["json", "text"], default="text")
    validate_parser.add_argument(
        "--repair-payload",
        action="store_true",
        help="Print the compact LLM repair-feedback payload",
    )

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
        try:
            backend = build_backend(args)
            record = run_segment_extraction_pass(
                args.book,
                args.unit_id,
                backend=backend,
                cache_dir=args.cache_dir,
                use_cache=not args.no_cache,
            )
        except (ExtractionError, ValueError) as error:
            print(f"extraction failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        return 0

    if args.command == "run-chain":
        try:
            record = run_chained_extraction(
                args.book,
                args.unit_id,
                backend=build_backend(args),
                cache_dir=args.cache_dir,
                use_cache=not args.no_cache,
            )
        except (ExtractionError, ValueError) as error:
            print(f"extraction chain failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        return 0

    if args.command == "refresh-chain-validation":
        try:
            manifest = refresh_chain_validation_cache(args.chain_cache_dir)
        except (OSError, ValueError, KeyError) as error:
            print(f"validation refresh failed: {error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        else:
            print(format_chain_refresh_text(manifest))
        return 0

    if args.command == "validate-result":
        index = build_book_index(args.book)
        unit = index.unit_map().get(args.unit_id)
        if unit is None:
            parser.error(f"unknown unit_id: {args.unit_id}")
        result_payload = json.loads(Path(args.result_json).read_text(encoding="utf-8"))
        data = result_payload.get("data", result_payload)
        if not isinstance(data, dict):
            parser.error("result_json must contain an extraction object or a result with a data object")
        unit_text = extract_unit_text(args.book, unit)
        report = validate_extraction_quality(data, unit_text, expected_unit_id=args.unit_id)
        payload = report.to_repair_payload() if args.repair_payload else report.to_dict()
        if args.repair_payload or args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_quality_report_text(report))
        return 0 if report.passed else 1

    parser.error("unknown command")
    return 2


def format_quality_report_text(report) -> str:
    lines = [
        f"unit_id: {report.unit_id}",
        f"passed: {str(report.passed).lower()}",
        f"issues: {report.issue_count} ({report.error_count} errors, {report.warning_count} warnings)",
    ]
    for issue in report.issues:
        lines.append(
            f"- {issue.severity} {issue.code} at {issue.path}: {issue.message} "
            f"repair: {issue.repair_hint}"
        )
    return "\n".join(lines)


def format_chain_refresh_text(manifest: dict) -> str:
    validation = manifest["validation_report"]
    repair_hints = manifest["repair_hints"]
    lines = [
        f"chain_cache_dir: {manifest['cache_dir']}",
        f"unit_id: {manifest['unit_id']}",
        f"passed: {str(validation['passed']).lower()}",
        f"issues: {validation['error_count']} errors, {validation['warning_count']} warnings",
        f"overview_cache_hit: {str(manifest['overview']['cache_hit']).lower()}",
        f"segment_passes: {len(manifest.get('segment_passes', []))}",
        f"ready_for_llm_repair: {str(repair_hints['ready_for_llm_repair']).lower()}",
        f"validation_report: {manifest['artifact_paths']['validation_report']}",
        f"repair_hints: {manifest['artifact_paths']['repair_hints']}",
    ]
    return "\n".join(lines)


def build_backend(args):
    return (
        MockExtractionBackend()
        if args.backend == "mock"
        else DeepSeekBackend(
            args.model,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
