from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .book_reader import build_book_index, extract_unit_text
from .reading_pipeline import ReadingPipelineRecord, run_reading_pipeline
from .source_blocks import split_source_blocks
from .source_index import build_book_source_index, save_book_source_index
from .backend import (
    DEEPSEEK_DEFAULT_MAX_RETRIES,
    DEEPSEEK_DEFAULT_TIMEOUT,
    DEFAULT_MAX_TOKENS,
    DeepSeekBackend,
    ExtractionError,
)
from .extraction_quality import validate_extraction_quality


def _add_llm_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout", type=float, default=DEEPSEEK_DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEEPSEEK_DEFAULT_MAX_RETRIES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilusion-reader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build a structure index for a book")
    index_parser.add_argument("book")
    index_parser.add_argument("--format", choices=["json", "text"], default="text")

    extract_parser = subparsers.add_parser("extract", help="Extract text for a structural unit")
    extract_parser.add_argument("book")
    extract_parser.add_argument("unit_id")

    run_reading_parser = subparsers.add_parser(
        "run-reading",
        help="Run the source-grounded reading pipeline for one unit",
    )
    run_reading_parser.add_argument("book")
    run_reading_parser.add_argument("unit_id")
    _add_llm_backend_args(run_reading_parser)
    run_reading_parser.add_argument("--cache-dir", default=".tilusion_cache")
    run_reading_parser.add_argument("--no-cache", action="store_true")
    run_reading_parser.add_argument(
        "--scope", choices=["unit", "book"], default="unit",
        help="Extraction scope: unit (isolated, default) or book (cross-unit with registry)",
    )
    run_reading_parser.add_argument(
        "--json", action="store_true",
        help="Print full pipeline record as JSON to stdout (default: compact summary)",
    )

    source_index_parser = subparsers.add_parser(
        "source-index",
        help="Build the deterministic book-scoped source block index",
    )
    source_index_parser.add_argument("book")
    source_index_parser.add_argument("--cache-dir", default=".tilusion_cache")
    source_index_parser.add_argument("--format", choices=["json", "text"], default="text")

    split_blocks_parser = subparsers.add_parser(
        "split-blocks",
        help="Split one unit into deterministic source blocks without LLM calls",
    )
    split_blocks_parser.add_argument("book")
    split_blocks_parser.add_argument("unit_id")
    split_blocks_parser.add_argument("--segment-id", default=None)
    split_blocks_parser.add_argument("--format", choices=["json", "text"], default="text")
    split_blocks_parser.add_argument(
        "--include-text",
        action="store_true",
        help="Include full source block text in text output instead of previews",
    )

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

    if args.command == "run-reading":
        try:
            record = run_reading_pipeline(
                args.book,
                args.unit_id,
                backend=build_reading_backend(args),
                cache_dir=args.cache_dir,
                use_cache=not args.no_cache,
                scope=args.scope,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"reading pipeline failed: {error}", file=sys.stderr)
            return 1
        if args.json:
            print(record.to_json())
        else:
            print(format_pipeline_record_text(record))
        print(f"package: {record.unit_package_path}", file=sys.stderr)
        return 0

    if args.command == "source-index":
        try:
            payload = build_book_source_index(args.book)
            output_path = save_book_source_index(payload, args.book, cache_root=args.cache_dir)
        except (OSError, ValueError, KeyError) as error:
            print(f"source index failed: {error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_source_index_text(payload, output_path=output_path))
        return 0

    if args.command == "split-blocks":
        try:
            payload = split_unit_source_blocks(
                args.book,
                args.unit_id,
                segment_id=args.segment_id,
            )
        except (OSError, ValueError, KeyError) as error:
            print(f"source block split failed: {error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_source_blocks_text(payload, include_text=args.include_text))
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


def format_pipeline_record_text(record: ReadingPipelineRecord) -> str:
    """Compact human-readable summary of a pipeline run (no LLM output dump)."""
    validation = record.validation
    lines = [
        f"unit_id: {record.unit_id}",
        f"elapsed: {record.elapsed_ms}ms",
        f"package: {record.unit_package_path}",
        f"validation: {'PASSED' if validation.get('passed') else 'FAILED'} "
        f"({validation.get('error_count', 0)} errors, "
        f"{validation.get('warning_count', 0)} warnings)",
        "passes:",
    ]
    for name, summary in record.passes.items():
        cache_status = "cached" if summary.get("cache_hit") else "live"
        elapsed = summary.get("elapsed_ms", 0)
        parts = [f"  {name}: {cache_status} ({elapsed}ms)"]
        if "segment_count" in summary:
            parts.append(f"{summary['segment_count']} segments")
        if "resolved_segment_count" in summary:
            parts.append(f"{summary['resolved_segment_count']} resolved")
        if "repair_hint_count" in summary:
            parts.append(f"{summary['repair_hint_count']} repairs")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def format_source_index_text(payload: dict, *, output_path: Path) -> str:
    metrics = payload.get("metrics", {})
    lines = [
        f"source_index_id: {payload.get('source_index_id', '')}",
        f"book_id: {payload.get('book_id', '')}",
        f"source_format: {payload.get('source_format', '')}",
        (
            f"units: {metrics.get('unit_count', 0)}, "
            f"blocks: {metrics.get('block_count', 0)}, "
            f"chars: {metrics.get('total_chars', 0)}, "
            f"avg_block_size: {metrics.get('avg_block_size', 0)}"
        ),
        f"path: {output_path}",
    ]
    return "\n".join(lines)


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


def split_unit_source_blocks(
    book: str | Path,
    unit_id: str,
    *,
    segment_id: str | None = None,
) -> dict:
    index = build_book_index(book)
    unit = index.unit_map().get(unit_id)
    if unit is None:
        raise ValueError(f"unknown unit_id: {unit_id}")
    text = extract_unit_text(book, unit)
    effective_segment_id = segment_id or unit_id
    blocks, metrics = split_source_blocks(
        text,
        segment_id=effective_segment_id,
        unit_id=unit_id,
        unit_text=text,
        unit_offset=0,
    )
    return {
        "unit_id": unit_id,
        "segment_id": effective_segment_id,
        "source": {
            "book_path": str(book),
            "book_title": index.title or "",
            "unit_label": unit.label,
            "unit_kind": unit.kind,
        },
        "metrics": metrics.to_dict(),
        "source_blocks": [block.to_dict() for block in blocks],
    }


def format_source_blocks_text(payload: dict, *, include_text: bool = False) -> str:
    metrics = payload.get("metrics", {})
    lines = [
        f"unit_id: {payload.get('unit_id', '')}",
        f"segment_id: {payload.get('segment_id', '')}",
        (
            f"blocks: {metrics.get('block_count', 0)}, "
            f"coverage: {metrics.get('coverage_pct', 0)}%, "
            f"avg_size: {metrics.get('avg_block_size', 0)}, "
            f"oversized: {metrics.get('oversized_count', 0)}"
        ),
    ]
    for block in payload.get("source_blocks", []):
        text = block.get("text", "")
        preview = text if include_text else text.replace("\n", " ")[:120]
        if not include_text and len(text.replace("\n", " ")) > 120:
            preview += "..."
        lines.append(
            f"- {block.get('block_id')}: {block.get('block_type')} "
            f"[{block.get('start')}, {block.get('end')}) {preview}"
        )
    return "\n".join(lines)


def build_reading_backend(args):
    if args.backend == "mock":
        return None
    return DeepSeekBackend(
        args.model,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        max_tokens=args.max_tokens,
        timeout=getattr(args, "timeout", DEEPSEEK_DEFAULT_TIMEOUT),
        max_retries=getattr(args, "retries", DEEPSEEK_DEFAULT_MAX_RETRIES),
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
