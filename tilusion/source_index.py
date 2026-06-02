from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .backend import sha256_json
from .book_context import book_cache_dir, stable_book_id
from .book_reader import BookIndex, StructureUnit, build_book_index, extract_unit_text
from .reading_schema import SourceBlock
from .source_blocks import SOURCE_BLOCK_SPLITTER_VERSION, split_source_blocks

BOOK_SOURCE_INDEX_SCHEMA_VERSION = "book-source-index-v0.1"
BOOK_READER_VERSION = "book-reader-v0.1"


def build_book_source_index(book_path: str | Path) -> dict[str, Any]:
    """Build the deterministic book-scoped source block index for *book_path*.

    The returned artifact uses book-scoped block IDs (``block-000001``) and
    records both stitched-book offsets and unit-local offsets. Extraction still
    uses the legacy per-segment splitter for now; this function is the stable
    source-address layer that later pipeline phases will consume.
    """
    path = Path(book_path).resolve()
    index = build_book_index(path)
    book_id = stable_book_id(path)
    source_digest = _file_sha256(path)
    content_units = _content_units(index)

    units: dict[str, dict[str, Any]] = {}
    blocks: dict[str, dict[str, Any]] = {}
    next_block_index = 1
    book_cursor = 0

    for unit in content_units:
        unit_text = extract_unit_text(path, unit)
        unit_start = book_cursor
        raw_blocks, metrics = split_source_blocks(
            unit_text,
            segment_id="book-source",
            unit_id=unit.id,
            unit_text=unit_text,
            unit_offset=0,
        )
        block_refs: list[str] = []
        for raw in raw_blocks:
            block_id = f"block-{next_block_index:06d}"
            block_refs.append(block_id)
            block = raw.to_dict()
            unit_local_start = int(block["start"])
            unit_local_end = int(block["end"])
            block.update({
                "block_id": block_id,
                "segment_id": "",
                "block_index": next_block_index,
                "start": unit_local_start,
                "end": unit_local_end,
                "unit_start": unit_local_start,
                "unit_end": unit_local_end,
                "book_start": unit_start + unit_local_start,
                "book_end": unit_start + unit_local_end,
                "legacy_block_id": raw.block_id,
                "source_index_scope": "book",
                "provenance": {
                    **dict(block.get("provenance", {})),
                    "source_index_schema_version": BOOK_SOURCE_INDEX_SCHEMA_VERSION,
                    "source_index_scope": "book",
                },
            })
            blocks[block_id] = block
            next_block_index += 1

        units[unit.id] = {
            "unit_id": unit.id,
            "label": unit.label,
            "kind": unit.kind,
            "order": unit.order,
            "level": unit.level,
            "parent_id": unit.parent_id,
            "title_path": list(unit.title_path),
            "content_kind": unit.content_kind,
            "source_kind": unit.source_kind,
            "book_start": unit_start,
            "book_end": unit_start + len(unit_text),
            "char_count": len(unit_text),
            "block_refs": block_refs,
            "source_block_splitter": metrics.to_dict(),
        }
        book_cursor += len(unit_text)

    source_index_id = _source_index_id(
        book_id=book_id,
        source_digest=source_digest,
        source_format=index.source_format,
    )
    for block in blocks.values():
        block["source_index_id"] = source_index_id
        block["provenance"]["source_index_id"] = source_index_id

    return {
        "schema_version": BOOK_SOURCE_INDEX_SCHEMA_VERSION,
        "source_index_id": source_index_id,
        "book_id": book_id,
        "source_path": str(path),
        "source_format": index.source_format,
        "source_digest": source_digest,
        "book_reader_version": BOOK_READER_VERSION,
        "splitter_version": SOURCE_BLOCK_SPLITTER_VERSION,
        "units": units,
        "blocks": blocks,
        "metrics": {
            "unit_count": len(units),
            "block_count": len(blocks),
            "total_chars": book_cursor,
            "avg_block_size": round(
                sum(len(block.get("text", "")) for block in blocks.values()) / len(blocks), 2
            ) if blocks else 0.0,
        },
    }


def source_index_cache_path(book_path: str | Path, cache_root: str | Path = ".tilusion_cache") -> Path:
    book_id = stable_book_id(Path(book_path).resolve())
    return book_cache_dir(cache_root, book_id) / "source_index.json"


def save_book_source_index(
    index_data: dict[str, Any],
    book_path: str | Path,
    *,
    cache_root: str | Path = ".tilusion_cache",
) -> Path:
    path = source_index_cache_path(book_path, cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_book_source_index(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != BOOK_SOURCE_INDEX_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported source index schema: {data.get('schema_version')!r}"
        )
    return data


def block_by_id(index_data: dict[str, Any], block_id: str) -> dict[str, Any] | None:
    blocks = index_data.get("blocks", {})
    if not isinstance(blocks, dict):
        return None
    block = blocks.get(block_id)
    return block if isinstance(block, dict) else None


def blocks_for_unit(index_data: dict[str, Any], unit_id: str) -> list[dict[str, Any]]:
    unit = index_data.get("units", {}).get(unit_id, {})
    refs = unit.get("block_refs", []) if isinstance(unit, dict) else []
    return [block for ref in refs if (block := block_by_id(index_data, ref)) is not None]


def blocks_for_unit_range(
    index_data: dict[str, Any],
    unit_id: str,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    """Return book-source-index blocks in *unit_id* overlapping ``[start, end)``.

    ``start`` and ``end`` are unit-local offsets. The returned blocks are full
    source-index blocks, not clipped fragments, so callers can expand an LLM
    segment to stable block boundaries.
    """
    result: list[dict[str, Any]] = []
    for block in blocks_for_unit(index_data, unit_id):
        block_start = int(block.get("unit_start", block.get("start", 0)))
        block_end = int(block.get("unit_end", block.get("end", 0)))
        if block_end > start and block_start < end:
            result.append(block)
    return sorted(result, key=lambda b: (int(b.get("unit_start", b.get("start", 0))), b.get("block_id", "")))


def source_index_block_to_source_block(block: dict[str, Any]) -> SourceBlock:
    """Convert one source-index block to the current package SourceBlock shape."""
    unit_start = int(block.get("unit_start", block.get("start", 0)))
    unit_end = int(block.get("unit_end", block.get("end", unit_start)))
    provenance = dict(block.get("provenance", {}))
    for key in ("source_index_id", "source_index_scope", "book_start", "book_end", "legacy_block_id"):
        if key in block:
            provenance[key] = block[key]
    return SourceBlock(
        block_id=block.get("block_id", ""),
        unit_id=block.get("unit_id", ""),
        segment_id=block.get("segment_id", ""),
        block_index=int(block.get("block_index", 0)),
        block_type=block.get("block_type", "paragraph"),
        start=unit_start,
        end=unit_end,
        text=block.get("text", ""),
        text_hash=block.get("text_hash", ""),
        provenance=provenance,
    )


def _content_units(index: BookIndex) -> list[StructureUnit]:
    """Return non-overlapping content units in structural order.

    Prefer leaf units to avoid duplicating text from parent units whose source
    spans include their children. If the book has no leaf children, fall back to
    every non-root content unit.
    """
    candidates = [u for u in index.units if u.id != index.root_id]
    leaves = [u for u in candidates if not u.children]
    selected = leaves or candidates
    return sorted(selected, key=lambda u: (u.order, u.id))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_index_id(*, book_id: str, source_digest: str, source_format: str) -> str:
    digest = sha256_json({
        "schema_version": BOOK_SOURCE_INDEX_SCHEMA_VERSION,
        "book_id": book_id,
        "source_digest": source_digest,
        "source_format": source_format,
        "book_reader_version": BOOK_READER_VERSION,
        "splitter_version": SOURCE_BLOCK_SPLITTER_VERSION,
    })
    return f"source-index-{digest[:16]}"
