from __future__ import annotations

import json
from pathlib import Path

from tilusion.book_context import (
    BOOK_CONTEXT_SCHEMA_VERSION,
    build_empty_book_state_snapshot,
    build_passive_context_pack,
    stable_book_id,
    write_passive_context_artifacts,
)
from tilusion.extraction import sha256_json


def test_stable_book_id_is_deterministic_for_local_path(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\n", encoding="utf-8")

    assert stable_book_id(book) == stable_book_id(book)
    assert stable_book_id(book).startswith("book-")
    assert stable_book_id(book) != stable_book_id(tmp_path / "other.txt")


def test_empty_book_state_snapshot_is_hash_addressed(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    snapshot = build_empty_book_state_snapshot(book)
    snapshot_hash = snapshot["snapshot_hash"]
    base = {k: v for k, v in snapshot.items() if k not in {"snapshot_id", "snapshot_hash"}}

    assert snapshot["schema_version"] == BOOK_CONTEXT_SCHEMA_VERSION
    assert snapshot["snapshot_id"] == f"snapshot-{snapshot_hash[:16]}"
    assert snapshot_hash == sha256_json(base)
    assert snapshot["registry"]["entities"] == []
    assert snapshot["indices"]["surfaces"] == {}


def test_passive_context_pack_is_not_prompt_injected(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    pack = build_passive_context_pack(book, "unit-0002", source_length={"chars": 12})
    pack_hash = pack["context_pack_hash"]
    base = {k: v for k, v in pack.items() if k not in {"context_pack_id", "context_pack_hash"}}

    assert pack["context_pack_id"] == f"context-pack-{pack_hash[:16]}"
    assert pack_hash == sha256_json(base)
    assert pack["prompt_injection"]["enabled"] is False
    assert pack["context"]["entities"] == []
    assert pack["selection_summary"]["known_surface_hits"] == 0


def test_write_passive_context_artifacts(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\n", encoding="utf-8")
    paths = write_passive_context_artifacts(
        book_path=book,
        unit_id="unit-0002",
        cache_root=tmp_path / "cache",
        source_length={"chars": 10},
    )

    for path in paths.values():
        assert Path(path).exists()

    context_pack = json.loads(Path(paths["context_pack"]).read_text(encoding="utf-8"))
    report = json.loads(Path(paths["context_selection_report"]).read_text(encoding="utf-8"))
    latest = json.loads(Path(paths["book_state_latest"]).read_text(encoding="utf-8"))

    assert context_pack["target_unit_id"] == "unit-0002"
    assert context_pack["source_length"] == {"chars": 10}
    assert report["context_pack_hash"] == context_pack["context_pack_hash"]
    assert latest["snapshot_hash"] == context_pack["book_state_snapshot"]["snapshot_hash"]
