from __future__ import annotations

import json
import re
from pathlib import Path

from tools.generate_reading_view import generate_book
from tilusion.source_index import build_book_source_index, save_book_source_index


def _template_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "tools" / "reading_view_template.html")


def _script_payload(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="readerData">\n(.*?)\n</script>',
        html,
        flags=re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_generate_book_view_from_registry_and_source_index(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\n\nAlpha begins.\n", encoding="utf-8")
    source_index = build_book_source_index(book)
    source_index_path = save_book_source_index(source_index, book, cache_root=tmp_path / "cache")
    first_block_id = next(iter(source_index["blocks"]))

    registry = {
        "next_ids": {"concept": 2, "item": 2, "group": 2},
        "concepts": {
            "concept-0001": {
                "concept_id": "concept-0001",
                "surface": "Alpha",
                "concept_type": "term",
                "summary": "Alpha concept",
                "observed_surfaces": ["Alpha"],
                "source_block_refs": [first_block_id],
            }
        },
        "items": {
            "item-0001": {
                "item_id": "item-0001",
                "item_type": "observation",
                "summary": "Alpha begins",
                "source_block_refs": [first_block_id],
                "concept_refs": ["concept-0001"],
            }
        },
        "groups": {
            "group-0001": {
                "group_id": "group-0001",
                "group_type": "theme_set",
                "summary": "Alpha group",
                "item_refs": ["item-0001"],
                "concept_refs": ["concept-0001"],
                "graph": {},
            }
        },
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    output = tmp_path / "book_view.html"

    generate_book(_template_path(), str(registry_path), str(source_index_path), str(output))

    rendered = output.read_text(encoding="utf-8")
    payload = _script_payload(rendered)
    assert payload["scope"] == "book"
    assert payload["source_index"]["source_index_id"] == source_index["source_index_id"]
    assert payload["diagnostics"]["missing_source_block_ref_count"] == 0
    assert 'data-block="block-000001"' in rendered
    assert 'data-unit="unit-0001"' in rendered


def test_generate_book_view_reports_missing_legacy_refs(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\n\nAlpha begins.\n", encoding="utf-8")
    source_index = build_book_source_index(book)
    source_index_path = save_book_source_index(source_index, book, cache_root=tmp_path / "cache")
    registry = {
        "concepts": {
            "concept-0001": {
                "concept_id": "concept-0001",
                "surface": "Alpha",
                "concept_type": "term",
                "source_block_refs": ["overview-segment-0001-block-0000"],
            }
        },
        "items": {},
        "groups": {},
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    output = tmp_path / "book_view.html"

    generate_book(_template_path(), str(registry_path), str(source_index_path), str(output))

    rendered = output.read_text(encoding="utf-8")
    payload = _script_payload(rendered)
    assert payload["diagnostics"]["missing_source_block_ref_count"] == 1
    assert "unresolved source block reference" in rendered
    assert "overview-segment-0001-block-0000" in rendered
