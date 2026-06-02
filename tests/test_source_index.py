from __future__ import annotations

from pathlib import Path

from tilusion.source_index import (
    BOOK_SOURCE_INDEX_SCHEMA_VERSION,
    block_by_id,
    blocks_for_unit,
    build_book_source_index,
    load_book_source_index,
    save_book_source_index,
    source_index_cache_path,
)


def _write_book(tmp_path: Path) -> Path:
    book = tmp_path / "book.txt"
    book.write_text(
        "Chapter 1\n\nAlpha begins. Beta continues.\n\n"
        "Chapter 2\n\nGamma answers. Delta closes.\n",
        encoding="utf-8",
    )
    return book


def test_book_source_index_uses_book_scoped_block_ids(tmp_path: Path) -> None:
    book = _write_book(tmp_path)

    index = build_book_source_index(book)

    assert index["schema_version"] == BOOK_SOURCE_INDEX_SCHEMA_VERSION
    assert index["metrics"]["unit_count"] == 2
    assert index["metrics"]["block_count"] >= 2
    block_ids = list(index["blocks"])
    assert block_ids[0] == "block-000001"
    assert all(block_id.startswith("block-") for block_id in block_ids)
    assert all("segment" not in block_id for block_id in block_ids)

    first = index["blocks"]["block-000001"]
    assert first["source_index_scope"] == "book"
    assert first["segment_id"] == ""
    assert first["unit_id"] == "unit-0001"
    assert first["book_start"] == first["unit_start"]
    assert first["book_end"] == first["unit_end"]
    assert first["legacy_block_id"].startswith("book-source-block-")


def test_book_source_index_is_deterministic(tmp_path: Path) -> None:
    book = _write_book(tmp_path)

    first = build_book_source_index(book)
    second = build_book_source_index(book)

    assert first["source_index_id"] == second["source_index_id"]
    assert list(first["blocks"]) == list(second["blocks"])
    assert first["blocks"] == second["blocks"]


def test_blocks_for_unit_uses_unit_block_refs(tmp_path: Path) -> None:
    book = _write_book(tmp_path)
    index = build_book_source_index(book)

    unit_blocks = blocks_for_unit(index, "unit-0002")

    assert unit_blocks
    assert all(block["unit_id"] == "unit-0002" for block in unit_blocks)
    assert block_by_id(index, unit_blocks[0]["block_id"]) == unit_blocks[0]


def test_save_and_load_book_source_index(tmp_path: Path) -> None:
    book = _write_book(tmp_path)
    index = build_book_source_index(book)

    path = save_book_source_index(index, book, cache_root=tmp_path / "cache")
    loaded = load_book_source_index(path)

    assert path == source_index_cache_path(book, tmp_path / "cache")
    assert loaded["source_index_id"] == index["source_index_id"]
    assert loaded["blocks"] == index["blocks"]


def test_source_index_cli_writes_artifact(tmp_path: Path, capsys) -> None:
    from tilusion.cli import main

    book = _write_book(tmp_path)
    cache_dir = tmp_path / "cache"

    code = main(["source-index", str(book), "--cache-dir", str(cache_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "source_index_id:" in captured.out
    path = source_index_cache_path(book, cache_dir)
    assert path.exists()
    loaded = load_book_source_index(path)
    assert loaded["metrics"]["block_count"] >= 2


def test_source_index_cli_can_print_json(tmp_path: Path, capsys) -> None:
    from tilusion.cli import main
    import json

    book = _write_book(tmp_path)

    code = main(["source-index", str(book), "--cache-dir", str(tmp_path / "cache"), "--format", "json"])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == BOOK_SOURCE_INDEX_SCHEMA_VERSION
    assert payload["blocks"]["block-000001"]["source_index_scope"] == "book"
