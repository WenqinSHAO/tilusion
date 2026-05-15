# Progress

## Done

- Design intent aligned and recorded in the planning docs.
- Repo initialized; planning baseline committed in `975ef8b`.
- Reader slice implemented and committed in `f4d1d3d`.
- Current reader supports:
  - `.txt`: streamed indexing, encoding detection, heading-based structure, byte-range extraction
  - `.epub`: `container.xml` -> OPF -> manifest/spine/TOC indexing, reading-order extraction
  - CLI: `index` and `extract`
  - outputs: JSON for code, text outline for humans
- Reader hardening completed against sample books:
  - TXT duplicate TOC-like heading blocks filtered out of primary structure
  - suspicious EPUB TOC targets reconciled against actual spine document titles
  - EPUB `*-back` anchors normalized to heading starts
  - EPUB XHTML decoding made robust when charset metadata is incomplete
- Index output enriched for downstream modules with:
  - `source_kind`
  - `content_kind`
  - `title_path`
  - normalized `source_range`
  - explicit `warnings`
- External `unit_id` values normalized across TXT and EPUB output to a single opaque scheme: `unit-0001`, `unit-0002`, ...
- Reader schema and extraction usage documented in `docs/reader_index_schema.md`.
- Regression coverage exists for TXT heading/chunking/duplicate-TOC behavior and EPUB TOC/range reconciliation.

## Reader Contract

- Later modules should treat the reader as the source of:
  - a full-document structure index
  - per-unit navigation data
  - on-demand extraction for a chosen structural unit
- Later modules should not parse raw `.txt` or `.epub` directly once a reader index exists.
- Current public surface is effectively:
  - `build_book_index(path) -> BookIndex`
  - `extract_unit_text(path, unit) -> str`
- Current `StructureUnit` fields usable downstream:
  - `id`, `kind`, `label`, `order`, `level`, `parent_id`, `children`
  - `locator`, `nav_hint`, `source_path`, `start_line`, `end_line`, `notes`

## Ongoing

- Goal: make the reader contract explicit enough for extraction modules to build on safely.
- Gap: no benchmark yet against a true 500MB input.
- Gap: extraction currently returns the edition text present inside a structural unit, including embedded notes/commentary when the source edition mixes them with the main text.

## Index Schema Evaluation

- `unit_id` unification:
  - completed for external reader output
  - later modules should still treat `id` as opaque and not derive semantics from numbering
- attribute enrichment:
  - needed soon
  - likely additions:
    - `source_kind`: `heading`, `toc`, `fallback_chunk`, `reconciled_toc`
    - `title_path` or equivalent normalized ancestry labels
    - explicit range starts/ends in a normalized shape in addition to raw `locator`
    - `content_kind` hints such as `main_text`, `toc`, `front_matter`, `commentary`, `notes` when detectable
    - confidence / warnings when structure was inferred heuristically
- documentation:
  - current reader contract documentation added
  - keep future documentation changes close to real schema changes
