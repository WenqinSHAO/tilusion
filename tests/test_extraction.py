from __future__ import annotations

from pathlib import Path

from tilusion.extraction import (
    ExtractionContext,
    MockExtractionBackend,
    build_cache_key,
    build_local_bundle_prompt,
    run_local_bundle_extraction,
)
from tilusion.book_reader import build_book_index, extract_unit_text


def test_local_bundle_prompt_has_cache_relevant_structure(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    index = build_book_index(book)
    unit = index.units[1]
    text = extract_unit_text(book, unit)

    envelope = build_local_bundle_prompt(unit, text, context=run_empty_context())
    key = build_cache_key(envelope, MockExtractionBackend().model_identity)

    assert envelope.task == "local_bundle_extraction"
    assert envelope.unit["id"] == "unit-0001"
    assert envelope.unit["source_range"]["kind"] == "txt-span"
    assert len(key) == 64


def test_local_bundle_extraction_uses_mock_backend_and_cache(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    result = run_local_bundle_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
    )
    cached = run_local_bundle_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
    )

    assert result.data["unit_id"] == "unit-0001"
    assert result.data["evidence_spans"][0]["quote"] == "Chapter 1"
    assert cached.to_dict() == result.to_dict()
    assert list(cache_dir.glob("*.json"))


def run_empty_context():
    return ExtractionContext(frontier="unit-0001")
