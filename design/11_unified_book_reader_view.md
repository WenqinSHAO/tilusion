# Unified Book-Level Reading View

## Context

`tools/generate_reading_view.py` currently generates a self-contained HTML viewer
for **one unit package** (`unit_package.json`). It annotates unit source text with
concept surface marks, embeds concepts/items/groups as JSON data, and provides
a right-pane detail view.

The book registry (`registry.json`) accumulates concepts, items, and groups
across all units with book-scoped IDs. It carries richer identity information
(`merged_from`, cross-unit `registry_ref` links, narrative threads) that the
unit-scoped viewer can't show.

**Goal:** Unify unit and book visualization into a single tool that can render
either scope from the same template, with the source text region enlarged to
the full book when operating at book scope.

## Key design choice: global source block index

The extraction structure schema is identical at unit and book scope —
concepts, items, and groups have the same shape. The difference is **which
source blocks they reference** and **which ID namespace they occupy**.

Rather than having each LLM extraction run carry its own copy of source blocks
(which is the current state), we make source blocks a **global, content-addressed
artifact per book**, computed once and shared by all LLM extractions:

1. **Source blocks are extracted once per book**, producing a stable
   `block_id → (text, offsets, owner_unit)` index. Block splitting is a pure
   deterministic function of the text — it has no LLM dependency.

2. **All LLM extractions reference the same block IDs.** Concepts carry
   `source_block_refs: ["seg-0001-block-0003"]`. These block IDs are stable
   across all runs, all units, all re-extractions.

3. **Visualization needs only the block index + registry.** Given which units
   contributed to a book, the viewer has exactly one block index to unify
   annotation across all extracted structures, regardless of how many different
   extraction passes a unit has gone through.

### Dependency chain for stable block IDs

Block IDs have the form `{segment_id}-block-{index:04d}`. The `segment_id`
comes from the overview pass, which is **LLM-backed** — not deterministic.
So stabilizing block IDs requires pinning both the overview segmentation
and the block splitting:

```
book text
  → overview pass (LLM, cached) → resolved segments with stable IDs
    → split_source_blocks() (deterministic) → block IDs
```

When both the overview pass and the block splitter are cached with
content-addressed keys, block IDs become permanently stable. If the overview
is re-run (e.g., with a different prompt version), segment boundaries may
change, producing a new block index — but the old one remains valid for
extractions that referenced it.

### Block index as a standalone artifact

The block index lives at:

```
.tilusion_cache/reading_passes/<book_hash>/block_index.json
```

It is produced by a standalone indexing step (or on first pipeline run) and
keyed by `(overview_pass_cache_key, splitter_version)`. When the overview
pass is re-run with a different prompt, a new block index is produced, but
the old one is not invalidated — existing extractions still reference it.

```
{
  "schema_version": "block-index-v0.1",
  "book_hash": "a1b2c3d4e5f6",
  "overview_cache_key": "abc123def456",
  "splitter_version": "source-block-splitter-v0.1",
  "units": {
    "unit-0001": {
      "unit_label": "Chapter 1",
      "unit_text": "<full unit text>",
      "char_offset": 0,
      "segments": {
        "seg-0001": {
          "title": "Opening",
          "summary": "...",
          "char_start": 0,
          "char_end": 2450
        }
      }
    }
  },
  "blocks": {
    "seg-0001-block-0001": {
      "block_id": "seg-0001-block-0001",
      "unit_id": "unit-0001",
      "segment_id": "seg-0001",
      "block_index": 1,
      "block_type": "paragraph",
      "start": 0,
      "end": 412,
      "text": "In the beginning...",
      "text_hash": "sha256..."
    }
  }
}
```

### How the block index is produced

**First pipeline run (cold):**
1. Overview pass runs → produces segments (cached with content-addressed key)
2. `split_source_blocks()` runs per segment → produces blocks
3. Blocks are assembled into `block_index.json`, keyed by `(overview_cache_key, splitter_version)`
4. The extraction pass payloads reference these block IDs
5. The block index is written to `block_index.json`

**Subsequent runs with same overview:**
1. Overview cache hit → same segments, same segment IDs
2. Block index already exists for this `(overview_cache_key, splitter_version)` → reuse
3. Extraction pass payloads reference the same block IDs

**Subsequent runs with different overview (new prompt, etc.):**
1. Overview pass runs with new prompt → cache miss → new segments, new segment IDs
2. New block index produced, keyed by new overview cache key
3. Old block index remains — old extractions still reference it

### Impact on per-segment extraction

Currently `split_source_blocks()` is called inside `run_per_segment_extraction_pass`
and the blocks are embedded in the payload. In the new design:

- `split_source_blocks()` moves **before** the per-segment pass — it runs once
  per segment during indexing, not per extraction run
- The extraction payload references blocks by ID only (not embedding full block
  dicts), or continues to embed them for backward compatibility — the block
  content is identical either way since splitting is deterministic
- The per-segment cache key still includes block content → same blocks = same
  cache key = cache hits across runs

Practically, this is a small refactor: hoist the block splitting out of the
per-segment pass, produce `block_index.json` alongside the first run, and load
it on subsequent runs.

## Visualization architecture

### Unit scope (unchanged)

Load one `unit_package.json`. Source text = one unit. Block IDs resolve locally.

### Book scope

Load `registry.json` for canonical concepts/items/groups (book-scoped IDs).
Load `block_index.json` for the block → text mapping. No unit packages needed.

```
python tools/generate_reading_view.py \
    --registry .tilusion_cache/<book_hash>/registry.json \
    --block-index .tilusion_cache/<book_hash>/block_index.json \
    -o book_view.html
```

The block index provides:
- Block text for annotation (no need to load unit packages or the original book)
- Unit boundaries for navigation
- Segment structure for the segment outline

### Resolution flow

```
registry concept
  → source_block_refs: ["seg-0001-block-0003", "seg-0002-block-0007"]
    → block_index.blocks["seg-0001-block-0003"]
      → text, unit_id, char offsets
        → annotate surface in text at block position
```

This is a single hash lookup per block reference. No scanning unit packages,
no resolving `latest` pointers, no ambiguity about which run's blocks to use.

## Template changes

1. **Unit navigator.** Unit tabs/dropdown in top bar. Selecting a unit scrolls
   to that unit's text range, filters concept sidebar to concepts grounded in
   that unit (via `source_block_refs` → `block_index` → `unit_id`).

2. **Cross-unit concept indicators.** Each concept shows which units reference
   it. Badges: "U1, U2, U4". Clicking navigates to that unit's range.

3. **Group thread view.** Registry groups carry `narrative_thread_id` and
   cross-unit `continue`/`mutate` edges. Detail panel shows thread evolution
   across units.

4. **Concept identity chain.** `merged_from` and `registry_ref` links shown
   in detail panel.

5. **Book-level stats.** Aggregate counts across all units.

## Scope detection

- `--registry` + `--block-index` → book scope
- Only a unit package path → unit scope (backward compatible)
- `--book` optionally provides full book source for gap-free rendering

## Data embedded in HTML

```json
{
  "scope": "book",
  "block_index": {
    "units": { "unit-0001": {"label": "Chapter 1", "char_start": 0, "char_end": 12450} },
    "segments": { "seg-0001": {"title": "...", "unit_id": "unit-0001", "char_start": 0} },
    "blocks": { "seg-0001-block-0001": {"text": "...", "unit_id": "unit-0001", ...} }
  },
  "registry": {
    "concepts": {"concept-0001": {"surface": "...", "source_block_refs": [...], "merged_from": [...]}},
    "items": {"item-0001": {...}},
    "groups": {"group-0001": {"narrative_thread_id": "...", "graph": {...}}}
  },
  "stitched_text": "<concatenated block texts in unit+position order>",
  "metrics": {"total_units": 3, "total_concepts": 142, ...}
}
```

## Implementation plan

### Step 1: Block index generation (`tilusion/source_blocks.py` or new `tilusion/block_index.py`)

New function `build_book_block_index(book_path, overview_record, unit_texts) → dict`:
- Takes the resolved overview segments + unit texts
- Calls `split_source_blocks()` per segment (already deterministic)
- Assembles `block_index.json` structure
- Writes to `cache_root / "block_index.json"`
- Keyed by `(overview_cache_key, SPLITTER_VERSION)`

The splitting logic itself (`split_source_blocks`) does not change — it's just
called from a different orchestration point.

### Step 2: Pipeline integration (`tilusion/reading_pipeline.py`)

In `run_reading_pipeline()`:
- After overview segmentation + resolve, attempt to load existing block index
- If cache hit on `(overview_cache_key, splitter_version)`: reuse
- If cache miss: compute new block index, write to disk
- Per-segment extraction passes receive blocks from the index (no change to
  the per-segment function signature — it still receives blocks, just sourced
  from the index rather than computed inline)

### Step 3: Viewer: BlockIndex loader (`tools/generate_reading_view.py`)

- `load_block_index(path) → BlockIndex` — reads `block_index.json`, builds
  `block_id → block` lookup, unit boundary map, stitched text
- `BlockIndex.resolve(block_id) → block_dict`
- `BlockIndex.unit_for_block(block_id) → unit_id`
- `BlockIndex.stitched_text() → str`

### Step 4: Viewer: Registry data marshaling

- `load_registry_data(registry_path) → dict` — reads `registry.json`, produces
  slim format for template, attaches cross-unit metadata

### Step 5: Template extensions

- Scope-aware rendering branches
- Unit navigator, cross-unit badges, group threads, identity chains

### Step 6: CLI changes

- Add `--registry` and `--block-index` flags
- Backward compatible: positional package path = unit scope

### Step 7: Tests

- `test_block_index_roundtrip` — build index from segments, resolve blocks
- `test_block_index_stability` — same text + same overview → same block IDs
- `test_registry_data_marshaling` — correct slim format with cross-unit data
- `test_scope_detection` — unit vs book scope from flags
- `test_backward_compat` — unit package input unchanged

## Files changed

| File | Action | Est. lines |
|------|--------|------------|
| `tilusion/block_index.py` | NEW | ~120 |
| `tilusion/reading_pipeline.py` | MODIFY | ~40 (hoist block splitting, write index) |
| `tools/generate_reading_view.py` | MODIFY | ~250 |
| `tools/reading_view_template.html` | MODIFY | ~200 |

## Open questions

1. **Block index versioning.** When the overview pass changes (new prompt
   version), the old block index is not invalidated — old extractions still
   reference it. Should the viewer accept an `--block-index` that differs from
   the one the registry extractions used? Answer: the viewer resolves
   `block_id → text`, so as long as the block IDs match, it works. If a
   concept references a block from an old index that's no longer present, the
   viewer shows the concept but can't annotate its surfaces in source text.
   Acceptable — the concept data is still visible in the detail panel.

2. **Unit ordering for stitch.** Structural order (book index) vs extraction
   order. Structural order = default; `--order extraction` flag for the other.

3. **Memory budget for large books.** Block index + stitched text in one HTML
   payload. Block text only (no gaps) keeps this compact. For very long books,
   the data script could be gzip-compressed (decompress in JS on load).

4. **Registry git history.** Show historical snapshots? Defer to follow-up.

5. **First-run behavior.** If the block index doesn't exist yet (first pipeline
   run), the viewer can fall back to scanning unit packages for source_blocks.
   This is a bootstrapping convenience, not the primary path.
