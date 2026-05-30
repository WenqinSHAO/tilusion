# Book-Level Concept/Group/Graph Store — Implementation Plan

## Context

The reading pipeline currently extracts one unit in isolation. Cross-unit extraction
(`design/04_cross_unit_and_user_feedback.md`) requires a book-level store. The core
problem: at book level, maintaining a large concept set becomes too expensive to put
into LLM context every time just to merge/dedup a few new concepts. We need **finer
granular ops** — individual add/merge/dedup — not just batch operations.

The book store replaces the current two-layer concept merge (segment→unit
deterministic merge, then unit→book delta) with a single store that handles concept
identity throughout the pipeline. For the first unit it starts empty; for subsequent
units it already knows prior concepts and can bias extraction via context injection.

## Design

### BookRegistry API

```
BookRegistry
├── _concepts: dict[str, Concept]              # concept_id → Concept
├── _concept_key_index: dict[tuple, str]       # (surface, normalized_type, canonical_name) → concept_id
├── _surface_lookup: dict[str, set[str]]       # surface → {concept_id, ...}
├── _items: dict[str, AtomicItem]              # item_id → AtomicItem
├── _groups: dict[str, LogicalGroup]           # group_id → LogicalGroup
├── _transactions: list[dict]                  # append-only operation log (current session)
│
├── add_concept(concept) → (concept_id, collision_info | None)
├── add_concepts(concepts) → list[(concept_id, collision_info | None)]
├── get_concept(concept_id) → Concept | None
├── get_by_surface(surface) → list[Concept]
├── get_by_canonical_name(name) → list[Concept]
├── find_collisions(concept) → list[(concept_id, match_reason)]
├── merge_concepts(ids) → new_concept_id
│
├── add_item(item) → item_id
├── add_items(items) → list[item_id]
├── get_item(item_id) → AtomicItem | None
│
├── add_group(group) → group_id
├── get_group(group_id) → LogicalGroup | None
├── find_related_groups(concept_refs, item_refs) → list[(group_id, overlap_score)]
├── continue_group(book_group_id, unit_group) → None
├── merge_groups(group_ids) → new_group_id
│
├── save() → str              # Write registry.json, git commit, return commit hash
├── load() → BookRegistry     # Read registry.json from disk (classmethod)
└── rollback(commit_hash) → None  # git checkout, reload
```

**Key design decisions:**
- Identity key for collision detection: `(surface, normalized_concept_type)` tuple,
  augmented by `canonical_name` when present (same logic as current
  `merge_segment_extraction_results` Phase 2)
- `_surface_lookup` maps any surface from `observed_surfaces` + `aliases` + primary
  surface to concept IDs, enabling O(1) surface scanning for context pack building
- All mutations append a transaction record (no silent destructive edits)
- Primary key `concept_id` is book-scope sequential (`concept-0007`)

### Unified Call Paths

Concept merge, concept evolution, and item re-mention all follow the same pattern.
They should not be scattered across per-segment and grouping passes — they are
standalone operations on the BookRegistry. The unifying call path:

```
LLM emits structured operation (from extraction or grouping)
        → BookRegistry API call
        → Collision detection
        → Deterministic merge (if safe) OR LLM tie-break (if ambiguous)
        → Transaction recorded
        → Result (new/existing concept_id, item_id, group_id)
```

**Concepts:** `add_concept` → key collision? →
- No collision → `add_concept` (new book concept)
- Collision, deterministic-safe (same canonical_name + compatible type) → `merge_concepts` with `DeterministicConceptMerger`
- Collision, ambiguous (surface match, different type, no canonical_name) → LLM tie-break or `ambiguity_item`
- LLM can also emit `refine_concept` to update summary/facets when understanding deepens (identity preserved, old state in transaction log)

**Items:** `add_item` → collision? (same concept refs + temporal proximity) →
- No collision → `add_item` (new item)
- Collision, re-mention → `link_item_mention` (add source_block_refs) or `refine_item` (update summary if new details)
- Detection: context pack provides compact item summaries from prior units; LLM emits `item_ref` to known item rather than creating duplicate

**Groups:** `add_group` → overlap with existing? →
- No overlap → `add_group`
- Concept overlap ≥50% or shared timeline concepts → `group_continuation` (absorb unit items into book group)
- LLM proposes `merge_groups` when same theme, different surface

This pattern means concept deltas are no longer embedded in the unit grouping
pass — they are standalone BookRegistry operations. The grouping pass focuses
on creating groups and graph edges; concept/item identity is handled by the
registry.

### Agent Tool Calling

The mechanism unifying all three paths: LLM backend responses are transformed
into BookRegistry API calls. This is the same pattern as the repair loop
(`repair.py`): the LLM emits structured operations, and a deterministic engine
executes them. No LLM directly mutates state.

During extraction or grouping, the LLM can emit:
```json
{"action": "merge_concepts", "concept_ids": ["concept-0012", "concept-0034"]}
{"action": "refine_concept", "concept_id": "concept-0007", "summary": "..."}
{"action": "link_item_mention", "item_id": "item-0042", "source_block_refs": ["..."]}
```

These are validated then applied via `BookRegistry.apply_operation(op)`, which
dispatches to the appropriate method. The operation is recorded in the
transaction log. This keeps the LLM in a proposing role and the registry as the
single source of truth.

### Token-Saving via Context Injection

When the registry has many known concepts, the per-segment extraction prompt guides
the LLM to skip re-extracting entities already in the context pack. The condensed
list is only concepts whose surfaces appear in the current unit's text (via
`_surface_lookup` scan), with compact fields: `{concept_id, canonical_name,
concept_type, summary (≤1 sentence), observed_surfaces}`.

### Merger

Extract the hardcoded merge logic from `_merge_concept_group` into
`DeterministicConceptMerger`:
- Longest `canonical_name` across members, ties broken alphabetically
- First nonempty `summary`
- Set-union of `aliases`, `source_block_refs`, `facets`, `uncertainty`
- `observed_surfaces` union
- Provenance: `"deterministic"` if all members agree on grounding, `"synthesis"` otherwise

A `KeepExistingConceptMerger` (first-write-wins) is also provided.

LLM tie-breaking for ambiguous collisions is deferred — emit `ambiguity_item` for
manual review. An `LLMConceptMerger` can be added when real ambiguous cases emerge.

### Persistence

Git-backed, single working file:

```
.tilusion_cache/books/{book_hash}/
├── .git/                  # Auto-initialized on first --scope book run
├── registry.json          # Current state, plain JSON (git-diffable)
└── context_packs/         # Not versioned (derived, reproducible)
    └── {unit_id}/
        └── context_pack.json
```

- `BookRegistry.save()` writes `registry.json` and runs `git commit` with a
  machine-readable message: `unit unit-0003 | run a1b2c3 | 12 concepts added, 3 merged`
- The `run_key` in the commit message links the registry state back to the
  extraction run cache (`.tilusion_cache/reading_passes/units/{unit_id}/{run_key}/`)
  for full provenance tracing
- Rollback → `BookRegistry.rollback(hash)` does `git checkout <hash> -- registry.json` + reload
- No custom snapshot hashing, no `latest.json` pointer, no transaction log files —
  git handles versioning, diffing, and rollback. The current stage/unit/run cache
  stays in place for logging and debugging.

### Multi-Run Extraction

Multiple extractions of the same unit (different prompts/pipeline versions) are
reconciled through the registry's key collision: same entity extracted in both runs
→ merged into one book concept with enriched surfaces and source refs. This naturally
improves quality across runs.

## Implementation Sequence

### Step 1: BookRegistry Core + Deterministic Merger (`tilusion/book_registry.py`)

Create `BookRegistry` with concept storage, key collision detection, and deterministic
merge. This step is self-contained and testable independently.

- `add_concept` / `add_concepts` with key collision → deterministic merge
- `get_concept`, `get_by_surface`, `get_by_canonical_name`
- `find_collisions`, `merge_concepts`
- `DeterministicConceptMerger` (extracted from `_merge_concept_group`)
- `KeepExistingConceptMerger`
- `save` / `load` / `rollback` with git backing
- Transaction log (in-memory, flushed on save)
- Type normalization reused from `reading_payloads._CONCEPT_TYPE_NORMALIZATION`
- `_classify_merge_risk` extracted from `reading_pipeline.py` into shared utility

### Step 2: Item and Group Storage

Add item and group dicts to BookRegistry with their operations.

- `add_item` / `add_items` / `get_item`
- `add_group` / `get_group`
- `find_related_groups` (concept/item overlap scoring, deterministic)
- `continue_group`, `merge_groups`
- `link_item_mention`, `refine_item` operations


% WQ: to be more focused and trackable in implementation, I think this batch should stop at the realization of the bookstore and the corresponding git commit/save interface. as for how LLM uses the bookstore API and how the extraction pipeline will be changed, such as concept, item, grouping ops to the bookstore during per-segement and unit-level grouping pass should be deferred to a later batch along with the planning iteration on design/04_cross_unit_and_user_feedback.md. the core of that is exactly LLM calling bookstore API which in same cases may further embed LLM calls etc. Also as we bookstore api and realization may need adjustment when we re-factor the extraction pipeline. what we envision here allows for starting defining the bookstore API.

Agreed — this batch stops at the BookRegistry API + persistence. The pipeline
refactor and LLM integration are a separate batch. as for how LLM uses the bookstore API and how the extraction pipeline will be changed, such as concept, item, grouping ops to the bookstore during per-segement and unit-level grouping pass should be deferred to a later batch along with the planning iteration on design/04_cross_unit_and_user_feedback.md. the core of that is exactly LLM calling bookstore API which in same cases may further embed LLM calls etc. Also as we bookstore api and realization may need adjustment when we re-factor the extraction pipeline. what we envision here allows for starting defining the bookstore API.

## Files

| File | Action | What |
|---|---|---|
| `tilusion/book_registry.py` | **Create** | BookRegistry, DeterministicConceptMerger, all CRUD + operations, git persistence |
| `tilusion/reading_payloads.py` | Modify | Extract `_classify_merge_risk` into shared utility (needed by merger) |
| `tests/test_book_registry.py` | **Create** | Unit tests for registry CRUD, collision detection, merge, persistence |

## Deferred (Next Batch)

These will be designed together with the `design/04_cross_unit_and_user_feedback.md`
iteration, since the pipeline refactor and LLM→BookRegistry API integration are
tightly coupled:

- **Pipeline integration**: Replacing `merge_segment_extraction_results` with
  registry calls, pulling concept deltas out of the grouping pass, wiring agent
  tool calling into extraction
- **Context pack builder**: Replacing `book_context.py` with v0.3 shapes
- **CLI `--scope book`**: Wiring book-level extraction mode
- **LLMConceptMerger**: LLM tie-breaking for ambiguous concept collisions
- **`find_similar_items` search API**: Programmatic item lookup
- **Group splitting**: Periodic review pass
- **Diff report**: CLI reporting for same-unit re-extraction

## Verification

1. **Unit tests**: Create BookRegistry, add concepts with/without collisions, verify
   deterministic merge outcomes. Test item/group CRUD. Test save/load/rollback cycle
   with git.
2. **Merge parity**: `DeterministicConceptMerger` produces identical output to
   `_merge_concept_group` for the same inputs (extract and compare).
3. **Persistence round-trip**: Create registry, add concepts, save, load into new
   instance, verify identical state.
