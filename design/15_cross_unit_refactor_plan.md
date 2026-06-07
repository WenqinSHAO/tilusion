# Cross-Unit Entity Consistency: Unified Refactoring Plan

Status: **plan + partial implementation** — Phase 1, Phase 1.5, Phase 2a,
and Phase 2b are done. Phase 2c (prompt/data-model contract refactor) is the
next planning/implementation step before Phase 3.

This is the canonical plan. It incorporates findings from:
- `16_extraction_quality_audit.md` — 10 quality problems found in units 2–4
- `17_prompt_pipeline_audit.md` — root cause analysis of all pipeline prompts
- `18_prompt_simplification.md` — field-language policy and prompt simplification
- `14_cross_chunk_entity_consistency_analysis.md` — original problem analysis

## Overview

| # | Phase | Est. scope | Depends on | Status |
|---|-------|------------|------------|--------|
| 1 | Embedding cache | ~250 lines | None | Done |
| 1.5 | Per-concept candidate maps | ~180 lines | Phase 1 | Done |
| **2a** | **Quality metrics scaffolding** | ~60 lines | None | Done |
| **2b** | **Prompt refresh v0.3 + field-language policy** | ~260 lines | Phase 2a | Done |
| **2c** | **Prompt/data-model contract refactor** | ~300-500 lines | Phase 2b | **Before Phase 3** |
| 3 | Repair/retry policy from quality metrics | ~40 lines | Phase 2c + run data | — |
| 4 | Soft typing (identity-gated facets) | ~400 lines | Phase 2c | — |
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

## Phase 2a: Quality Metrics Scaffolding

**Motivation**: Before changing prompts again, add cheap non-fatal
metrics so the next LLM-backed run can show which failures remain. These
metrics should not trigger repairs yet; they are visibility scaffolding.

Add the Phase 3 validators first, but initially run them as warnings only:
source-grounded field checks, reader-facing language checks, normalized
facet checks, canonical-name coverage by type, non-standard type counts,
and group granularity metrics.

**Files**: `tilusion/reading_pipeline.py` (+60), pass/run metrics helpers as
needed.

---

## Phase 2b: Prompt Refresh v0.3 + Field-Language Policy

**Motivation**: The extraction quality audit found that current prompts
have three structural problems:

1. **~48% of content is overhead the LLM doesn't need.** Hierarchy
   explanations, bloated schema examples (42 lines of empty JSON), repeated
   edge-case rules. This dilutes attention on binding constraints.

Implementation note: hierarchy is useful only when it defines the current pass boundary and explains why inputs are already resolved/merged. Full pipeline architecture should stay out of extraction prompts because it consumes attention without improving local extraction decisions.

Schema-contract note: recent structured-output practice points toward code-owned input/output schemas with compact field descriptions, provider structured-output enforcement where available, and local validation/repair for portability. The next prompt-system cleanup should generate or render prompt contracts from the same Python schema/validator metadata instead of maintaining hand-written schema prose in every prompt.

2. **The language constraint is underspecified.** The current prompt says
   "write in the source language", but extraction output has three different
   kinds of text fields: source-grounded identity fields, reader-facing prose
   fields, and pipeline-normalized internal fields. Treating all of them as
   one language category causes both English translations of Chinese surfaces
   and weak machine signals for merge logic.

3. **The same constraint is missing from the overview segmentation prompt,**
   which feeds `extraction_hints` to the extractor. Hints are reader-facing
   prose and should follow an explicit `reader_language`, defaulting to
   Simplified Chinese for the current app.

### 2b-1. Strip structural overhead

Each prompt reduced to only what the LLM needs for its specific task:

| Section | Current | Target | Rationale |
|---------|---------|--------|-----------|
| Language constraint | 3 lines (top) | Field-language policy near schema | See 2b |
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
- Do not invent block IDs; use only IDs supplied in the segment payload
- Do not merge distinct time expressions, places, or people

**Content to drop:**
- Pipeline architecture diagram (belongs in design docs)
- "Later passes will..." (irrelevant to this pass's task)
- "The caller provides JSON with..." field-by-field descriptions (the JSON
  itself is enough)
- "Use simple IDs like concept-0001" (model does this naturally)
- Empty schema examples that teach the model to output empty values

### 2b-2. Field-language policy, not language-specific prompt files

Keep one compact prompt template. Add explicit language roles to the payload:

- `source_language`: detected or caller-provided language of the source text.
- `reader_language`: preferred language for reader-facing prose; default
  `zh-Hans` for the current application.
- `normalized_language`: language or tag policy for internal merge signals;
  default `normalized` (stable controlled tags rather than reader prose).

The prompt must classify output fields into three groups:

| Field class | Examples | Policy |
|-------------|----------|--------|
| Source-grounded identity | `surface`, `canonical_name`, `aliases`, `observed_surfaces`, source text quotes | Copy from or normalize within the original source text. Never translate because of `reader_language`. |
| Reader-facing prose | `summary`, `rationale`, overview `extraction_hints`, group summaries, graph edge notes | Write in `reader_language`; default Simplified Chinese. |
| Pipeline-normalized internals | `concept_type`, `facets`, normalized time anchors, merge/type hints | Use stable controlled vocabulary/tags consistently across the pipeline. Prefer machine usefulness over reader prose. |

This avoids duplicated prompt files that can drift, while still preventing
English translations of source identity fields and allowing the reader UI to
request Chinese, English, or another prose language later.

---

### 2b-3. Type vocabulary consolidation

**Concept types**: Remove non-standard entries (`action`, `activity`,
`statement`, `event`) from the extractor's effective vocabulary. Add
explicit instruction: "Use ONLY the types listed above. If none fit, use
`other`. Do not invent new types."

**Group types**: Keep both `timeline` and `temporal_sequence`, but define
their granularity. Remove `custom` from the allowed list (keep `other` as
the escape hatch). Add type definitions with distinguishing criteria:

| Type | When to use |
|------|------------|
| `timeline` | Coarse unit-level or book-level arc of major happenings; may be composed from multiple local temporal sequences |
| `temporal_sequence` | Local/micro chronological episode or event chain; may later be aggregated into a timeline |
| `theme_set` | Items sharing a theme/motif, no temporal ordering |
| `method_example_set` | Techniques, methods, and their examples |
| `claim_evidence_map` | Claims with supporting/contradicting evidence |
| `contrast_set` | Items presented in explicit contrast |
| `other` | None of the above fit |

Add scale guidance:
- A `temporal_sequence` can be a small coherent episode if it has clear
  local ordering.
- A `timeline` should span at least a substantial unit section, whole unit,
  or cross-unit/book arc and should contain coarser events.
- Adjacent temporal sequences that form a continuous arc should either be
  merged into a larger timeline or connected with `part_of` / `precedes`
  cross-group edges.

### 2b-4. Facet and canonical_name generation

These were originally Phase 3 (soft typing). The prompt instruction to
generate them belongs here — the schema change and merge logic stay in
Phase 4.

**Facets** (new instruction in extraction prompt):
> For each concept, provide 2–5 type-describing phrases at different
> abstraction levels in `facets`. Example: a treaty concept →
> `["treaty", "legal document", "historical event", "agreement"]`. Facets
> supplement `concept_type` — they do not replace it. Use normalized,
> stable tags consistently across the pipeline; do not translate them for
> reader presentation.

**Canonical name** (new instruction + fixed example):
> Stable named or recurring concepts SHOULD have a `canonical_name`. For
> persons, use the full source-text name. For places, use the complete
> source-text place name. For terms, use the normalized source-text form.
> Leave empty when no stable source-text canonical form exists (e.g.,
> unnamed minor characters or one-off objects).

Change the schema example from `"canonical_name": ""` to a populated value
like `"canonical_name": "沈复"` so the model doesn't learn to leave it
empty.

### 2b-5. Anti-examples

Add explicit wrong-value examples near the output schema to prevent common
failure modes:

```
WRONG: "surface": "congee"         (translation, not source text)
RIGHT: "surface": "粥"              (copied from source block text)

WRONG: "summary": "The method of arranging flowers"  (ignores reader_language=zh-Hans)
RIGHT: "summary": "插瓶之法：..."                     (reader-facing prose)

WRONG: "facets": ["插花方法", "花艺"]                 (reader prose, unstable)
RIGHT: "facets": ["method", "flower_arrangement"]    (normalized internal tags)
```

### 2b-6. Overview segmentation prompt fix

Add the field-language policy and ensure `extraction_hints` are written in
`reader_language`. The overview pass primes extraction; English hints should
not appear unless the caller requested English reader-facing prose.

### Prompts affected

| Prompt | Action |
|--------|--------|
| `prompt_per_segment_extraction_v0.2.md` | Replace with one v0.3 template using field-language policy |
| `prompt_unit_grouping_v0.2.md` | Replace with one v0.3 template using field-language policy and group granularity rules |
| `overview_segmentation_v0.2.md` | Add field-language policy + tighten |
| `prompt_concept_resolution_v0.2.md` | Minor: add reclassify scan instruction |
| `prompt_group_resolution_v0.2.md` | Minor: add temporal aggregation / edge guidance |
| `prompt_book_digest_v0.1.md` | Already OK, no change needed |

### Implementation approach

1. Create one compact `prompt_per_segment_extraction_v0.3.md` template.
2. Create one compact `prompt_unit_grouping_v0.3.md` template.
3. Update overview segmentation prompt with `reader_language` hint policy.
4. Update agentic resolution prompts (minor edits for reader-facing rationale
   and normalized internal fields).
5. Add `source_language`, `reader_language`, and `normalized_language` to
   prompt payloads, pass manifests, and cache identity. Defaults:
   `source_language=auto`, `reader_language=zh-Hans`,
   `normalized_language=normalized`.
6. Update `reading_prompts.py` composition builders to inject the
   field-language policy and language parameters.
7. Wire into `run_reading_pipeline()`.
8. Validate: re-run unit-0003 with v0.3 prompts and compare metrics.

**Files**: `tilusion/prompts/` (+2 new files, ~4 modified),
`tilusion/reading_prompts.py` (+50), `tilusion/reading_pipeline.py` (+25),
cache/run manifest helpers as needed.

### Validation criteria

Run unit-0003 with v0.3 against the same source text and
`reader_language=zh-Hans`:
- Reader-facing English concept summaries: target 0 (was 79)
- Reader-facing English group summaries: target 0 (was 15)
- Translated/non-source surfaces: target 0 (was 19)
- Non-standard concept types: target 0 (was 25)
- Canonical name coverage for stable named/recurring concepts: target >90%
- Normalized facet coverage for merge-relevant concepts: target >90%
- Non-standard group types: target 0 (was 3)

---

## Phase 2c: Prompt/Data-Model Contract Refactor — BEFORE PHASE 3

**Motivation**: Phase 2b exposed a deeper architecture issue. Prompt text,
Python schema objects, validators, quality metrics, deterministic merge
safety, and tests all repeat pieces of the same data model. A small type
change such as narrowing concept/item vocabularies should not require
manual prompt rewrites plus scattered test/code edits. Before using quality
metrics to drive more repairs, make the prompt/data contract explicit and
composable.

This phase is not a large semantic change to extraction behavior. It is a
base-layer refactor that should make future semantic changes cheaper and
safer.

### 2c-1. Code-owned data model contracts

Create a small contract module that owns the prompt-facing data interface:

- pass input fields, output fields, required keys, and copy-through rules;
- field role metadata: `source_identity`, `reader_prose`,
  `normalized_internal`, `id_ref`, `provenance`;
- enum/type registries for concepts, items, groups, and edges;
- per-pass allowed/preferred type subsets;
- validator hints and repair labels tied to fields.

Prompt text should render compact input/output contract sections from this
metadata instead of hand-maintaining JSON prose in every prompt. Tests should
assert the contract metadata and rendered prompt snippets, not duplicate the
full wording.

Target effect: changing an allowed type or field-language role should touch
the contract registry and a small number of focused tests, not every prompt
and validator by hand.

### 2c-2. Composable prompt sections

Split prompts into reusable structured parts:

| Section | Reused by | Purpose |
|---------|-----------|---------|
| `overall_task` | pass-specific | One concise sentence defining the job boundary |
| `language_policy` | all passes | Field-role language rules |
| `data_interface` | all passes | Rendered input/output contract from code metadata |
| `tool_protocol` | agentic passes | Tool-call turn format and completion semantics |
| `scope_guidance` | book/unit sensitive passes | Clarify unit-scope vs book-scope behavior |
| `binding_rules` | extraction/grouping | Evidence, ID, and reference constraints |
| `semantic_guidance` | pluggable | Timeline, temporal sequence, argument graph, method/example guidance |

The prompt builder should compose these sections so shared behavior cannot
drift across overview, extraction, grouping, and registry-resolution prompts.

### 2c-3. Extensible type and guidance registries

Separate structurally special core types from customizable extraction types.

Core structural types should remain first-class in code because validators
and merge safety rely on them:

- temporal: `time_anchor`, `timeline`, `temporal_sequence`;
- identity safety: enough entity categories to protect person/place/source
  merges;
- graph/ref plumbing: IDs, item refs, group refs, provenance.

Everything else should move toward a registry/config shape:

```json
{
  "concept_types": {
    "person": {"core": true, "identity_bearing": true, "prompt_preferred": true},
    "method": {"core": false, "identity_bearing": false, "prompt_preferred": true},
    "other": {"escape_hatch": true}
  },
  "aliases": {"time_expression": "time_anchor", "work": "source"},
  "item_types": {
    "event": {"temporal_candidate": true, "prompt_preferred": true},
    "argument": {"argument_graph_candidate": true, "prompt_preferred": true}
  },
  "guidance_plugins": ["temporal", "argumentation", "method_example"]
}
```

The registry should generate:

- prompt enum lists and preferred subsets;
- normalization maps;
- non-standard-type quality metrics;
- merge-safety policy lookups;
- reader-facing labels/descriptions where needed.

### 2c-4. Pluggable semantic guidance

Keep concept and item types concise and general, then improve extraction
quality through pluggable guidance modules instead of ever-growing enum
lists. Start with three guidance modules:

1. **Temporal guidance**: time expressions, event ordering, local
   `temporal_sequence`, larger `timeline`, aggregation via `part_of` /
   `precedes`, and uncertainty when time is implicit.
2. **Argumentation guidance**: claim/argument/statement/evidence graph
   formation, support/contradict/qualify edges, and discourse flow.
3. **Method/example guidance**: method, technique, example, result,
   limitation relationships.

Each module should expose prompt guidance, allowed graph/group type hints,
and validator/metric checks. This lets us gradually improve new graph types
without rewriting the whole prompt.

### 2c-5. Acceptance criteria

- One source of truth renders concept/item/group/edge vocabularies into
  prompts.
- Language policy field roles are declared in metadata and reused by every
  prompt.
- Unit-scope vs book-scope behavior is explicit in reusable scope guidance.
- Adding a non-core type requires editing the type registry plus targeted
  tests only.
- Temporal and argumentation guidance can be strengthened as plugins without
  changing unrelated prompt sections.
- Existing tests pass and prompt snapshot/string tests avoid brittle prose
  duplication.


## Phase 3: Repair/Retry Policy from Quality Metrics

**Motivation**: Phase 2a adds non-fatal metrics. After one or more clean
LLM-backed runs, use the observed distributions to decide which failures are
severe enough to trigger repair or retry. This avoids expensive retry loops
based on uncalibrated heuristics.

### 3a. Field-language validators

Calibrate the Phase 2a field-language metrics and decide which checks become fatal or repair-triggering:

- Source-grounded fields: warn when `surface` / `observed_surfaces` do not
  appear in cited source block text; warn when `canonical_name` / aliases
  look translated rather than source-normalized.
- Reader-facing fields: if `reader_language == "zh-Hans"`, warn when
  summaries/rationales/edge notes are predominantly ASCII prose.
- Pipeline-normalized fields: warn when facets are empty, prose-like, or
  inconsistent with the normalized tag policy.
- Repair/retry only for severe cases, such as translated source-grounded
  surfaces or invalid source refs.
- Keep weaker signals, such as sparse facets, as warnings until thresholds
  are proven stable.

### 3b. Canonical name coverage metric

Use the Phase 2a canonical-name coverage metric to set type-specific thresholds. Do not use one global threshold for all concepts.

### 3c. Type vocabulary check

Promote non-standard concept/group types to repair candidates once the v0.3 prompt has had a chance to comply.

### 3d. Group granularity metrics

Use timeline/temporal-sequence span metrics to decide whether group resolution should merge groups, add `part_of` edges, or leave local episodes separate.

**Files**: `tilusion/reading_pipeline.py` (+40), repair/retry helpers as needed

---

## Phase 4: Soft Typing (Identity-Gated Type Facets)

**Motivation**: (unchanged from original plan) `TYPE_FAMILIES` has known
gaps. Facet set intersection handles cross-type identity without manual
ontology maintenance. Identity-gated: facet overlap relaxes type mismatch
only after a name/surface/alias identity signal exists.

Phase 2b ensures facets are generated. Phase 4 makes them mechanically useful.

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

| File | Phase 2a | Phase 2b | Phase 3 | Phase 4 | Phase 5 | Phase 6 |
|------|----------|----------|---------|---------|---------|---------|
| `tilusion/prompts/prompt_per_segment_extraction_v0.3.md` | — | **new** | — | — | — | +20 |
| `tilusion/prompts/prompt_unit_grouping_v0.3.md` | — | **new** | — | — | — | — |
| `tilusion/prompts/overview_segmentation_v0.2.md` | — | ~10 | — | — | — | — |
| `tilusion/prompts/prompt_concept_resolution_v0.2.md` | — | ~5 | — | — | — | — |
| `tilusion/prompts/prompt_group_resolution_v0.2.md` | — | ~5 | — | — | — | — |
| `tilusion/reading_prompts.py` | — | +50 | — | — | — | — |
| `tilusion/reading_pipeline.py` | +60 | +25 | +40 | — | +70 | +30 |
| `tilusion/registry_index.py` | — | — | — | +30, ~20 | +50 | — |
| `tilusion/book_registry.py` | — | — | — | +15 | — | — |
| `tilusion/reading_schema.py` | — | — | — | — | — | +3 |
| `tilusion/book_digest.py` | — | — | — | — | +50 | — |
| `tilusion/overview.py` | — | — | — | — | +30 | — |
| `tilusion/registry_delta.py` | — | — | — | — | +15 | — |

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

After Phase 2a: run existing cached or fixture data through metrics and verify the logs/manifests expose field-language, type, canonical-name, facet, and group-granularity counts.

After Phase 2b: re-run unit-0003 with v0.3 prompts and `reader_language=zh-Hans`; verify zero reader-facing English output, zero translated/non-source surfaces, and high canonical/facet coverage for eligible concepts.

After Phase 5: full `scope=book` pipeline run with ≥2 units, `known_in_registry`
flagging visible in unit metrics.
