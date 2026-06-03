# Cross-Unit Entity Consistency: Refactoring & Implementation Plan

Status: **plan** — derived from analysis in `14_cross_chunk_entity_consistency_analysis.md`.

## Overview

Four implementation phases, in dependency order. Each phase builds on the
previous and produces independently mergeable, testable increments.

| # | Phase | Est. scope | Depends on |
|---|-------|------------|------------|
| 1 | Embedding cache | ~250 lines | None |
| 2 | Soft typing (type facets) | ~400 lines | None (independent of Phase 1) |
| 3 | Richer book digest & per-segment hints | ~350 lines | Phase 1, Phase 2 |
| 4 | Concept-to-higher-order-reference | ~250 lines | Phase 3 |

### What we reuse and improve

- **Multi-round agentic loop**: `run_agentic_resolution_pass()` in
  `reading_pipeline.py` — the loop survives; we feed it fewer/better
  candidates so it runs fewer turns.
- **BookRegistry**: `BookRegistry` with git-backed persistence — we add
  embedding-aware fields and facet indexes but the save/load/rollback
  contract is unchanged.
- **Cache & git**: `book_cache_dir()`, `stable_book_id()`, git commits on
  registry save — we extend the cache to cover embeddings.
- **Registry tools**: `registry_tools.py` — `search_concepts` query
  quality improves when embeddings of new concepts are pre-computed.
- **Semantic search**: `_dual_signal_select()` in `registry_index.py` —
  we add embedding caching so repeated calls reuse pre-computed vectors,
  eliminating the dominant cost of re-encoding.

---

## Phase 1: Embedding Cache (No Vector DB Needed)

### Motivation

From the unit-0003 run (240 unit concepts, ~260 registry concepts):

**Per-`search_concepts` cost** (observed from agentic turn logs):
Each `search_concepts` tool call re-runs `_dual_signal_select()`, which
re-encodes ALL registry texts (~260, 140s) and ALL unit texts (~235, 115s)
from scratch. Individual calls take **142–183 seconds** — almost entirely
embedding recomputation. The actual BM25 + cosine compute is 35ms.

**Per-turn example** (from actual logs):
```
Turn 6: 9 search_concepts calls × ~158s avg = ~1,422s
Turn 7: 10 search_concepts calls × ~149s avg = ~1,490s
Turn 8: 8 search_concepts calls × ~175s avg = ~1,400s
3 turns alone: ~4,300s (72 min) — all embedding recomputation
```

**Full run estimate** (44 tool calls total):
| Step | Time | % |
|------|------|---|
| Embedding recomputation (44 calls × ~255s) | **~11,200s** | **dominant** |
| Actual cosine similarity (44 calls × 35ms) | ~1.5s | negligible |
| LLM inference / judgment | modest | — |
| **Total cross-unit merge** | **~44 min observed** | |

The observed 44 minutes is NOT LLM inference — it's the embedding model
running the same texts through Qwen3-0.6B 44 times. Each `search_concepts`
call independently re-encodes the full registry + unit concept set.

Three conclusions:

1. **Embedding cache is the single biggest win.** After unit 1's first
   `search_concepts` call, every subsequent call uses cached embeddings.
   44 calls drop from ~11,200s to ~1.5s (cosine only). This is not a 10%
   improvement — it eliminates the dominant cost entirely.
2. **Vector DB is unnecessary** — cosine compute is 35ms on 260 vectors.
   The problem was never retrieval speed; it was recomputing embeddings
   inside every tool call. A simple file-based cache solves it completely.
3. **After caching, agentic LLM turns become the next bottleneck** — but
   each turn will be ~5-10s (LLM inference + 35ms cosine) instead of
   ~160s. Phases 2+3 then reduce how many concepts need `search_concepts`
   at all.

Two fixes in this phase: (a) cache registry + unit embeddings so tool
calls are ~35ms instead of ~160s, (b) improve agentic `search_concepts`
query guidance so fewer turns are needed per concept. (Dropping BM25 was
tested but reverted — BM25 catches partial token overlap that the
deterministic filter misses, at negligible cost.)

### 1a. Embedding cache in `_dual_signal_select` (`registry_index.py`)

**What**: A two-layer cache that prevents `_dual_signal_select()` from
re-encoding the same texts every time a `search_concepts` tool call fires.

Layer 1 — **in-memory** (per pipeline run): After the first call encodes
registry + unit texts, embeddings stay in module-level dicts. Subsequent
`search_concepts` calls in the same agentic turn (or later turns) hit
memory directly — no re-encoding. This is the critical path: turn 6's
9 calls all share the same embeddings.

Layer 2 — **disk** (persistent): Keyed by `(concept_id, sha256(text))`.
Survives across pipeline runs. When a new unit starts, the registry's
existing concepts all hit disk (no re-encoding), and only net-new concepts
added by the previous unit need fresh embeddings.

**How**:
- New class `EmbeddingCache` in `registry_index.py`:
  ```python
  class EmbeddingCache:
      def __init__(self, cache_dir: Path): ...
      def get(self, key: str) -> np.ndarray | None: ...
      def put(self, key: str, embedding: np.ndarray) -> None: ...
      def key_for(self, concept_id: str, text: str) -> str: ...
  ```
- Cache directory: `<registry_cache_dir>/embeddings/` — alongside
  `registry.json` in the git-backed cache.
- Add module-level in-memory cache in `registry_index.py`:
  ```python
  _reg_embeddings_cache: dict[str, np.ndarray] = {}
  _unit_embeddings_cache: dict[str, np.ndarray] = {}
  ```
- Modify `_dual_signal_select()`:
  - Before `model.encode(reg_texts)`: check each text's hash against
    `_reg_embeddings_cache` (memory) → `EmbeddingCache` (disk). Only
    encode cache misses, merge cached + new into the return array.
  - Same pattern for unit texts in `_unit_embeddings_cache`.
  - Print cache hit rate to stderr: `registry: 258/260 cached (99%), unit: 230/235 cached (98%)`.
- Modify `_dual_signal_select_groups()`: same two-layer caching for group texts.
- `BookRegistry` gains `embedding_cache_dir` property (derived from
  `self._cache_dir`).

**Expected impact**: Each `search_concepts` call drops from ~160s to ~35ms
after the first call in a unit. For 44 calls: ~7,000s → ~1.5s.

**Files**: `tilusion/registry_index.py` (+130), `tilusion/book_registry.py` (+5)

### 1b. Reuse unit concept embeddings (`registry_delta.py` / `registry_index.py`)

**What**: After a concept is confirmed new and added to the registry, save
its already-computed unit embedding to the cache so it's available for
later units without re-encoding.

**How**:
- `_dual_signal_select()` returns a `dict[str, np.ndarray]` mapping
  unit concept text hash → embedding alongside the candidate set.
- `apply_registry_delta()` accepts optional `unit_embeddings: dict[str, np.ndarray]`
  and writes embeddings for newly-added concepts to the `EmbeddingCache`.
- `run_reading_pipeline()` wires the embedding dict through:
  `_dual_signal_select` → `compute_registry_delta` → `apply_registry_delta`.

**Files**: `tilusion/registry_index.py` (+30), `tilusion/registry_delta.py` (+15),
`tilusion/reading_pipeline.py` (+20)

### 1c. Drop BM25 for deterministic-filter leftovers — REVERTED

**Decision**: NOT implemented. BM25 (35ms) catches partial token overlap
(e.g., "narrator" in both "the old man" and "Shen Fu" concepts) that the
deterministic filter (exact surface/cname/alias match) misses. The real
bottleneck was embedding recomputation, not BM25. With the cache, the
pipeline keeps BM25 as a useful lexical signal at negligible cost.

### 1d. Improve agentic search_concepts query guidance

**What**: The agent currently composes poor `search_concepts` queries
(mixed Chinese/English glosses, using surface alone instead of summary).
Add a note to the v0.2 prompt instructing the agent to use the full
concept summary as the search query when surface/cname matching fails.

**How**:
- Edit `prompt_concept_resolution_v0.2.md`: add a paragraph in the
  tool-calling section explaining that `search_concepts` uses embedding
  similarity, so passing the concept's full summary (not just surface)
  produces better results.

**Files**: `tilusion/prompts/prompt_concept_resolution_v0.2.md` (+10)

### Tests for Phase 1

- `test_embedding_cache_hit_and_miss`
- `test_embedding_cache_persistence`
- `test_unit_embedding_reuse_in_delta`

### Verification

```
~/.virtualenvs/shredder/bin/python -m pytest tests/test_registry_index.py tests/test_registry_delta.py -q
```

---

## Phase 2: Soft Typing (Type Facets)

### Motivation

`TYPE_FAMILIES` is a hand-maintained dict with known gaps (`term` ↔ `method`,
`source` ↔ `term`, `time_anchor` ↔ `other`). AutoSchemaKG shows that set-based
type compatibility (facet set intersection) handles all these edge cases
without manual ontology maintenance.

The `Concept` dataclass already has a `facets: list[str]` field (line 217 of
`reading_schema.py`). It's currently unused — always empty. Phase 2 activates it.

### 2a. Extraction-time facet generation (`prompt_per_segment_extraction_v0.2.md`)

**What**: Instruct the extractor to populate `facets` with 2-5 type-describing
phrases at varying abstraction levels for each concept.

**How**:
- Edit `prompt_per_segment_extraction_v0.2.md`:
  - Add `facets` to the concept schema example (already present as `"facets": []`).
  - Add a rule: "For each concept, provide 2-5 type-describing phrases at
    different abstraction levels in `facets`. Examples: a treaty concept →
    `['treaty', 'legal document', 'historical event', 'agreement']`; an
    emperor → `['person', 'ruler', 'historical figure', 'emperor']`."
  - Facets are NOT a replacement for `concept_type` — they supplement it.
- The extractor already runs per-segment, so it has local context to generate
  meaningful facets. No additional API calls.

**Files**: `tilusion/prompts/prompt_per_segment_extraction_v0.2.md` (+15)

### 2b. Deterministic type compatibility via facet intersection (`registry_index.py`)

**What**: Replace `TYPE_FAMILIES` + `_relaxed_types()` with facet set
intersection as the type-compatibility test. Hard families become a fallback
for concepts without facets.

**How**:
- New function in `registry_index.py`:
  ```python
  def _types_compatible(unit_facets: set[str], reg_facets: set[str],
                        unit_type: str, reg_type: str) -> bool:
      """True if facet sets intersect or hard types are in the same family."""
      if unit_facets and reg_facets:
          return bool(unit_facets & reg_facets)
      # Fallback to TYPE_FAMILIES for legacy concepts without facets
      relaxed = _relaxed_types(unit_type)
      return reg_type in relaxed or "*" in relaxed
  ```
- Modify `_deterministic_filter()`:
  - Add `facets` to the registry index entries in `build_registry_index()`.
  - After the type-family gate, add a facet-intersection gate.
- Modify `_dual_signal_select()` type-filter path:
  - Use `_types_compatible()` instead of `_relaxed_types()` for the
    type mask, when facets are available.
- Keep `TYPE_FAMILIES` as fallback (no breaking change).
- Add `"facets"` field to `build_registry_index()` output.

**Files**: `tilusion/registry_index.py` (+30, ~20 modified)

### 2c. Facet-aware merge boundary (`book_registry.py`)

**What**: `_check_merge_boundary()` currently only considers hard types.
Add facet intersection as an additional signal — if two concepts share
at least one facet, the merge is safe even across different hard types.

**How**:
- In `_check_merge_boundary()`, add before the rejection path:
  ```python
  # Facet intersection → type compatibility established
  all_facets = [set(m.facets) for m in members if m.facets]
  if len(all_facets) >= 2:
      common = all_facets[0].intersection(*all_facets[1:])
      if common:
          return None  # safe to merge
  ```
- `DeterministicConceptMerger.merge()` already unions facets (line 71 of
  `book_registry.py`), so merged concepts accumulate facets from all members.

**Files**: `tilusion/book_registry.py` (+15)

### 2d. Registry index includes facets (`registry_index.py`)

**What**: `build_registry_index()` already returns concept dicts; add
`"facets"` field so the deterministic filter and dual-signal can use them.

**How**:
- Add `"facets": concept.facets[:10]` to the index entry dict in
  `build_registry_index()`.
- `_deterministic_filter()` reads `reg.get("facets", [])` and constructs
  a set for intersection with unit concept facets.

**Files**: `tilusion/registry_index.py` (+5, ~5 modified)

### Migration path

- Concepts already in the registry without facets: `_types_compatible()`
  falls back to `TYPE_FAMILIES`. No data migration needed.
- Concepts extracted after the prompt update: carry facets, get facet-based
  matching.
- Over time, as more concepts carry facets, `TYPE_FAMILIES` becomes dead
  code that can be removed.

### Tests for Phase 2

- `test_facet_intersection_allows_cross_type_merge`
- `test_facetless_concepts_fallback_to_type_families`
- `test_deterministic_filter_with_facets`
- `test_merge_boundary_with_facets`

### Verification

```
~/.virtualenvs/shredder/bin/python -m pytest tests/test_registry_index.py tests/test_book_registry.py -q
```

---

## Phase 3: Richer Book Digest & Per-Segment Hints

### Motivation

The current per-segment hint (`segment_hint_payload()` in `overview.py:315`)
only passes the overview's `extraction_hints` list — lightweight natural
language cues from the segmentation pass. These are too light to help the
extractor distinguish:

1. **Known concepts**: concepts already in the registry that appear in this
   segment. The extractor should flag them via aliases/observed_surfaces
   rather than re-extracting them as new concepts.
2. **New concepts**: genuinely new entities that need full extraction.

The book digest (`book_digest.py`) generates a prose summary of the registry
state but is only updated between units, not per-segment, and is limited to
50 entities.

### 3a. Per-segment known-concept lookup (`registry_index.py`)

**What**: Given a segment's source blocks, look up which registry concepts
already reference those blocks (via `source_block_refs`). These are
"known to appear in this segment" and should be passed as hints.

**How**:
- New function in `registry_index.py`:
  ```python
  def known_concepts_for_segment(
      registry: BookRegistry,
      segment_block_ids: list[str],
      *,
      max_concepts: int = 30,
  ) -> list[dict[str, Any]]:
      """Return registry concepts that cite any of the given source blocks."""
  ```
- Builds a reverse index `block_id → {concept_id, ...}` from registry
  concepts' `source_block_refs`. Returns compact entries (concept_id,
  surface, canonical_name, concept_type, summary truncated to 80 chars).
- Called in `run_reading_pipeline()` per segment, result added to
  `context["known_concepts"]`.

**Files**: `tilusion/registry_index.py` (+50)

### 3b. Revised per-segment hint payload (`reading_pipeline.py` / `overview.py`)

**What**: Extend the context dict passed to the extraction LLM with:
- `known_concepts`: registry concepts that appear in this segment's blocks
- `new_concept_guidance`: if this is a later unit and most concepts are
  known, tell the extractor to focus on detecting genuinely new entities
  rather than re-describing known ones.

**How**:
- Modify `segment_hint_payload()` or add a wrapper that enriches the context:
  ```python
  def enriched_segment_context(
      segment: ResolvedOverviewSegment,
      known_concepts: list[dict[str, Any]],
      unit_index: int,
  ) -> dict[str, Any]:
  ```
- The extraction prompt already has a `context` field; these fields slot in.
- Add a brief rule to the extraction prompt: "If `context.known_concepts`
  lists concepts already in the registry, do not re-extract them as new.
  Instead, add their surfaces to `observed_surfaces` of your local concept
  and reference them via `aliases`." (The exact mechanism depends on whether
  we want the extractor to emit link proposals or just flag presence.)

**Files**: `tilusion/overview.py` (+30), `tilusion/reading_pipeline.py` (+30),
`tilusion/prompts/prompt_per_segment_extraction_v0.2.md` (+15)

### 3c. Improved book digest (`book_digest.py`)

**What**: The current digest is a flat entity table limited to 50 entries.
Improve it to be more useful as a "world model":
- Stratify by concept type (persons, places, terms, time_anchors, etc.)
- Mark high-frequency concepts (appearing in many segments) separately
  from low-frequency ones.
- Include a "recently added" section for concepts from the most recent
  unit — these are most likely to appear again.

**How**:
- `_build_digest_payload()` already builds the payload. Extend it:
  - Add `type_groups`: entities grouped by concept_type.
  - Add `high_frequency`: concepts appearing in ≥3 source blocks.
  - Add `recent`: concepts added in the last unit (tracked via
    `provenance.source_unit`).
- The `MAX_ENTITIES_IN_DIGEST` stays at 50 but the structured grouping
  makes better use of the budget.
- The digest prompt (`prompt_book_digest_v0.1.md`) already generates prose
  sections; the structured input helps it produce better guidance.

**Files**: `tilusion/book_digest.py` (+50)

### 3d. Per-segment known/new concept flagging

**What**: With known concepts identified per segment (3a), track per concept
whether it's "known" (appears in registry already) or "new" (genuinely
unseen). This metric feeds back into the cross-unit merge: known concepts
should merge deterministically; new concepts need embedding search.

**How**:
- After per-segment extraction, compare extracted concept surfaces against
  `known_concepts` for that segment.
- Add a `known_in_registry: bool` field to the unit concept metadata
  (internal, not part of the extraction schema).
- In cross-unit merge (`compute_registry_delta`), skip embedding search
  for concepts flagged `known_in_registry=True` and go straight to
  deterministic merge.
- This eliminates the 97% waste: only genuinely new concepts need
  embedding-based search.

**Files**: `tilusion/reading_pipeline.py` (+40), `tilusion/registry_delta.py` (+15)

### Tests for Phase 3

- `test_known_concepts_for_segment`
- `test_enriched_segment_context`
- `test_book_digest_with_type_groups`
- `test_known_concept_skips_embedding_search`

### Verification

```
~/.virtualenvs/shredder/bin/python -m pytest tests/test_registry_index.py tests/test_book_digest.py tests/test_reading_pipeline.py -q
```

---

## Phase 4: Concept-to-Higher-Order-Reference Detection

### Motivation

The analysis identified a gap: concepts that refer to items/events/groups
(e.g., "the battle" referring to a specific event item, "the treaty"
referring to a source) have no first-class representation. The extractor
may or may not resolve these locally. When unresolved, they become inputs
for agentic resolution.

This phase adds detection and flagging — not full resolution — so we can
measure the prevalence of the problem before building the resolution mechanism.

### 4a. Extraction-time reference detection (`prompt_per_segment_extraction_v0.2.md`)

**What**: Instruct the extractor to flag when a concept surface or summary
refers to a higher-order structure (item, event, group) rather than a
standalone entity.

**How**:
- Add a new optional field to concepts: `refers_to: list[dict]` — each
  entry has `{target_type: "item"|"group"|"event", confidence: "certain"|"likely"|"possible",
  evidence: "brief quote from source"}`
- Add to the extraction prompt:
  - "When a concept mention in the text refers to an atomic item, event,
    or logical group rather than a standalone entity, add a `refers_to`
    entry. Example: 'the battle' referring to a specific battle event →
    `{target_type: 'event', confidence: 'likely', evidence: 'the battle'}`."
  - "This is a DETECTION only. You do not need to resolve the reference.
    Flag it even if the target is not extracted in this segment."

**Files**: `tilusion/prompts/prompt_per_segment_extraction_v0.2.md` (+20)

### 4b. Schema changes (`reading_schema.py`)

**What**: Add `refers_to` as an optional field on `Concept`.

**How**:
- Add `refers_to: list[dict[str, Any]] = field(default_factory=list)` to
  the `Concept` dataclass.
- Add to `to_dict()` output.
- Add to `registry_delta.py` serialization (already uses `asdict()` so
  should be automatic).
- `DeterministicConceptMerger._union()` handles list fields generically
  so `refers_to` is automatically merged.

**Files**: `tilusion/reading_schema.py` (+3)

### 4c. Metrics collection (`reading_pipeline.py`)

**What**: Count how many concepts have `refers_to` entries, grouped by
confidence level and target type. Report in unit metrics.

**How**:
- After `merge_segment_extraction_results()`, scan all concepts for
  `refers_to` entries.
- Add to unit metrics:
  ```json
  "higher_order_references": {
      "total": 12,
      "by_target_type": {"event": 5, "group": 4, "item": 3},
      "by_confidence": {"certain": 2, "likely": 7, "possible": 3},
      "unresolved": 10
  }
  ```
- Log summary to stderr: `[unit-N] 12 higher-order references detected (2 certain, 7 likely, 3 possible)`.

**Files**: `tilusion/reading_pipeline.py` (+30)

### 4d. Cross-unit reference resolution (agentic, deferred)

**What**: Once metrics confirm the problem is worth solving, add an agentic
pass that takes `refers_to` entries and resolves them against the registry
using `search_concepts`, `search_groups`, and `get_item` tools.

**Deferred**: This is the "bonus feature" — implement only after Phases 1-3
are stable and metrics from 4c justify it. The design sketch:

- New agentic pass `run_reference_resolution_pass()`:
  - Input: concepts with unresolved `refers_to` entries.
  - Tools: `search_items` (new), `get_item`, `search_groups`, `get_group`.
  - Output: `link_to_item` / `link_to_group` proposals.
- This pass runs after concept resolution and before group resolution
  in `run_reading_pipeline()`.
- Resolved references become `concept_refs` in the linked item/group,
  improving group formation.

### Tests for Phase 4

- `test_concept_refers_to_serialization`
- `test_higher_order_reference_metrics`
- `test_refers_to_field_in_extraction_schema`

### Verification

```
~/.virtualenvs/shredder/bin/python -m pytest tests/test_reading_schema.py tests/test_reading_pipeline.py -q
```

---

## Implementation Order (Summary)

```
Phase 1 (embedding cache + BM25 drop + query guidance)
  ├─ Unblocks: faster iteration on all subsequent phases
  ├─ Risk: low — pure optimization, no schema change
  └─ Deliverable: ~250s → ~0s embedding overhead per unit after unit 1

Phase 2 (soft typing via type facets)
  ├─ Unblocks: broader deterministic matching reduces LLM load
  ├─ Risk: medium — extraction prompt change, but backward-compatible
  └─ Deliverable: fewer concepts need agentic merge

Phase 3 (richer hints + known/new flagging)
  ├─ Unblocks: Phase 4 reference detection is more useful with known concepts
  ├─ Risk: medium — touches extraction prompt, hint payload, pipeline wiring
  └─ Deliverable: extractor distinguishes "flag known" from "extract new"

Phase 4 (concept-to-higher-order-reference detection)
  ├─ Unblocks: metrics inform whether full resolution is worth building
  ├─ Risk: low — detection only, no resolution yet
  └─ Deliverable: metrics on prevalence of higher-order references
```

## Files Affected

| File | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|------|---------|---------|---------|---------|
| `tilusion/registry_index.py` | +175, ~10 | +30, ~20 | +50 | — |
| `tilusion/book_registry.py` | +5 | +15 | — | — |
| `tilusion/registry_delta.py` | +15 | — | +15 | — |
| `tilusion/reading_pipeline.py` | +20 | — | +70 | +30 |
| `tilusion/reading_schema.py` | — | — | — | +3 |
| `tilusion/book_digest.py` | — | — | +50 | — |
| `tilusion/overview.py` | — | — | +30 | — |
| `tilusion/prompts/prompt_per_segment_extraction_v0.2.md` | — | +15 | +15 | +20 |
| `tilusion/prompts/prompt_concept_resolution_v0.2.md` | +10 | — | — | — |
| Tests (new) | ~120 | ~100 | ~100 | ~80 |

## Verification (End-to-End)

After each phase:

```
# Unit tests for the phase
~/.virtualenvs/shredder/bin/python -m pytest tests/test_registry_index.py tests/test_book_registry.py tests/test_registry_delta.py tests/test_reading_schema.py tests/test_reading_pipeline.py tests/test_book_digest.py -q

# Existing tests must not regress
~/.virtualenvs/shredder/bin/python -m pytest tests/test_cross_unit_resolution.py tests/test_reading_validation.py -q
```

After Phase 3, a full `scope=book` pipeline run with `MockReadingBackend`
must complete without errors for ≥2 units, and the `known_in_registry`
flagging must be visible in unit metrics.
