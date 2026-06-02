from __future__ import annotations

import json
from pathlib import Path

from tilusion.book_context import stable_book_id
from tilusion.cache_layout import (
    book_root,
    compute_cross_unit_run_hash,
    compute_unit_run_hash,
    model_config_for_cache,
    prepend_to_runs_catalog,
    read_run_manifest,
    read_runs_catalog,
    runs_catalog_path,
    source_index_path,
    unit_run_dir,
    write_run_manifest,
)


def test_book_root_uses_clean_book_scoped_root(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\nText.\n", encoding="utf-8")

    root = book_root(tmp_path / "cache", book)

    assert root == tmp_path / "cache" / stable_book_id(book)
    assert "books" not in root.parts
    assert source_index_path(tmp_path / "cache", book) == root / "source_index.json"
    assert unit_run_dir(tmp_path / "cache", book, "unit-0001", "run-abc") == root / "unit-0001" / "run-abc"


def test_unit_run_hash_includes_context_and_model_config() -> None:
    base = {
        "source_index_id": "source-index-a",
        "unit_id": "unit-0001",
        "scope": "book",
        "model_identity": "deepseek:test",
        "model_config": {"max_tokens": 100},
        "context_identity": {"registry_commit": "abc"},
        "prompt_versions": {"overview": "v1"},
    }

    first = compute_unit_run_hash(**base)
    second = compute_unit_run_hash(**{**base, "context_identity": {"registry_commit": "def"}})
    third = compute_unit_run_hash(**{**base, "model_config": {"max_tokens": 200}})

    assert first.startswith("run-")
    assert first != second
    assert first != third


def test_cross_unit_hash_includes_registry_state() -> None:
    base = {
        "source_index_id": "source-index-a",
        "triggering_run_hash": "run-a",
        "triggering_unit_id": "unit-0001",
        "registry_state_hash": "abc",
        "model_identity": "deepseek:test",
        "model_config": {},
        "prompt_versions": {"concept_resolution": "v1"},
    }

    first = compute_cross_unit_run_hash(**base)
    second = compute_cross_unit_run_hash(**{**base, "registry_state_hash": "def"})

    assert first.startswith("run-")
    assert first != second


def test_runs_catalog_prepends_entries_atomically(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\nText.\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    prepend_to_runs_catalog(cache_root, book, {"run_hash": "run-1"})
    prepend_to_runs_catalog(cache_root, book, {"run_hash": "run-2"})

    catalog = read_runs_catalog(cache_root, book)
    assert [entry["run_hash"] for entry in catalog["runs"]] == ["run-2", "run-1"]
    assert runs_catalog_path(cache_root, book).exists()
    assert not runs_catalog_path(cache_root, book).with_name("runs.json.tmp").exists()


def test_run_manifest_round_trips(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\nText.\n", encoding="utf-8")
    run_dir = unit_run_dir(tmp_path / "cache", book, "unit-0001", "run-abc")

    path = write_run_manifest(run_dir, {"run_hash": "run-abc", "passes": {}})

    assert path == run_dir / "run.json"
    assert read_run_manifest(run_dir)["run_hash"] == "run-abc"


def test_model_config_for_cache_extracts_known_json_scalars() -> None:
    class Backend:
        model = "m"
        thinking = False
        reasoning_effort = "high"
        max_tokens = 123
        timeout = 5.0
        max_retries = 2
        client = object()

    assert model_config_for_cache(Backend()) == {
        "model": "m",
        "thinking": False,
        "reasoning_effort": "high",
        "max_tokens": 123,
        "timeout": 5.0,
        "max_retries": 2,
    }
