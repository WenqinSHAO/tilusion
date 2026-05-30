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

**Scope for this batch:** BookRegistry API + deterministic concept merger +
persistence. LLM integration (agent tool calling, pipeline refactor, context pack
builder) is deferred to the next batch aligned with
`design/04_cross_unit_and_user_feedback.md` iteration.

## Revised API

```
BookRegistry(book_path, cache_root=".tilusion_cache")
├── _concepts: dict[str, Concept]
├── _surface_type_index: dict[tuple[str, str], list[str]]
│       # (surface, normalized_type) → [concept_id, ...]
├── _canonical_name_index: dict[str, set[str]]
│       # canonical_name → {concept_id, ...}
├── _surface_lookup: dict[str, set[str]]
│       # surface (any form) → {concept_id, ...}
├── _items: dict[str, AtomicItem]
├── _groups: dict[str, LogicalGroup]
├── _transactions: list[dict]
├── _next_concept_id: int
├── _next_item_id: int
├── _next_group_id: int
│
├── add_concept(concept: Concept) → tuple[str, CollisionInfo | None]
├── add_concepts(concepts: list[Concept]) → list[tuple[str, CollisionInfo | None]]
├── get_concept(concept_id: str) → Concept | None
├── get_by_surface(surface: str) → list[Concept]
├── get_by_canonical_name(name: str) → list[Concept]
├── find_collisions(concept: Concept) → list[CollisionInfo]
├── merge_concepts(ids: list[str]) → str  # returns new concept_id
│
├── add_item(item: AtomicItem) → str
├── get_item(item_id: str) → AtomicItem | None
│
├── add_group(group: LogicalGroup) → str
├── get_group(group_id: str) → LogicalGroup | None
│
├── save() → str                    # writes registry.json, returns content hash
├── load(book_path, cache_root) → BookRegistry  # classmethod
└── rollback(commit_hash) → None    # instance method; reloads state
```

### What changed from the original sketch and why

**Two-index collision detection** (replaces the single `_concept_key_index`).
The original `_concept_key_index: dict[tuple, str]` keyed by
`(surface, type, canonical_name)` can't reproduce the existing Phase 2 + Phase 2.5
logic. Two concepts with same surface+type but empty canonical_names would get
different keys and never collide. The two-index approach mirrors what
`merge_segment_extraction_results` already does:

1. `_surface_type_index`: `(surface, normalized_type) → [concept_id, ...]` — maps
   Phase 2 grouping.
2. `_canonical_name_index`: `canonical_name → {concept_id, ...}` — maps Phase 2.5
   cross-group re-merge.

`find_collisions(concept)`:
- Look up `(concept.surface, normalize_type(concept.concept_type))` in
  `_surface_type_index` → `surface_type_match`
- If concept has non-empty `canonical_name`, look it up in
  `_canonical_name_index` → `canonical_name_match`
- Intersection → `"exact_match"`; surface-only → `"surface_match"`;
  cname-only → `"alias_match"`

`_surface_lookup` is kept as a convenience index for context pack building
(maps any surface form to concept IDs for O(1) text scanning).

**Constructor takes book identity.** `BookRegistry` needs to know where its book
lives for `save()`/`load()`/`rollback()`:

```python
class BookRegistry:
    def __init__(self, book_path: str | Path, cache_root: str | Path = ".tilusion_cache"):
        self._book_path = Path(book_path)
        self._book_hash = stable_book_id(book_path)  # reused from book_context.py:368
        self._cache_dir = book_cache_dir(cache_root, self._book_hash)
        ...
```

**Group ops scoped down.** Only `add_group`/`get_group` in this batch.
`continue_group`, `find_related_groups`, `merge_groups` are deferred — they only
make sense with LLM-driven grouping comparing unit-level groups against book-level
groups.

**Agent tool calling (`apply_operation`) deferred.** Described in the original
design but absent from the API sketch. It belongs with the pipeline refactor in the
next batch, not here.

**_classify_merge_risk stays in `reading_pipeline.py`.** It's only used to screen
LLM-proposed merge deltas in `_validate_merge_deltas`. Moving it now creates a code
dependency that this batch won't exercise and risks a future circular import
(`reading_pipeline.py` → `book_registry.py` → `reading_payloads.py`).

## CollisionInfo

```python
@dataclass
class CollisionInfo:
    existing_concept_id: str
    match_reason: str   # "exact_match" | "surface_match" | "alias_match"
    match_details: dict  # e.g. {"shared_surface": "...", "shared_type": "person"}
```

## DeterministicConceptMerger

Extracted from `_merge_concept_group` in `reading_payloads.py:321`:

```python
class DeterministicConceptMerger:
    @staticmethod
    def merge(members: list[Concept]) -> Concept:
        ...
```

Rules (identical to `_merge_concept_group`):
- Longest `canonical_name` across members, ties broken alphabetically
- First nonempty `summary`
- Set-union of `aliases`, `source_block_refs`, `facets`, `uncertainty`,
  `observed_surfaces`
- Provenance: `"deterministic"` if all members agree on grounding,
  `"synthesis"` otherwise
- `merged_from` populated from all original concept IDs

A `KeepExistingConceptMerger` (first-write-wins) is also provided.

`_merge_concept_into` (in `reading_pipeline.py:799`, in-place enrichment of an
existing concept with new surfaces/refs) is a separate concern. Don't try to unify
the two merge patterns in this batch.

## Persistence

[OPEN Q1] Git-backed or plain filesystem? See Open Questions below.

```
.tilusion_cache/books/{book_hash}/
├── registry.json          # Current state, plain JSON
├── context_packs/         # Not versioned (derived, reproducible)
│   └── {unit_id}/
│       └── context_pack.json
└── [optionally] .git/     # If git-backed
```

- `save()` serializes all concepts/items/groups to `registry.json`. If git-backed,
  also runs `git commit`.
- `load(book_path, cache_root)` reads `registry.json` and rebuilds indices.
- `rollback(commit_hash)` restores a prior state (git checkout or copy-back).

## Transaction Log

[OPEN Q2] In-memory, append-only (see Open Questions):

```python
{"op": "add_concept", "concept_id": "concept-0007",
 "surface": "...", "concept_type": "person", "ts": "2026-05-30T..."}
```

If git-backed: powers commit messages and in-session audit. Cleared after `save()`.
If plain filesystem: may be unnecessary; logging can serve the audit role.

## Import Dependency Management

`BookRegistry` needs `normalize_concept_type` for building identity keys. Currently
it lives in `reading_payloads.py`. To avoid a future circular import
(`reading_pipeline.py` → `book_registry.py` → `reading_payloads.py` ←
`reading_pipeline.py`), move `_CONCEPT_TYPE_NORMALIZATION` and
`normalize_concept_type` from `reading_payloads.py` to `reading_schema.py` — a leaf
module where the `Concept` dataclass already lives.

## Files to Touch

| File | Action | What |
|---|---|---|
| `tilusion/reading_schema.py` | **Modify** | Move `_CONCEPT_TYPE_NORMALIZATION` + `normalize_concept_type` here |
| `tilusion/book_registry.py` | **Create** | `BookRegistry`, `DeterministicConceptMerger`, `CollisionInfo`, persistence |
| `tilusion/reading_payloads.py` | **Modify** | Re-import normalization from `reading_schema` |
| `tests/test_book_registry.py` | **Create** | CRUD, collision detection, merge parity, persistence round-trip |

## Deferred (Next Batch)

- **Pipeline integration**: Replacing `merge_segment_extraction_results` with
  registry calls, pulling concept deltas out of the grouping pass
- **Agent tool calling**: `apply_operation` dispatch, LLM emitting structured
  operations during extraction/grouping
- **Context pack builder**: Updating `book_context.py` for registry-based packs
- **CLI `--scope book`**: Wiring book-level extraction mode
- **`LLMConceptMerger`**: LLM tie-breaking for ambiguous concept collisions
- **Group operations**: `continue_group`, `find_related_groups`, `merge_groups`
- **Item operations**: `link_item_mention`, `refine_item`, `find_similar_items`
- **Concept refinement**: `refine_concept`
- **Diff report**: CLI reporting for same-unit re-extraction

## Verification

1. **CRUD tests**: Add/get concepts, items, groups. Verify indices are correctly
   maintained on each mutation.
2. **Collision detection**: Same surface+type → collision. Same canonical_name
   different surface → collision. Different type same surface → collision with
   type mismatch in `match_details`.
3. **Merge parity**: `DeterministicConceptMerger` output matches
   `_merge_concept_group` for the same test fixtures. Use a small
   `_concept_dict_to_object(d)` helper in the test module to convert existing
   dict-based fixtures to `Concept` objects.
4. **Persistence round-trip**: Create registry, add data, `save()`, `load()` into
   new instance, verify identical state. `rollback()` to previous save, verify
   state matches.
5. **Import sanity**: `book_registry.py` does not import from
   `reading_pipeline.py`.

---

## Open Design Questions

These need explicit decisions before or during implementation.

### Q1: Persistence — git or plain files?

Git-backed persistence (`save()` commits, `rollback()` does `git checkout`) has
good properties but there is **no programmatic git usage** anywhere in `tilusion/`
today. Options:

**A) Git-backed** — `save()` runs `git commit`; `rollback()` does
`git checkout <hash> -- registry.json` + reload.
- Pros: versioning, diffing, rollback come for free
- Cons: git repo init on first save, subprocess dependency, dirty-state
  edge cases to handle

**B) Plain filesystem with atomic writes** — `save()` writes via temp-file +
rename. Rollback via timestamped/hash-named backup copies.
- Pros: no git dependency, simpler
- Cons: reimplementing versioning, harder to diff/inspect history

**C) Plain `save()`/`load()` now, add git later** — start with simple reads/writes
to a single `registry.json`. Design the signatures to accommodate git later.
Rollback would be limited (only restore the last-saved state) or unimplemented
until git is added.

> Recommendation: **(C)**. Git backing is well-motivated but adds complexity not
> needed until multi-unit extraction exercises rollback. Start simple, add git
> when the need is concrete.

### Q2: Is an in-memory transaction log needed?

The original design keeps `_transactions: list[dict]` for commit messages and
in-session audit. But:
- If we go with Q1(C), there's no git commit message to build
- In-session audit can be done with Python `logging`

> Recommendation: Drop `_transactions` from this batch. Add it when there's a
> concrete consumer (git commit messages or a debug/diff feature).

### Q3: Should `DeterministicConceptMerger` work with `Concept` objects or dicts?

The existing `_merge_concept_group` works with dicts. The BookRegistry API uses
`Concept` dataclass instances. Options:

**A) `Concept` objects** — clean API, type-safe, consistent with BookRegistry
**B) Dicts** — matches existing code, easier parity testing
**C) Both** — primary method takes `Concept`; add a `merge_dicts` classmethod
for parity testing

> Recommendation: **(C)**. The primary `merge(members: list[Concept]) -> Concept`
> is the production path. The parity test converts dict fixtures to `Concept`
> objects via a small helper, runs through `merge()`, and compares.

### Q4: How does the next-ID counter survive save/load?

Sequential IDs (`concept-0007`) need the counter to persist across sessions:

**A) Embed in `registry.json`** — top-level `"next_ids": {"concept": 8, ...}`
**B) Scan on load** — `load()` derives `_next_concept_id = max(existing_ids) + 1`
**C) Separate counter file** — `.registry_counters.json`

> Recommendation: **(A)**. Self-contained, travels with the registry state.
> (B) works but is fragile if IDs have gaps. (C) adds an unnecessary file.

### Q5: Normalization move — separate preparatory commit?

Moving `_CONCEPT_TYPE_NORMALIZATION` and `normalize_concept_type` from
`reading_payloads.py` to `reading_schema.py` is pure refactoring touching
existing imports.

> Recommendation: Do it as a **separate preparatory commit** before the
> BookRegistry implementation. Keeps the BookRegistry commit focused and makes
> the import change independently reviewable.

### Q6: `rollback` — reload in place or return new instance?

After `rollback(commit_hash)`:

**A) Reload in place** — the current instance's `_concepts`, `_items`, etc. are
replaced with the historical state. The instance now reflects the rolled-back
point.
**B) Return new instance** — `rollback()` returns a fresh `BookRegistry` loaded
from the historical state. The current instance is unchanged.

> Recommendation: **(A)**. After rollback, the registry IS at the historical
> state — this is the natural mental model. The caller can always `load()`
> separately if they want both states side by side.
