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
  - desirable later
  - not urgent if later modules treat `id` as opaque and do not parse meaning from prefixes like `u` or `toc`
  - should be done before multiple downstream modules persist cross-references to units
- attribute enrichment:
  - needed soon
  - likely additions:
    - `source_kind`: `heading`, `toc`, `fallback_chunk`, `reconciled_toc`
    - `title_path` or equivalent normalized ancestry labels
    - explicit range starts/ends in a normalized shape in addition to raw `locator`
    - `content_kind` hints such as `main_text`, `toc`, `front_matter`, `commentary`, `notes` when detectable
    - confidence / warnings when structure was inferred heuristically
- documentation:
  - needed now, but short
  - enough to define the current `BookIndex` / `StructureUnit` contract, which fields are stable to consume, and which are provisional
