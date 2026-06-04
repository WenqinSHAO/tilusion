# Book-Scoped Source Index Refactor Plan

## Goal

Make source blocks the stable book/document address space that all unit runs,
LLM segments, registry records, and viewers reference. Source blocks should be
computed before overview segmentation and before extraction. They are not owned
by a unit or an LLM segment; units and segments are views/ranges over the same
book-level source index.

This fixes the current instability where block IDs derive from LLM overview
segment IDs such as `overview-segment-0019-block-0000`. Those IDs change when
segmentation changes, which makes registry visualization and cross-run evidence
resolution fragile.

## Current State

- `split_source_blocks()` is deterministic, but it runs inside
  `run_per_segment_extraction_pass()`.
- `block_id` is generated as `{segment_id}-block-{index:04d}`.
- `segment_id` comes from the LLM overview pass, so block IDs are not stable
  across no-cache runs, model changes, prompt changes, or segment-count drift.
- Unit packages carry their own `source_blocks` copies.
- The book registry persists only `source_block_refs`, without a durable global
  resolver from block ID to text.
- `tools/generate_reading_view.py` can render one unit package, but cannot
  reliably render the book registry because it lacks a stable source-block map.

## Target Model

```text
book/document bytes
  -> deterministic BookSourceIndex
     -> book-scoped source blocks: block-000001, block-000002, ...
        -> unit ranges over blocks
        -> LLM overview segment ranges over blocks
        -> per-segment extraction references block IDs
        -> registry stores book-scoped source_block_refs
        -> unit and book viewers resolve evidence through the same index
```

Source block IDs are book scoped by default. Unit IDs and segment IDs are
metadata only. A block can say which unit contains it, but the unit does not
own the ID namespace.

## Cache Identity

The source-block definition is part of the effective book identity for
extraction and visualization.

Add a source-index identity:

```text
source_index_id = sha256({
  source_path or source file digest,
  source_format,
  book_reader_version,
  source_block_splitter_version,
  source_index_schema_version,
})
```

If the source block rules change, the source index changes. From a
visualization perspective this is a different evidence-address space. Old
unit packages and old registry snapshots remain valid only against the old
source index.

## Artifact Shape

Store the index beside the book registry so registry snapshots and source
resolution live under the same book-scoped cache root:

```text
.tilusion_cache/books/<book_id>/source_index.json
```

Initial schema:

```json
{
  "schema_version": "book-source-index-v0.1",
  "source_index_id": "source-index-...",
  "source_path": "...",
  "source_format": "txt",
  "book_id": "book-...",
  "splitter_version": "source-block-splitter-v0.1",
  "units": {
    "unit-0002": {
      "unit_id": "unit-0002",
      "label": "...",
      "order": 2,
      "kind": "chapter",
      "block_refs": ["block-000001", "block-000002"],
      "book_start": 0,
      "book_end": 2521
    }
  },
  "blocks": {
    "block-000001": {
      "block_id": "block-000001",
      "unit_id": "unit-0002",
      "block_index": 1,
      "block_type": "paragraph",
      "book_start": 0,
      "book_end": 412,
      "unit_start": 0,
      "unit_end": 412,
      "text": "...",
      "text_hash": "sha256...",
      "provenance": {
        "created_by": "deterministic",
        "splitter": "source-block-splitter-v0.1",
        "source_index_id": "source-index-..."
      }
    }
  },
  "metrics": {
    "unit_count": 6,
    "block_count": 529,
    "total_chars": 123456,
    "avg_block_size": 233.4
  }
}
```

For compatibility with the current `SourceBlock` dataclass and validation,
unit packages may still include `start/end` as unit-level offsets during the
transition. Source-index records add explicit `book_start/book_end` and
`unit_start/unit_end` to avoid ambiguity.

## Refactor Phases

### Phase 1: Add BookSourceIndex without changing extraction

Files:

- new `tilusion/source_index.py`
- tests `tests/test_source_index.py`
- CLI `tilusion.cli source-index`

Behavior:

- Build a deterministic source index from `build_book_index()` and
  `extract_unit_text()`.
- Reuse the existing splitter logic, but remap IDs to book-scoped
  `block-000001` style IDs.
- Persist to `.tilusion_cache/books/<book_id>/source_index.json`.
- Provide lookup helpers:
  - `load_book_source_index(path)`
  - `block_by_id(index, block_id)`
  - `blocks_for_unit(index, unit_id)`

This phase gives the viewer and debugging tools a stable artifact while the
pipeline still emits legacy segment-derived block IDs.

### Phase 2: Viewer support using source index

Files:

- `tools/generate_reading_view.py`
- `tools/reading_view_template.html`

Behavior:

- Unit scope remains backward compatible with a unit package path.
- Book scope accepts:

```bash
python tools/generate_reading_view.py \
  --registry .tilusion_cache/books/<book_id>/registry.json \
  --source-index .tilusion_cache/books/<book_id>/source_index.json \
  -o html/book_view.html
```

- The viewer shows unresolved evidence refs explicitly if a registry record
  references a block not in the selected source index.
- During transition, support loading unit packages as a fallback source-block
  resolver for old `overview-segment-...-block-...` IDs.

### Phase 3: Pipeline payload migration

Files:

- `tilusion/reading_pipeline.py`
- `tilusion/reading_payloads.py`
- prompt `prompt_per_segment_extraction_v0.2.md`

Behavior:

- Build/load `BookSourceIndex` at the start of book-scope and unit-scope runs.
- Overview segmentation becomes a grouping over existing source blocks instead
  of the creator of source block IDs.
- Per-segment extraction receives blocks selected from the source index.
- LLM output uses book-scoped `block-000001` refs.
- Final unit packages include `source_index_id` in `context_metadata` and
  `source_blocks` from the source index.

### Phase 4: Registry migration and validation

Files:

- `tilusion/book_registry.py`
- `tilusion/registry_delta.py`
- `tilusion/reading_validation.py`

Behavior:

- Store `source_index_id` in registry metadata.
- Reject applying deltas whose source index does not match the registry, unless
  an explicit migration path is provided.
- Preserve old registries with no source index as legacy snapshots.

### Phase 5: Remove legacy segment-derived block ownership

Once enough new packages exist:

- stop generating `{segment_id}-block-*` IDs in extraction paths;
- keep the old splitter API only for compatibility tests/tools;
- update prompts to describe source blocks as book-scoped evidence IDs.

## LLM Context and KV Cache Layout

This refactor enables better KV reuse, but the message layout must be designed
carefully.

Recommended layout for per-segment extraction:

1. System: static task contract and schema. Highly cacheable.
2. Developer/static user prefix: source-index contract, extraction rules,
   output JSON skeleton. Highly cacheable.
3. User variable payload: current segment metadata, selected source blocks,
   book digest, segment hint. Not cacheable across segments, but compact.

Avoid mixing static prompt text with volatile segment text. Persist prompt
composition artifacts so we can confirm the static blocks remain byte-identical
across runs.

## CLI Debug Logging

For book-scope runs, print concise previews and artifact paths:

- before overview: digest source (`cached`, `generated`, or none), char count,
  first N human-readable lines, and digest artifact path;
- before each per-segment pass: segment title, source block count, block ID
  range, hint summary, and source-index ID;
- never dump the full digest or full block text to stderr by default.

Add `--verbose-context` later if full previews are needed.

## Risks

- Unit extraction currently assumes `start/end` are unit-level offsets. The
  transition must avoid silently switching their meaning.
- Existing registries reference legacy block IDs. The viewer needs a fallback
  resolver until those registries are regenerated.
- EPUB structural units can overlap or derive from spine fragments. The source
  index builder must avoid duplicate text by choosing leaf/content units in
  structural order.
- Hash-based block IDs are tempting, but repeated identical text blocks collide.
  Use sequential IDs plus text hashes for now.

## First Implementation Slice

1. Add `tilusion/source_index.py`.
2. Add `python -m tilusion.cli source-index BOOK --cache-dir .tilusion_cache`.
3. Add tests for determinism, book-scoped IDs, unit lookup, and persistence.
4. Do not change extraction outputs yet.

## Implementation Progress

- Phase 1 complete in `87a0534`: `BookSourceIndex`, persistence, and `source-index` CLI.
- Phase 2 complete in `3abe9ac`: book registry viewer from `registry.json` + `source_index.json`.
- Phase 3 first slice: pipeline now builds/loads the book source index and
  passes overlapping book-scoped blocks into per-segment extraction. Segments
  expand to full source-block boundaries so stable `block-*` IDs are not
  clipped into transient sub-block IDs.
- Phase 4 complete: book registries persist `source_index_id`; pipeline,
  registry-delta computation/application, and package validation reject
  mismatched source indexes. Indexed packages reject legacy segment-derived
  block IDs.
- Phase 5 advanced: extraction prompt examples and rules now describe
  book-scoped `block-*` evidence IDs. The legacy splitter remains for
  `split-blocks`, source-index construction, and non-indexed compatibility
  tests/tools, but indexed extraction paths no longer rely on segment-derived
  block IDs.
