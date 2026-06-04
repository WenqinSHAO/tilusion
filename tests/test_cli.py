from __future__ import annotations

import json
from pathlib import Path

from tilusion.cli import build_parser, main, split_unit_source_blocks


def test_parser_accepts_run_reading_defaults() -> None:
    args = build_parser().parse_args(["run-reading", "book.txt", "unit-0001"])

    assert args.command == "run-reading"
    assert args.backend == "mock"
    assert args.cache_dir == ".tilusion_cache"
    assert args.no_cache is False
    assert args.source_language == "auto"
    assert args.reader_language == "zh-Hans"
    assert args.normalized_language == "normalized"


def test_parser_accepts_split_blocks_options() -> None:
    args = build_parser().parse_args(
        ["split-blocks", "book.txt", "unit-0001", "--segment-id", "seg-a", "--format", "json"]
    )

    assert args.command == "split-blocks"
    assert args.segment_id == "seg-a"
    assert args.format == "json"


def test_split_unit_source_blocks_returns_metrics(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlpha sentence. Beta sentence.\n", encoding="utf-8")

    payload = split_unit_source_blocks(book, "unit-0001", segment_id="seg-test")

    assert payload["unit_id"] == "unit-0001"
    assert payload["segment_id"] == "seg-test"
    assert payload["metrics"]["block_count"] >= 1
    assert payload["metrics"]["coverage_pct"] == 100.0
    assert payload["source_blocks"][0]["block_id"].startswith("seg-test-block-")


def test_split_blocks_cli_json_output(tmp_path: Path, capsys) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlpha sentence.\n", encoding="utf-8")

    code = main(["split-blocks", str(book), "unit-0001", "--format", "json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unit_id"] == "unit-0001"
    assert payload["metrics"]["coverage_pct"] == 100.0


def test_split_blocks_cli_text_output(tmp_path: Path, capsys) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlpha sentence.\n", encoding="utf-8")

    code = main(["split-blocks", str(book), "unit-0001"])

    assert code == 0
    out = capsys.readouterr().out
    assert "unit_id: unit-0001" in out
    assert "blocks:" in out


def test_run_reading_cli_mock_writes_package(tmp_path: Path, capsys) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlpha sentence. Beta sentence.\n", encoding="utf-8")
    cache_dir = tmp_path / "reading_cache"

    code = main([
        "run-reading",
        str(book),
        "unit-0001",
        "--cache-dir",
        str(cache_dir),
        "--no-cache",
        "--json",
    ])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["unit_id"] == "unit-0001"
    package_path = Path(payload["unit_package_path"])
    assert package_path.exists()
    package = json.loads(package_path.read_text(encoding="utf-8"))
    assert package["schema_version"] == "reading-unit-v0.3"
    assert "metrics" in package
    assert package["metrics"]["counts"]["overview"]["resolved_segment_count"] >= 1
