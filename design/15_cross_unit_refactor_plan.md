# Cross-Unit Entity Consistency: Unified Refactoring Plan

Status: **plan + partial implementation** — Phase 1 (embedding cache) and
Phase 1.5 (candidate maps) are done. Phase 2 (prompt refresh v0.3) is next.

This is the canonical plan. It incorporates findings from:
- `16_extraction_quality_audit.md` — 10 quality problems found in units 2–4
- `17_prompt_pipeline_audit.md` — root cause analysis of all pipeline prompts
- `18_prompt_simplification.md` — language-specific prompt variant design
- `14_cross_chunk_entity_consistency_analysis.md` — original problem analysis

## Overview

| # | Phase | Est. scope | Depends on | Status |
|---|-------|------------|------------|--------|
| 1 | Embedding cache | ~250 lines | None | Done |
| 1.5 | Per-concept candidate maps | ~180 lines | Phase 1 | Done |
| **2** | **Prompt refresh v0.3** | ~300 lines | None | **Next** |
| 3 | Post-extraction validation | ~60 lines | Phase 2 | — |
| 4 | Soft typing (identity-gated facets) | ~400 lines | Phase 2 | — |
| 5 | Richer hints & known/new flagging | ~350 lines | Phase 4 | — |
| 6 | Higher-order reference detection | ~250 lines | Phase 5 | — |

### Why Phase 2 changed

The original plan had soft typing as Phase 2. The extraction quality audit
(units 2–4) showed that **prompt-level issues cause 8 of 10 observed
problems** — language non-compliance, type proliferation, English surfaces,
no facets, missing canonical names, temporal fragmentation. These are
cheaper to fix than soft typing and block it: facets can't be used if the
extractor never generates them. Prompt refresh comes first.

---

## Phase 1: Embedding Cache — DONE

Text-hash-addressed two-layer (memory + disk) embedding cache in
`registry_index.py`. Eliminates ~11,200s of redundant embedding
recomputation per unit. Details in commit history.

**Key decisions**: `sha256(searchable_text)` keys (not concept_id+hash),
BM25 retained as secondary lexical signal, no vector DB needed.

## Phase 1.5: Per-Concept Candidate Maps — DONE

`candidate_map` in LLM payload gives the agent per-unit-concept local
candidate sets instead of a flat registry union. Per-concept caps: 5
embedding-signal + 3 BM25-only. `candidate_selection_warning` printed to
stderr when ≥80% of registry is selected.

---

## Phase 2: Prompt Refresh v0.3

**Motivation**: The extraction quality audit found that current prompts
have three structural problems:

1. **~48% of content is overhead the LLM doesn't need.** Hierarchy
   explanations, bloated schema examples (42 lines of empty JSON), repeated
   edge-case rules. This dilutes attention on binding constraints.

2. **The language constraint is fragile.** An English instruction
   ("CRITICAL — Language: write in the source language") is placed at line
   3 of a 115-line prompt. By the time the model reaches the output schema
   at line 44, the constraint is outside its attention window. Unit-0003
   produced 79 English concepts and 15 English groups from Chinese source
   text because of this.

3. **The same constraint is missing from the overview segmentation prompt,**
   which feeds `extraction_hints` to the extractor. English hints prime
   English extraction output.

### 2a. Strip structural overhead

Each prompt reduced to only what the LLM needs for its specific task:

| Section | Current | Target | Rationale |
|---------|---------|--------|-----------|
| Language constraint | 3 lines (top) | N/A — prompt IS in target language | See 2b |
| Hierarchy explanation | 10 lines | **Removed** | LLM doesn't need pipeline architecture |
| Input field descriptions | 8 lines | 6 lines | Keep, tighten |
| Schema example | 42 lines (empty JSON) | ~12 lines (realistic populated example) | Empty JSON teaches model to output empties |
| Rules | 32 lines | ~18 lines | Drop edge cases the model handles implicitly; keep binding constraints |
| Region guidance | 6 lines | ~3 lines | Useful signal, over-detailed |

**Target extraction prompt: ~55 lines (52% reduction).** Same principle
for grouping, overview, and resolution prompts.

**Binding constraints that MUST stay:**
- `surface` must be copied from source block text (not translated)
- `source_block_refs` must cite provided block IDs
- `observed_surfaces` must list every surface form in the segment
- Items are source-grounded, concepts may be inferred
- `concept_refs` must reference concepts in the same response
- Do not merge distinct time expressions, places, or people

**Content to drop:**
- Pipeline architecture diagram (belongs in design docs)
- "Later passes will..." (irrelevant to this pass's task)
- "The caller provides JSON with..." field-by-field descriptions (the JSON
  itself is enough)
- "Do not invent block IDs" (model does this naturally)
- "Use simple IDs like concept-0001" (model does this naturally)
- Empty schema examples that teach the model to output empty values

### 2b. Language-specific prompt variants

Instead of one prompt with a language-switching instruction, create
per-language variants where the entire system prompt — instructions,
examples, field descriptions, rules — is in the target language.

**Mechanism**: The pipeline detects or receives the book's primary language.
At prompt composition time, the matching variant is loaded:

```
prompt_per_segment_extraction_v0.3_zh.md   ← Chinese books
prompt_per_segment_extraction_v0.3_en.md   ← English books (default)
```

**What changes per language**: All human-language prose. Instructions,
example field values, anti-examples, type definitions.

**What stays language-agnostic**: JSON field names (`surface`,
`concept_type`), type vocabulary (`person`, `place`, `temporal_sequence`),
ID patterns.

**Why this is general**: The mechanism is `source_language → prompt variant`.
Works for any language — Chinese, English, French, Arabic. Not specific to
Chinese classical texts. If a language lacks a dedicated variant, fall back
to the English variant with the current "write in source language"
instruction (graceful degradation).

### 2c. Type vocabulary consolidation

**Concept types**: Remove non-standard entries (`action`, `activity`,
`statement`, `event`) from the extractor's effective vocabulary. Add
explicit instruction: "Use ONLY the types listed above. If none fit, use
`other`. Do not invent new types."

**Group types**: Merge `timeline` and `temporal_sequence` into ONE type
(`temporal_sequence`). Remove `custom` from the allowed list (keep `other`
as the escape hatch). Add type definitions with distinguishing criteria:

| Type | When to use |
|------|------------|
| `temporal_sequence` | Items with chronological order forming a narrative arc |
| `theme_set` | Items sharing a theme/motif, no temporal ordering |
| `method_example_set` | Techniques, methods, and their examples |
| `claim_evidence_map` | Claims with supporting/contradicting evidence |
| `contrast_set` | Items presented in explicit contrast |
| `other` | None of the above fit |

Add scale guidance: "A temporal_sequence should represent a meaningful
narrative arc. Single events or outings with ≤5 items should use a
different group type or be merged into a larger temporal_sequence.
Adjacent temporal_sequences that form a continuous narrative (same key
entities, sequential time periods) should be merged into one."

### 2d. Facet and canonical_name generation

These were originally Phase 3 (soft typing). The prompt instruction to
generate them belongs here — the schema change and merge logic stay in
Phase 4.

**Facets** (new instruction in extraction prompt):
> For each concept, provide 2–5 type-describing phrases at different
> abstraction levels in `facets`. Example: a treaty concept →
> `["treaty", "legal document", "historical event", "agreement"]`. Facets
> supplement `concept_type` — they do not replace it. Use the source
> language.

**Canonical name** (new instruction + fixed example):
> Every concept MUST have a `canonical_name`. For persons, use the full
> standard name. For places, use the complete place name. For terms, use
> the normalized form. Leave empty ONLY when no standardized form exists
> (e.g., unnamed minor characters).

Change the schema example from `"canonical_name": ""` to a populated value
like `"canonical_name": "沈复"` so the model doesn't learn to leave it
empty.

### 2e. Anti-examples

Add explicit wrong-value examples near the output schema to prevent common
failure modes:

```
WRONG: "surface": "congee"         (translation, not source text)
RIGHT: "surface": "粥"              (copied from source block text)

WRONG: "summary": "The method of arranging flowers"  (English for Chinese source)
RIGHT: "summary": "插瓶之法：..."                     (source language)
```

### 2f. Overview segmentation prompt fix

Add the CRITICAL language banner (currently missing). The overview pass
produces `extraction_hints` that prime the extraction LLM — English hints
trigger English extraction.

### Prompts affected

| Prompt | Action |
|--------|--------|
| `prompt_per_segment_extraction_v0.2.md` | Replace with v0.3 zh+en variants |
| `prompt_unit_grouping_v0.2.md` | Replace with v0.3 zh+en variants |
| `overview_segmentation_v0.2.md` | Add language banner + tighten |
| `prompt_concept_resolution_v0.2.md` | Minor: add reclassify scan instruction |
| `prompt_group_resolution_v0.2.md` | Minor: add adjacent-sequence merge guidance |
| `prompt_book_digest_v0.1.md` | Already OK, no change needed |

### Implementation approach

1. Create `prompt_per_segment_extraction_v0.3_zh.md` — canonical tight
   Chinese variant (~55 lines)
2. Create `prompt_per_segment_extraction_v0.3_en.md` — English variant,
   same structure
3. Same for grouping prompt (zh + en)
4. Update overview segmentation prompt
5. Update agentic resolution prompts (minor edits)
6. Add `source_language` parameter to pipeline (detected or caller-specified)
7. Update `reading_prompts.py` — v0.3 composition builders that select
   language-appropriate resource
8. Wire into `run_reading_pipeline()`
9. Validate: re-run unit-0003 with v0.3_zh prompts, compare metrics

**Files**: `tilusion/prompts/` (+6 new files, ~4 modified),
`tilusion/reading_prompts.py` (+40), `tilusion/reading_pipeline.py` (+15)

### Validation criteria

Run unit-0003 with v0.3_zh against the same source text:
- English concept summaries: target 0 (was 79)
- English group summaries: target 0 (was 15)
- English-only surfaces: target 0 (was 19)
- Non-standard concept types: target 0 (was 25)
- Canonical name coverage: target >90% (was 61%)
- Facet coverage: target >90% (was 0%)
- Non-standard group types: target 0 (was 3)

---

## Phase 3: Post-Extraction Validation

**Motivation**: Prompts are instructions, not enforcement. A lightweight
validation layer catches prompt non-compliance before bad data enters the
registry. This is defense-in-depth: Phase 2 prevents most issues, Phase 3
catches the rest.

### 3a. Language validator

After per-segment extraction, check concept summaries and surfaces against
the expected source language:

- If `source_language == "zh"` and >30% of concept summaries have more
  ASCII alpha characters than CJK characters → emit warning, flag segment
  for potential re-extraction.
- If any concept surface is ASCII-only when `source_language == "zh"` and
  the concept's source block text contains CJK characters → emit warning.
- Log summary to stderr: `[seg-N] language check: 3/45 concepts appear
  to be in wrong language`.

### 3b. Canonical name coverage metric

After within-unit merge, report canonical name coverage:
- `canonical_name_coverage: 0.87` (fraction of concepts with non-empty
  canonical_name)
- Log to stderr: `[unit-N] canonical name coverage: 87% (52/60 concepts)`

### 3c. Type vocabulary check

After within-unit merge, flag concepts using types outside the standard
vocabulary. Report count and list the non-standard types found.

**Files**: `tilusion/reading_pipeline.py` (+60)

---

## Phase 4: Soft Typing (Identity-Gated Type Facets)

**Motivation**: (unchanged from original plan) `TYPE_FAMILIES` has known
gaps. Facet set intersection handles cross-type identity without manual
ontology maintenance. Identity-gated: facet overlap relaxes type mismatch
only after a name/surface/alias identity signal exists.

Phase 2 ensures facets are generated. Phase 4 makes them mechanically useful.

### 4a. Deterministic type compatibility via facet intersection

New `_types_compatible()` replacing `_relaxed_types()` when facets exist.
`TYPE_FAMILIES` remains as fallback for facetless legacy concepts.

### 4b. Identity-gated merge boundary

Two separate predicates in `_check_merge_boundary()`:
```python
identity_signal = shared_canonical_name or shared_alias or shared_surface
type_compatible = same_hard_type or shared_type_family or shared_facet
if identity_signal and type_compatible:
    return None  # safe to merge
```

### 4c. Registry index includes facets

Add `"facets"` field to `build_registry_index()` output.

**Files**: `tilusion/registry_index.py` (+30, ~20),
`tilusion/book_registry.py` (+15)

---

## Phase 5: Richer Hints & Known/New Flagging

**Motivation**: (unchanged from original plan) Current per-segment hints
are too light. The extractor can't distinguish "flag this known concept"
from "extract this new concept." Later units should converge toward
consistent types and avoid re-extraction.

### 5a. Per-segment known-concept lookup

New `known_concepts_for_segment()` in `registry_index.py`:
- Reverse index `block_id → {concept_id, ...}` from registry
  `source_block_refs` (for reruns and exact source continuity)
- Normalized lexical matching of canonical names, aliases, and observed
  surfaces against `segment_text` (for cross-unit memory)
- Returns compact entries ranked by evidence strength

### 5b. Enriched segment context

Extend `context` dict passed to extraction LLM with `known_concepts` and
`new_concept_guidance`.

### 5c. Improved book digest

Stratify by concept type, mark high-frequency concepts, add "recently
added" section for concepts from the most recent unit.

### 5d. Known/new flagging

Add `known_in_registry: bool` to unit concept metadata. Skip embedding
search for known concepts in cross-unit merge — go straight to
deterministic merge.

**Files**: `tilusion/registry_index.py` (+50), `tilusion/overview.py` (+30),
`tilusion/reading_pipeline.py` (+70), `tilusion/book_digest.py` (+50),
`tilusion/registry_delta.py` (+15)

---

## Phase 6: Higher-Order Reference Detection

**Motivation**: (unchanged from original plan) Concepts that refer to
items/events/groups have no first-class representation. Detection first,
metrics second, resolution deferred.

### 6a–6c: Detection + schema + metrics

Add optional `refers_to: list[dict]` field to `Concept`. Instruct extractor
to flag when a concept refers to a higher-order structure. Collect metrics
by confidence and target type.

### 6d: Agentic resolution (deferred)

Build only after metrics from 6c confirm the problem is prevalent.

**Files**: `tilusion/prompts/` (+20), `tilusion/reading_schema.py` (+3),
`tilusion/reading_pipeline.py` (+30)

---

## Files Affected (All Phases)

| File | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 |
|------|---------|---------|---------|---------|---------|
| `tilusion/prompts/prompt_per_segment_extraction_v0.3_zh.md` | **new** | — | — | — | +20 |
| `tilusion/prompts/prompt_per_segment_extraction_v0.3_en.md` | **new** | — | — | — | — |
| `tilusion/prompts/prompt_unit_grouping_v0.3_zh.md` | **new** | — | — | — | — |
| `tilusion/prompts/prompt_unit_grouping_v0.3_en.md` | **new** | — | — | — | — |
| `tilusion/prompts/overview_segmentation_v0.2.md` | ~10 | — | — | — | — |
| `tilusion/prompts/prompt_concept_resolution_v0.2.md` | ~5 | — | — | — | — |
| `tilusion/prompts/prompt_group_resolution_v0.2.md` | ~5 | — | — | — | — |
| `tilusion/reading_prompts.py` | +40 | — | — | — | — |
| `tilusion/reading_pipeline.py` | +15 | +60 | — | +70 | +30 |
| `tilusion/registry_index.py` | — | — | +30, ~20 | +50 | — |
| `tilusion/book_registry.py` | — | — | +15 | — | — |
| `tilusion/reading_schema.py` | — | — | — | — | +3 |
| `tilusion/book_digest.py` | — | — | — | +50 | — |
| `tilusion/overview.py` | — | — | — | +30 | — |
| `tilusion/registry_delta.py` | — | — | — | +15 | — |

---

## Verification

After each phase:

```
~/.virtualenvs/shredder/bin/python -m pytest \
  tests/test_book_registry.py tests/test_registry_delta.py \
  tests/test_reading_schema.py tests/test_reading_pipeline.py \
  tests/test_book_digest.py tests/test_cross_unit_resolution.py \
  tests/test_reading_validation.py tests/test_reading_payloads_prompts.py -q
```

After Phase 2: re-run unit-0003 with v0.3_zh prompts, verify zero English
output, >90% canonical name coverage, >90% facet coverage.

After Phase 5: full `scope=book` pipeline run with ≥2 units, `known_in_registry`
flagging visible in unit metrics.
