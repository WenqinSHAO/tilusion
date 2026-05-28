from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .book_reader import build_book_index, extract_unit_text
from .reading_pipeline import run_reading_pipeline
from .source_blocks import split_source_blocks
from .extraction import (
    DEFAULT_MAX_TOKENS,
    DEEPSEEK_DEFAULT_MAX_RETRIES,
    DEEPSEEK_DEFAULT_TIMEOUT,
    DeepSeekBackend,
    ExtractionError,
    MockExtractionBackend,
)
from .extraction_pipeline import (
    refresh_chain_validation_cache,
    run_all_passes,
    run_chained_extraction,
    run_segment_extraction_pass,
    run_unit_finalization_pass,
    run_unit_repair_pass,
    run_unit_timeline_pass,
    run_unit_timeline_repair_pass,
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

    run_pass_parser = subparsers.add_parser("run-pass", help="Run one extraction pipeline pass")
    run_pass_parser.add_argument("book")
    run_pass_parser.add_argument("unit_id")
    run_pass_parser.add_argument("--pass", dest="pass_name", choices=["local-bundle"], default="local-bundle")
    _add_llm_backend_args(run_pass_parser)
    run_pass_parser.add_argument("--cache-dir", default=".tilusion_cache/extraction_passes")
    run_pass_parser.add_argument("--no-cache", action="store_true")

    run_chain_parser = subparsers.add_parser(
        "run-chain", help="Run overview segmentation plus per-segment extraction"
    )
    run_chain_parser.add_argument("book")
    run_chain_parser.add_argument("unit_id")
    _add_llm_backend_args(run_chain_parser)
    run_chain_parser.add_argument("--cache-dir", default=".tilusion_cache/extraction_chains")
    run_chain_parser.add_argument("--no-cache", action="store_true")

    refresh_chain_parser = subparsers.add_parser(
        "refresh-chain-validation",
        help="Recompute validation artifacts for an existing chain cache without LLM calls",
    )
    refresh_chain_parser.add_argument("chain_cache_dir")
    refresh_chain_parser.add_argument("--format", choices=["json", "text"], default="text")

    finalize_parser = subparsers.add_parser(
        "finalize-unit",
        help="Run unit-level finalization over an existing extraction chain cache",
    )
    finalize_parser.add_argument("chain_cache_dir")
    _add_llm_backend_args(finalize_parser)
    finalize_parser.add_argument("--no-cache", action="store_true")

    repair_parser = subparsers.add_parser(
        "repair-unit",
        help="Run unit-level repair pass over an existing unit finalization cache",
    )
    repair_parser.add_argument("finalization_pass_dir")
    _add_llm_backend_args(repair_parser)
    repair_parser.add_argument("--no-cache", action="store_true")

    timeline_parser = subparsers.add_parser(
        "timeline-unit",
        help="Construct partially-ordered timelines from repaired unit extraction",
    )
    timeline_parser.add_argument("repair_pass_dir")
    _add_llm_backend_args(timeline_parser)
    timeline_parser.add_argument("--no-cache", action="store_true")

    timeline_repair_parser = subparsers.add_parser(
        "repair-timeline",
        help="Repair specific issues in a timeline construction output",
    )
    timeline_repair_parser.add_argument("timeline_pass_dir")
    _add_llm_backend_args(timeline_repair_parser)
    timeline_repair_parser.add_argument("--no-cache", action="store_true")

    run_all_parser = subparsers.add_parser(
        "run-all",
        help="Run the full extraction pipeline (chain → finalize → repair → timeline → timeline-repair)",
    )
    run_all_parser.add_argument("book")
    run_all_parser.add_argument("unit_id")
    _add_llm_backend_args(run_all_parser)
    run_all_parser.add_argument("--cache-dir", default=".tilusion_cache")
    run_all_parser.add_argument("--no-cache", action="store_true")
    run_all_parser.add_argument(
        "--prior-unit-package",
        action="append",
        default=[],
        help="Prior unit_package.json to scan into compact book-scope context; repeatable",
    )
    run_all_parser.add_argument(
        "--skip-repair",
        action="store_true",
        help="Skip repair passes even when issues are detected",
    )

    run_reading_parser = subparsers.add_parser(
        "run-reading",
        help="Run the source-grounded reading pipeline for one unit",
    )
    run_reading_parser.add_argument("book")
    run_reading_parser.add_argument("unit_id")
    _add_llm_backend_args(run_reading_parser)
    run_reading_parser.add_argument("--cache-dir", default=".tilusion_cache/reading_passes")
    run_reading_parser.add_argument("--no-cache", action="store_true")

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

    if args.command == "finalize-unit":
        try:
            record = run_unit_finalization_pass(
                args.chain_cache_dir,
                backend=build_backend(args),
                use_cache=not args.no_cache,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"unit finalization failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        return 0

    if args.command == "repair-unit":
        _warn_if_repair_unnecessary(args.finalization_pass_dir)
        try:
            record = run_unit_repair_pass(
                args.finalization_pass_dir,
                backend=build_backend(args),
                use_cache=not args.no_cache,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"unit repair failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        return 0

    if args.command == "timeline-unit":
        try:
            record = run_unit_timeline_pass(
                args.repair_pass_dir,
                backend=build_backend(args),
                use_cache=not args.no_cache,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"unit timeline failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        return 0

    if args.command == "repair-timeline":
        _warn_if_timeline_repair_unnecessary(args.timeline_pass_dir)
        try:
            record = run_unit_timeline_repair_pass(
                args.timeline_pass_dir,
                backend=build_backend(args),
                use_cache=not args.no_cache,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"timeline repair failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        return 0

    if args.command == "run-all":
        try:
            record = run_all_passes(
                args.book,
                args.unit_id,
                backend=build_backend(args),
                cache_dir=args.cache_dir,
                use_cache=not args.no_cache,
                skip_repair=args.skip_repair,
                prior_unit_package_paths=args.prior_unit_package,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"run-all failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        print(f"package: {record.unit_package_path}", file=sys.stderr)
        return 0

    if args.command == "run-reading":
        try:
            record = run_reading_pipeline(
                args.book,
                args.unit_id,
                backend=build_reading_backend(args),
                cache_dir=args.cache_dir,
                use_cache=not args.no_cache,
            )
        except (ExtractionError, OSError, ValueError, KeyError) as error:
            print(f"reading pipeline failed: {error}", file=sys.stderr)
            return 1
        print(record.to_json())
        print(f"package: {record.unit_package_path}", file=sys.stderr)
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
    overview = validation.get("segment_quality_overview", {})
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
    if overview:
        resolved = overview["resolved_segments"]
        total = overview["total_overview_segments"]
        unresolved = overview["unresolved_segments"]
        lines.append("")
        lines.append(
            f"segments: {resolved}/{total} resolved"
            f"{' (' + str(unresolved) + ' unresolved)' if unresolved else ''}"
        )
        for reason in overview.get("unresolved_reasons", []):
            lines.append(f"  unresolved: {reason['segment_id']} — {reason['detail']}")
        if overview.get("dominant_issues"):
            lines.append(f"dominant issues: {', '.join(overview['dominant_issues'][:5])}")
        for seg in overview.get("per_segment", []):
            issue_str = ", ".join(
                f"{code}:{count}" for code, count in seg.get("issue_codes", {}).items()
            )
            evidence_parts = []
            for k, v in seg.get("evidence", {}).items():
                if v:
                    evidence_parts.append(f"{k}={v}")
            evidence_str = ", ".join(evidence_parts)
            lines.append(
                f"  {seg['segment_id']}: {seg['chars']} chars, passed={seg['passed']}"
                f", evidence=[{evidence_str}]"
                f"{', issues={' + issue_str + '}' if issue_str else ''}"
            )
    non_actionable = repair_hints.get("non_actionable_warnings", {})
    if non_actionable and non_actionable.get("total"):
        by_code = non_actionable.get("by_code", {})
        code_summary = ", ".join(f"{code}:{count}" for code, count in by_code.items())
        lines.append(
            f"non-actionable warnings: {non_actionable['total']} total"
            f"{' (' + code_summary + ')' if code_summary else ''}"
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


def build_backend(args):
    return (
        MockExtractionBackend()
        if args.backend == "mock"
        else DeepSeekBackend(
            args.model,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
            timeout=getattr(args, "timeout", DEEPSEEK_DEFAULT_TIMEOUT),
            max_retries=getattr(args, "retries", DEEPSEEK_DEFAULT_MAX_RETRIES),
        )
    )


def _warn_if_repair_unnecessary(finalization_pass_dir: str) -> None:
    """Warn if repair hints indicate no actionable issues, but don't block the run."""
    chain_dir = Path(finalization_pass_dir).parent.parent
    repair_hints_path = chain_dir / "repair_hints.json"
    if not repair_hints_path.exists():
        return
    try:
        hints = json.loads(repair_hints_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if hints.get("ready_for_llm_repair") is not True:
        print(
            "warning: repair_hints.ready_for_llm_repair is not true; "
            "repair pass may have nothing to fix",
            file=sys.stderr,
        )


def _warn_if_timeline_repair_unnecessary(timeline_pass_dir: str) -> None:
    """Warn if timeline validation shows no errors, but don't block the run."""
    validation_path = Path(timeline_pass_dir) / "validation_report.json"
    if not validation_path.exists():
        return
    try:
        report = json.loads(validation_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if report.get("error_count", 0) == 0:
        print(
            "warning: timeline validation report shows 0 errors; "
            "repair pass may have nothing to fix",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
