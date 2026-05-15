# Reader Index Schema And Extraction Usage

This document defines the current reader-facing contract for later modules.

It covers:

- what the reader returns
- which fields downstream code may rely on
- how to use unit IDs
- how structural extraction is expected to work today

## Current Reader Surface

Code-level entry points:

```python
from tilusion.book_reader import build_book_index, extract_unit_text

index = build_book_index(path)
text = extract_unit_text(path, unit)
```

CLI entry points:

```bash
python -m tilusion.cli index <book> --format text
python -m tilusion.cli index <book> --format json
python -m tilusion.cli extract <book> <unit_id>
```

## Returned Object Shape

`build_book_index(path)` returns a `BookIndex`.

Top-level fields:

- `source_path`
- `source_format`
- `title`
- `metadata`
- `root_id`
- `units`

`units` is a flat list of `StructureUnit` objects. Tree structure is represented by `parent_id` and `children`.

## StructureUnit Contract

Current fields:

- `id`
- `kind`
- `label`
- `order`
- `level`
- `parent_id`
- `children`
- `locator`
- `nav_hint`
- `source_kind`
- `content_kind`
- `title_path`
- `source_range`
- `source_path`
- `start_line`
- `end_line`
- `warnings`
- `notes`

### Stable Fields For Downstream Modules

These are the fields later modules should prefer to consume:

- `id`
- `kind`
- `label`
- `order`
- `parent_id`
- `children`
- `source_kind`
- `content_kind`
- `title_path`
- `source_range`
- `warnings`

These are the main control-plane fields for navigation and later extraction/orchestration work.

### Provisional Fields

These are usable, but should be treated as format-specific details rather than the long-term abstraction boundary:

- `locator`
- `nav_hint`
- `source_path`
- `start_line`
- `end_line`
- `notes`

In particular, downstream modules should not rely on the internal shape of `locator` unless they are implementing reader-adjacent behavior.

## Unit ID Rule

All non-root structural units use one normalized opaque ID scheme:

- `unit-0001`
- `unit-0002`
- `unit-0003`

The root node remains:

- `book`

Rules:

- treat `id` as opaque
- do not parse semantics from the numeric suffix
- do not assume TXT and EPUB produce matching numbering for semantically similar books
- persist cross-references by `id` only within the context of one concrete `BookIndex`

## Meaning Of Key Attributes

### `kind`

Current structural category, for example:

- `book`
- `part`
- `chapter`
- `section`
- `chunk`

### `source_kind`

How the unit was discovered, for example:

- `container`
- `heading`
- `toc`
- `reconciled_toc`
- `fallback_chunk`
- `spine_document`

This is important for downstream trust and review behavior.

### `content_kind`

Current coarse semantic role, for example:

- `book`
- `front_matter`
- `toc`
- `main_text`
- `section`
- `unknown`

This field is intended to help later modules avoid treating every structural unit as narrative body text.

### `title_path`

Normalized ancestry labels from the root down to the current unit.

Example:

```json
["第一册 少年起微末", "第一章 惊蛰"]
```

This is the preferred human-readable ancestry field for later modules.

### `source_range`

Normalized range metadata for later extraction logic.

TXT example:

```json
{
  "kind": "txt-span",
  "start_byte": 16666,
  "end_byte": 59481,
  "start_line": 195,
  "end_line": 652
}
```

EPUB example:

```json
{
  "kind": "epub-range",
  "start": {"spine_index": 6, "char_offset": 0},
  "end": {"spine_index": 7, "char_offset": 0},
  "source_path": "text/part0005.html"
}
```

Later modules should prefer `source_range` over raw `locator` when possible.

## Extraction Usage

Current extraction is unit-based:

1. build the index
2. choose a `StructureUnit`
3. pass that unit to `extract_unit_text(path, unit)`

Example:

```python
index = build_book_index(path)
unit = index.unit_map()["unit-0002"]
text = extract_unit_text(path, unit)
```

Current behavior:

- extraction returns the full text inside the structural unit range
- if an edition mixes main text with commentary, notes, or editorial apparatus inside the same structural unit, the reader returns that mixed source text as-is
- for EPUB, extraction follows the reconciled reading-order ranges produced by the indexer
- for TXT, extraction follows byte spans derived from heading boundaries or fallback chunking

## Current Guidance For Later Modules

- Use the reader index as the only source of structural navigation.
- Do not re-parse raw `.txt` or `.epub` in later modules once an index exists.
- Use `content_kind` and `source_kind` to decide what should be sent to later extraction or analysis stages.
- Treat `warnings` as review signals, not as hard failures.
- Treat separation of main text from commentary/notes as a later extraction or analysis responsibility, not a reader responsibility.
