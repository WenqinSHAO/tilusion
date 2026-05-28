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
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


# ── Data loading ──────────────────────────────────────────────────────────────

def load_package(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_source_text(book_path: str) -> str:
    with open(book_path, encoding="utf-8") as f:
        return f.read()


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
                    "concept_type": c.get("concept_type", ""),
                    "canonical_name": c.get("canonical_name", surf),
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
        ctype = ann["concept_type"]
        parts.append(
            f'<mark class="concept-mark {html.escape(ctype)}" '
            f'data-concept="{html.escape(ann["concept_id"])}" '
            f'data-name="{html.escape(ann["canonical_name"])}">'
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

    # Sort blocks by start position so rendering follows source order
    sorted_blocks = sorted(source_blocks, key=lambda b: b["start"])

    # Auto-detect the book-level offset (e.g. front matter before unit text)
    offset = _resolve_source_offset(sorted_blocks, source_text)

    def _resolve_pos(pos: int) -> int:
        return pos + offset

    parts: list[str] = []
    cursor = _resolve_pos(sorted_blocks[0]["start"]) if sorted_blocks else 0

    for block in sorted_blocks:
        block_id = block["block_id"]
        unit_start = block["start"]
        unit_end = block["end"]
        start = _resolve_pos(unit_start)
        end = _resolve_pos(unit_end)
        block_text = source_text[start:end]

        # Verify round-trip against block's stored text
        if block_text != block["text"]:
            block_text = block["text"]

        # Render any uncovered text between cursor and this block
        if start > cursor:
            gap_text = source_text[cursor:start]
            if gap_text.strip():
                gap_start = cursor - offset
                gap_end = start - offset
                parts.append(
                    f'<div class="src-gap" data-start="{gap_start}" data-end="{gap_end}">'
                    f'{html.escape(gap_text)}</div>'
                )

        # Build item refs data
        item_ids = sorted({it["item_id"] for it in block_items.get(block_id, [])})
        item_data = ""
        if item_ids:
            item_data = f' data-items="{html.escape(",".join(item_ids))}"'

        ref_concepts = block_concepts.get(block_id, [])
        inner = _annotate_text(block_text, block_id, ref_concepts, concept_surfaces)

        # Wrap in block element with unit-level data attributes (matching source_blocks)
        pos_attrs = f' data-start="{unit_start}" data-end="{unit_end}"'
        parts.append(f'<p class="src-block" data-block="{html.escape(block_id)}"{item_data}{pos_attrs}>{inner}</p>')

        cursor = max(cursor, end)

    # Render any trailing text after the last block
    max_end = _resolve_pos(sorted_blocks[-1]["end"]) if sorted_blocks else 0
    if cursor < max_end:
        tail_text = source_text[cursor:max_end]
        if tail_text.strip():
            tail_start = cursor - offset
            tail_end = max_end - offset
            parts.append(
                f'<div class="src-gap" data-start="{tail_start}" data-end="{tail_end}">'
                f'{html.escape(tail_text)}</div>'
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


def build_data_script(
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    logical_groups: list[dict[str, Any]],
    unit_id: str,
    source_info: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    """Return a <script> block embedding structured data as JSON."""
    payload = {
        "unit_id": unit_id,
        "source": source_info,
        "source_blocks": source_blocks,
        "concepts": [slim_concept(c) for c in concepts],
        "atomic_items": [slim_item(it) for it in atomic_items],
        "logical_groups": logical_groups,
        "metrics": metrics,
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
        source_text = load_source_text(book_path)
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
        description="Generate a self-contained reading view HTML from a v0.3 unit package."
    )
    parser.add_argument("package", help="Path to unit_package.json")
    parser.add_argument("-o", "--output", default="reading_view.html",
                        help="Output HTML path (default: reading_view.html)")
    parser.add_argument("--book", default=None,
                        help="Path to the source book .txt file (auto-detected from package if omitted)")
    parser.add_argument("--template", default=None,
                        help="Path to template HTML (default: tools/reading_view_template.html)")
    args = parser.parse_args()

    template_path = args.template
    if template_path is None:
        template_path = str(Path(__file__).parent / "reading_view_template.html")

    if not Path(template_path).exists():
        print(f"Error: template not found at {template_path}", file=sys.stderr)
        sys.exit(1)

    generate(
        template_path=template_path,
        package_path=args.package,
        book_path=args.book,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
