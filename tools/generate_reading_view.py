#!/usr/bin/env python3
"""Generate a self-contained reading-view HTML from a v0.3 unit package.

Usage:
    python tools/generate_reading_view.py \\
        .tilusion_cache/reading_passes/units/unit-0002/unit_package.json \\
        -o reading_view.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Data loading ──────────────────────────────────────────────────────────────

def load_package(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_registry(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_source_index(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_source_text(book_path: str, unit_id: str | None = None) -> str:
    path = Path(book_path)
    suffix = path.suffix.lower()
    if suffix == ".epub":
        from tilusion.book_reader import EpubBookReader, build_book_index
        from tilusion.book_reader import extract_unit_text
        if unit_id is None:
            raise ValueError("unit_id is required for epub source text extraction")
        index = build_book_index(path)
        unit = index.unit_map().get(unit_id)
        if unit is None:
            raise ValueError(f"Unit {unit_id} not found in {path}")
        return extract_unit_text(path, unit)
    if suffix == ".txt":
        with open(path, encoding="utf-8") as f:
            return f.read()
    raise ValueError(f"Unsupported book format: {suffix}")



def _text_attr(value: Any, fallback: str = "") -> str:
    """Return a safe display string for optional JSON text fields."""
    if value is None:
        return fallback
    return str(value)


# ── Source text annotation ────────────────────────────────────────────────────

def _find_all(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Return all (start, end) positions of needle in haystack (non-overlapping)."""
    results = []
    pos = 0
    while True:
        idx = haystack.find(needle, pos)
        if idx == -1:
            break
        results.append((idx, idx + len(needle)))
        pos = idx + 1
    return results


def _build_concept_surfaces(concepts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Build concept_id -> surfaces mapping for annotation."""
    result: dict[str, list[str]] = {}
    for c in concepts:
        surfaces = list(c.get("observed_surfaces", []))
        if not surfaces and c.get("surface"):
            surfaces = [c["surface"]]
        # Deduplicate and sort by length descending (longest match first)
        seen = set()
        unique = []
        for s in surfaces:
            if s and s not in seen and len(s) >= 2:
                seen.add(s)
                unique.append(s)
        unique.sort(key=len, reverse=True)
        if unique:
            result[c["concept_id"]] = unique
    return result


def _annotate_text(
    text: str,
    block_id: str,
    ref_concepts: list[dict[str, Any]],
    concept_surfaces: dict[str, list[str]],
) -> str:
    """Build annotated HTML for a single block of text."""
    annotations: list[dict[str, Any]] = []
    for c in ref_concepts:
        cid = c["concept_id"]
        surfaces = concept_surfaces.get(cid, [])
        for surf in surfaces:
            for s, e in _find_all(text, surf):
                annotations.append({
                    "start": s, "end": e,
                    "concept_id": cid,
                    "concept_type": _text_attr(c.get("concept_type")),
                    "canonical_name": _text_attr(c.get("canonical_name"), surf),
                })

    # Remove overlapping annotations (keep longer surface match)
    annotations.sort(key=lambda a: (a["start"], -(a["end"] - a["start"])))
    filtered: list[dict[str, Any]] = []
    for ann in annotations:
        overlaps = any(
            ann["start"] < f["end"] and ann["end"] > f["start"]
            for f in filtered
        )
        if not overlaps:
            filtered.append(ann)
    filtered.sort(key=lambda a: a["start"])

    parts = []
    cursor = 0
    for ann in filtered:
        if ann["start"] > cursor:
            parts.append(html.escape(text[cursor:ann["start"]]))
        surface_text = text[ann["start"]:ann["end"]]
        ctype = _text_attr(ann.get("concept_type"))
        concept_id = _text_attr(ann.get("concept_id"))
        canonical_name = _text_attr(ann.get("canonical_name"), surface_text)
        parts.append(
            f'<mark class="concept-mark {html.escape(ctype)}" '
            f'data-concept="{html.escape(concept_id)}" '
            f'data-name="{html.escape(canonical_name)}">'
            f'{html.escape(surface_text)}</mark>'
        )
        cursor = ann["end"]
    if cursor < len(text):
        parts.append(html.escape(text[cursor:]))

    return "".join(parts)


def _resolve_source_offset(
    sorted_blocks: list[dict[str, Any]],
    source_text: str,
) -> int:
    """Return the book-level offset that aligns block positions to source_text.

    Block start/end offsets are unit-relative (0 = unit start).  The source
    file may include front-matter before the unit text, so we find the first
    block whose text is unambiguous in the source and compute the delta.
    """
    if not sorted_blocks or not source_text:
        return 0

    # Try the first few blocks with substantial text, preferring longer unique text
    for block in sorted_blocks[:10]:
        text = block.get("text", "")
        if len(text) < 20:
            continue
        pos = source_text.find(text)
        if pos != -1:
            return pos - block["start"]

    return 0


def build_source_html(
    source_text: str,
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
) -> str:
    """Build annotated source HTML.

    Blocks are rendered in source position order (sorted by start offset).
    Gaps between blocks are rendered as unannotated raw text so no source
    content is silently dropped.

    Block offsets are unit-relative; the function auto-detects the book-level
    offset so rendering works against both unit-scoped and full-book source text.
    """
    # Index: block_id -> concepts that reference it
    block_concepts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in concepts:
        for ref in c.get("source_block_refs", []):
            block_concepts[ref].append(c)

    # Index: block_id -> atomic items that reference it
    block_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in atomic_items:
        for ref in item.get("source_block_refs", []):
            block_items[ref].append(item)

    concept_surfaces = _build_concept_surfaces(concepts)

    def _block_sort_key(block: dict[str, Any]) -> tuple[int, int, str]:
        return (int(block.get("book_start", block.get("start", 0))), int(block.get("start", 0)), block.get("block_id", ""))

    sorted_blocks = sorted(source_blocks, key=_block_sort_key)

    has_book_offsets = any("book_start" in block for block in sorted_blocks)
    offset = 0 if has_book_offsets else _resolve_source_offset(sorted_blocks, source_text)

    def _resolve_pos(block: dict[str, Any], field: str) -> int:
        if has_book_offsets:
            key = "book_start" if field == "start" else "book_end"
            return int(block.get(key, block.get(field, 0)))
        return int(block.get(field, 0)) + offset

    parts: list[str] = []
    cursor = _resolve_pos(sorted_blocks[0], "start") if sorted_blocks else 0

    current_unit = None
    for block in sorted_blocks:
        block_id = block["block_id"]
        unit_start = int(block.get("unit_start", block.get("start", 0)))
        unit_end = int(block.get("unit_end", block.get("end", 0)))
        start = _resolve_pos(block, "start")
        end = _resolve_pos(block, "end")
        block_text = source_text[start:end]

        unit_id = block.get("unit_id")
        if has_book_offsets and unit_id and unit_id != current_unit:
            current_unit = unit_id
            parts.append(f'<h2 class="unit-break" data-unit="{html.escape(unit_id)}">{html.escape(unit_id)}</h2>')

        # Verify round-trip against block's stored text
        if block_text != block["text"]:
            block_text = block["text"]

        # Render any uncovered text between cursor and this block
        if start > cursor:
            gap_text = source_text[cursor:start]
            if gap_text.strip():
                gap_start = cursor if has_book_offsets else cursor - offset
                gap_end = start if has_book_offsets else start - offset
                parts.append(
                    f'<p class="src-block" data-start="{gap_start}" data-end="{gap_end}">'
                    f'{html.escape(gap_text)}</p>'
                )

        # Build item refs data
        item_ids = sorted({it["item_id"] for it in block_items.get(block_id, [])})
        item_data = ""
        if item_ids:
            item_data = f' data-items="{html.escape(",".join(item_ids))}"'

        ref_concepts = block_concepts.get(block_id, [])
        inner = _annotate_text(block_text, block_id, ref_concepts, concept_surfaces)

        pos_attrs = f' data-start="{unit_start}" data-end="{unit_end}" data-book-start="{start}" data-book-end="{end}"'
        unit_attr = f' data-unit="{html.escape(str(block.get("unit_id", "")))}"' if block.get("unit_id") else ""
        parts.append(f'<p class="src-block" data-block="{html.escape(block_id)}"{unit_attr}{item_data}{pos_attrs}>{inner}</p>')

        cursor = max(cursor, end)

    # Render any trailing text after the last block
    max_end = _resolve_pos(sorted_blocks[-1], "end") if sorted_blocks else 0
    if cursor < max_end:
        tail_text = source_text[cursor:max_end]
        if tail_text.strip():
            tail_start = cursor if has_book_offsets else cursor - offset
            tail_end = max_end if has_book_offsets else max_end - offset
            parts.append(
                f'<p class="src-block" data-start="{tail_start}" data-end="{tail_end}">'
                f'{html.escape(tail_text)}</p>'
            )

    return "\n".join(parts)


# ── Data embedding ────────────────────────────────────────────────────────────

def slim_concept(c: dict[str, Any]) -> dict[str, Any]:
    return {
        k: c[k] for k in (
            "concept_id", "surface", "concept_type", "canonical_name",
            "summary", "aliases", "observed_surfaces", "source_block_refs",
            "facets", "uncertainty",
        ) if k in c
    }


def slim_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        k: item[k] for k in (
            "item_id", "item_type", "summary", "source_block_refs",
            "concept_refs", "temporal_attributes", "attributes",
            "uncertainty",
        ) if k in item
    }


def _dict_values(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [v for v in data.values() if isinstance(v, dict)]
    if isinstance(data, list):
        return [v for v in data if isinstance(v, dict)]
    return []


def _registry_records(registry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        _dict_values(registry.get("concepts", {})),
        _dict_values(registry.get("items", {})),
        _dict_values(registry.get("groups", {})),
    )


def _source_index_blocks(source_index: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        _dict_values(source_index.get("blocks", {})),
        key=lambda b: (int(b.get("book_start", b.get("start", 0))), b.get("block_id", "")),
    )


def _stitched_source_text(blocks: list[dict[str, Any]]) -> str:
    if not blocks:
        return ""
    max_end = max(int(block.get("book_end", block.get("end", 0))) for block in blocks)
    chars = [" "] * max_end
    for block in blocks:
        start = int(block.get("book_start", block.get("start", 0)))
        text = str(block.get("text", ""))
        end = start + len(text)
        if end > len(chars):
            chars.extend(" " for _ in range(end - len(chars)))
        chars[start:end] = list(text)
    return "".join(chars)


def _evidence_diagnostics(
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    logical_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    known = {block.get("block_id") for block in source_blocks}
    missing: dict[str, list[str]] = defaultdict(list)

    def check(owner: str, refs: Any) -> None:
        for ref in refs or []:
            if ref and ref not in known:
                missing[str(ref)].append(owner)

    for concept in concepts:
        check(f"concept:{concept.get('concept_id', '')}", concept.get("source_block_refs"))
    for item in atomic_items:
        check(f"item:{item.get('item_id', '')}", item.get("source_block_refs"))
    for group in logical_groups:
        for edge in (group.get("graph") or {}).get("edges", []):
            check(f"group:{group.get('group_id', '')}:edge", edge.get("source_block_refs"))

    return {
        "missing_source_block_ref_count": len(missing),
        "missing_source_block_refs": [
            {"block_id": block_id, "owners": owners[:8]}
            for block_id, owners in sorted(missing.items())[:200]
        ],
    }


def _diagnostic_html(diagnostics: dict[str, Any]) -> str:
    count = diagnostics.get("missing_source_block_ref_count", 0)
    if not count:
        return ""
    examples = diagnostics.get("missing_source_block_refs", [])[:8]
    lines = "".join(
        f'<li><code>{html.escape(str(entry.get("block_id", "")))}</code> '
        f'{html.escape(", ".join(entry.get("owners", [])))}</li>'
        for entry in examples
    )
    return (
        '<div class="source-diagnostic">'
        f'<strong>{count} unresolved source block reference{"s" if count != 1 else ""}</strong>'
        '<ul>' + lines + '</ul>'
        '</div>'
    )


def build_data_script(
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    logical_groups: list[dict[str, Any]],
    unit_id: str,
    source_info: dict[str, Any],
    metrics: dict[str, Any],
    *,
    scope: str = "unit",
    source_index: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Return a <script> block embedding structured data as JSON."""
    payload = {
        "scope": scope,
        "unit_id": unit_id,
        "source": source_info,
        "source_index": source_index or {},
        "source_blocks": source_blocks,
        "concepts": [slim_concept(c) for c in concepts],
        "atomic_items": [slim_item(it) for it in atomic_items],
        "logical_groups": logical_groups,
        "metrics": metrics,
        "diagnostics": diagnostics or {},
    }
    return (
        '<script type="application/json" id="readerData">\n'
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n</script>"
    )


# ── Assembly ──────────────────────────────────────────────────────────────────

def render(template: str, replacements: dict[str, str]) -> str:
    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def _render_view(
    *,
    template: str,
    output_path: str,
    unit_id: str,
    unit_label: str,
    book_title: str,
    source_info: dict[str, Any],
    source_html: str,
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    logical_groups: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    metrics: dict[str, Any],
    scope: str,
    source_index: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> None:
    data_script = build_data_script(
        source_blocks, concepts, atomic_items, logical_groups,
        unit_id, source_info, metrics,
        scope=scope, source_index=source_index, diagnostics=diagnostics,
    )

    group_types = defaultdict(int)
    groups_with_graph = 0
    groups_without_graph = 0
    for g in logical_groups:
        group_types[g.get("group_type", "other")] += 1
        if g.get("graph"):
            groups_with_graph += 1
        else:
            groups_without_graph += 1

    replacements = {
        "{{UNIT_ID}}": html.escape(unit_id),
        "{{UNIT_LABEL}}": html.escape(unit_label),
        "{{BOOK_TITLE}}": html.escape(book_title),
        "{{CONCEPT_COUNT}}": str(len(concepts)),
        "{{ITEM_COUNT}}": str(len(atomic_items)),
        "{{GROUP_COUNT}}": str(len(logical_groups)),
        "{{UNRESOLVED_COUNT}}": str(len(unresolved)),
        "{{BLOCK_COUNT}}": str(len(source_blocks)),
        "{{GROUPS_WITH_GRAPH}}": str(groups_with_graph),
        "{{GROUPS_WITHOUT_GRAPH}}": str(groups_without_graph),
        "<!-- SOURCE_HTML -->": source_html,
        "<!-- DATA_SCRIPT -->": data_script,
    }

    rendered = render(template, replacements)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    print(f"Wrote {output_path}")
    print(f"  Scope: {scope}")
    print(f"  Source blocks: {len(source_blocks)}")
    print(f"  Concepts: {len(concepts)}")
    print(f"  Atomic items: {len(atomic_items)}")
    print(f"  Logical groups: {len(logical_groups)} ({groups_with_graph} with graph, {groups_without_graph} without)")
    print(f"  Group types: {dict(group_types)}")
    print(f"  Unresolved: {len(unresolved)}")
    if diagnostics and diagnostics.get("missing_source_block_ref_count"):
        print(f"  Missing source block refs: {diagnostics['missing_source_block_ref_count']}")


def generate_book(
    template_path: str,
    registry_path: str,
    source_index_path: str,
    output_path: str,
) -> None:
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    registry = load_registry(registry_path)
    source_index = load_source_index(source_index_path)
    concepts, atomic_items, logical_groups = _registry_records(registry)
    source_blocks = _source_index_blocks(source_index)
    diagnostics = _evidence_diagnostics(source_blocks, concepts, atomic_items, logical_groups)
    source_text = _stitched_source_text(source_blocks)
    source_html_inner = build_source_html(source_text, source_blocks, concepts, atomic_items)
    source_html = f'<div class="source-text">{_diagnostic_html(diagnostics)}{source_html_inner}</div>'

    source_info = {
        "book_path": source_index.get("source_path", ""),
        "book_title": Path(source_index.get("source_path", registry_path)).stem,
        "book_id": source_index.get("book_id", ""),
        "source_index_id": source_index.get("source_index_id", ""),
        "registry_path": str(registry_path),
        "source_index_path": str(source_index_path),
    }
    metrics = {
        "source_index": source_index.get("metrics", {}),
        "registry": {
            "concept_count": len(concepts),
            "item_count": len(atomic_items),
            "group_count": len(logical_groups),
        },
    }

    _render_view(
        template=template,
        output_path=output_path,
        unit_id="book",
        unit_label="Book Registry",
        book_title=source_info["book_title"],
        source_info=source_info,
        source_html=source_html,
        source_blocks=source_blocks,
        concepts=concepts,
        atomic_items=atomic_items,
        logical_groups=logical_groups,
        unresolved=[],
        metrics=metrics,
        scope="book",
        source_index={
            "schema_version": source_index.get("schema_version", ""),
            "source_index_id": source_index.get("source_index_id", ""),
            "book_id": source_index.get("book_id", ""),
            "units": source_index.get("units", {}),
            "metrics": source_index.get("metrics", {}),
        },
        diagnostics=diagnostics,
    )


def generate(
    template_path: str,
    package_path: str,
    book_path: str | None,
    output_path: str,
) -> None:
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    package = load_package(package_path)
    unit_id = package["unit_id"]
    source_info = package.get("source", {})
    source_blocks = package["source_blocks"]
    concepts = package["concepts"]
    atomic_items = package["atomic_items"]
    logical_groups = package["logical_groups"]
    unresolved = package.get("unresolved_items", [])
    metrics = package.get("metrics", {})

    # Resolve book path
    if book_path is None:
        book_path = source_info.get("book_path", "")
        if book_path:
            # Resolve relative to repo root
            repo_root = Path(__file__).resolve().parents[1]
            book_path = str(repo_root / book_path)

    if not book_path or not Path(book_path).exists():
        print(f"Warning: book source not found at '{book_path}', source pane will be empty.",
              file=sys.stderr)
        source_html = '<div class="source-text"><p class="src-block">Source text not available.</p></div>'
    else:
        source_text = load_source_text(book_path, unit_id=unit_id)
        source_html = build_source_html(
            source_text, source_blocks, concepts, atomic_items
        )
        source_html = f'<div class="source-text">{source_html}</div>'

    data_script = build_data_script(
        source_blocks, concepts, atomic_items, logical_groups,
        unit_id, source_info, metrics,
    )

    # Count summary
    group_types = defaultdict(int)
    groups_with_graph = 0
    groups_without_graph = 0
    for g in logical_groups:
        group_types[g["group_type"]] += 1
        if g.get("graph"):
            groups_with_graph += 1
        else:
            groups_without_graph += 1

    book_title = source_info.get("book_title", Path(book_path).stem) if book_path else "Unknown"
    unit_label = source_info.get("unit_label", unit_id)

    replacements = {
        "{{UNIT_ID}}": html.escape(unit_id),
        "{{UNIT_LABEL}}": html.escape(unit_label),
        "{{BOOK_TITLE}}": html.escape(book_title),
        "{{CONCEPT_COUNT}}": str(len(concepts)),
        "{{ITEM_COUNT}}": str(len(atomic_items)),
        "{{GROUP_COUNT}}": str(len(logical_groups)),
        "{{UNRESOLVED_COUNT}}": str(len(unresolved)),
        "{{BLOCK_COUNT}}": str(len(source_blocks)),
        "{{GROUPS_WITH_GRAPH}}": str(groups_with_graph),
        "{{GROUPS_WITHOUT_GRAPH}}": str(groups_without_graph),
        "<!-- SOURCE_HTML -->": source_html,
        "<!-- DATA_SCRIPT -->": data_script,
    }

    rendered = render(template, replacements)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    print(f"Wrote {output_path}")
    print(f"  Source blocks: {len(source_blocks)}")
    print(f"  Concepts: {len(concepts)}")
    print(f"  Atomic items: {len(atomic_items)}")
    print(f"  Logical groups: {len(logical_groups)} ({groups_with_graph} with graph, {groups_without_graph} without)")
    print(f"  Group types: {dict(group_types)}")
    print(f"  Unresolved: {len(unresolved)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a self-contained reading view HTML from a unit package or book registry."
    )
    parser.add_argument("package", nargs="?", help="Path to unit_package.json for unit scope")
    parser.add_argument("-o", "--output", default="reading_view.html",
                        help="Output HTML path (default: reading_view.html)")
    parser.add_argument("--book", default=None,
                        help="Path to the source book .txt file (auto-detected from package if omitted)")
    parser.add_argument("--registry", default=None,
                        help="Path to book registry.json for book-scope rendering")
    parser.add_argument("--source-index", default=None,
                        help="Path to book source_index.json for book-scope rendering")
    parser.add_argument("--template", default=None,
                        help="Path to template HTML (default: tools/reading_view_template.html)")
    args = parser.parse_args()

    template_path = args.template
    if template_path is None:
        template_path = str(Path(__file__).parent / "reading_view_template.html")

    if not Path(template_path).exists():
        print(f"Error: template not found at {template_path}", file=sys.stderr)
        sys.exit(1)

    if args.registry or args.source_index:
        if not args.registry or not args.source_index:
            print("Error: --registry and --source-index must be provided together", file=sys.stderr)
            sys.exit(2)
        generate_book(
            template_path=template_path,
            registry_path=args.registry,
            source_index_path=args.source_index,
            output_path=args.output,
        )
        return

    if not args.package:
        print("Error: package path is required unless --registry and --source-index are provided", file=sys.stderr)
        sys.exit(2)

    generate(
        template_path=template_path,
        package_path=args.package,
        book_path=args.book,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
