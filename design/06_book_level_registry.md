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

**_transactions dropped.** The in-memory transaction log was underspecified and had
no concrete consumer in this batch (no git commit messages to build, logging serves
the audit role). Dropped.

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

Extracted from `_merge_concept_group` in `reading_payloads.py:321`.
Operates on `Concept` objects only — no dict-based API.

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

A `KeepExistingConceptMerger` (first-write-wins) is also provided for cases where
the caller has already decided the existing concept is canonical.

`_merge_concept_into` (in `reading_pipeline.py:799`, in-place enrichment of an
existing concept with new surfaces/refs) is a separate concern. Don't try to unify
the two merge patterns in this batch.

### Merge boundary: what `merge_concepts` accepts

`merge_concepts(ids)` is **not** a pure "just do it" method. It validates that the
merge is safe before applying `DeterministicConceptMerger`. The rules, adapted from
the existing `_classify_merge_risk` in `reading_pipeline.py:537`:

| Condition | Outcome |
|---|---|
| All concepts share a non-empty `canonical_name` | Safe — merge |
| All concepts share the same surface | Safe — merge |
| Concepts are same `concept_type` and share surface/cname overlap | Safe — merge |
| Concepts are `time_anchor` with different surfaces and no shared cname | Reject — distinct temporal references |
| Concepts are `place` with different surfaces and no shared cname | Reject — distinct locations |
| Concepts are `source` with different surfaces and no shared cname | Reject — distinct cited works |
| Concepts have different `concept_type` and no shared cname | Reject — ambiguous, needs LLM tie-break |

Rejected merges return an error or raise `MergeRejectedError`; the caller is
responsible for deferring to LLM tie-breaking (deferred to next batch), emitting
an `ambiguity_item`, or leaving the concepts separate.

### Summary staleness after deterministic merge

Deterministic merge picks the first nonempty summary. When two concepts with
different summaries merge, the result may not capture the union. This is a known
limitation: deterministic merge produces **structurally correct** results (correct
indices, unioned surfaces, correct provenance), but **semantic coherence** of the
summary is an LLM concern.

Strategy:
- `DeterministicConceptMerger` sets `provenance.grounding = "synthesis"` when
  summaries differ, flagging that the merged concept would benefit from LLM
  refinement. Keep all the origin summaries to facilitate LLM refinement.
- `refine_concept` (deferred to next batch) will handle summary rewriting when
  LLM integration is wired up.
- Until then, the merged concept is correct for identity/collision purposes
  even with a potentially stale summary.

## Persistence

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

### Existing practice: git as application storage layer

Two mature projects demonstrate this pattern:

- **py-docvault** (Python): Git-backed document store where every write produces a
  git commit with author, timestamp, and message. Supports point-in-time retrieval
  at any commit SHA, tag, or branch. Content-addressable IDs.
- **Yamabiko** (Rust): Embedded database with Git-based version control. Key-value
  storage in a local Git repo with `revert-n-commits` and `revert-to-commit`
  commands. Supports JSON, YAML, and Pot data formats.

Both validate the approach: JSON on disk, git commits on every mutation, rollback
via checkout. The pattern is well-established for configuration management,
document versioning, and audit-logged data stores.

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
| `tilusion/reading_schema.py` | **Modify** | Move `_CONCEPT_TYPE_NORMALIZATION` + `normalize_concept_type` here (separate preparatory commit) |
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
- **Concept refinement**: `refine_concept` (including summary rewriting after
  deterministic merge)
- **Diff report**: Produce a diff summary after each registry save, comparing
  against prior state (added/merged/refined concepts, new items, group changes).
  Same-unit re-extraction is a special case of this.

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
4. **Merge boundary**: `merge_concepts` rejects unsafe merges (different types
   with no shared cname, distinct time_anchor/place/source concepts).
5. **Persistence round-trip**: Create registry, add data, `save()`, `load()` into
   new instance, verify identical state. `rollback()` to previous save, verify
   state matches.
6. **Import sanity**: `book_registry.py` does not import from
   `reading_pipeline.py`.

---

## Open Design Questions

### Q1: Persistence — git-backed from the start or plain files first?

Git-backed persistence is a known pattern (py-docvault, Yamabiko — see above).
Options:

**A) Git-backed from the start** — `save()` runs `git commit`; `rollback()` does
`git checkout <hash> -- registry.json` + reload. Needs git repo init on first
save and subprocess git calls.

**B) Plain filesystem with atomic writes** — `save()` writes via temp-file +
rename. Rollback via timestamped/hash-named backup copies.

**C) Plain `save()`/`load()` now, add git later** — start simple, design
signatures to accommodate git. Rollback limited or unimplemented until git added.

> Updated recommendation: **(A)**. The py-docvault and Yamabiko precedents show
> this is a mature pattern. Git-backed from the start avoids a migration later.
> The subprocess surface is small: `git init` (once), `git add + commit` (on
> save), `git checkout` (on rollback), `git log` (for history). Q1 resolution
> also resolves Q2, Q6: no separate transaction log needed (git IS the log),
> rollback reloads in place (natural with git checkout).

### Q2: Transaction log — dropped

`_transactions` is removed from the API. No in-memory operation log — git commit
history serves as the audit trail, and in-session debugging uses Python `logging`.

### Q3: DeterministicConceptMerger — Concept objects only

Works with `Concept` dataclass instances, not dicts. No backward-compatibility
with the dict-based `_merge_concept_group` needed in the merger itself — the
parity test converts dict fixtures to `Concept` objects via a helper.

### Q4: ID counter persistence — embed in registry.json

Top-level `"next_ids": {"concept": 8, "item": 3, "group": 1}` in `registry.json`.
Self-contained, travels with the registry state, survives `save()`/`load()`.

### Q5: Normalization move — separate preparatory commit

Move `_CONCEPT_TYPE_NORMALIZATION` and `normalize_concept_type` from
`reading_payloads.py` to `reading_schema.py` in its own commit before the
BookRegistry implementation.

### Q6: Rollback — reload in place

With git-backed persistence (Q1-A): `rollback(commit_hash)` does
`git checkout <hash> -- registry.json` then reloads the instance's in-memory
state from the historical file. The registry IS at the rolled-back point after
the call. Caller can `load()` separately if they need both states side by side.
