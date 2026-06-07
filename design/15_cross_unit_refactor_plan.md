# Cross-Unit Entity Consistency: Unified Refactoring Plan

Status: **plan + partial implementation** — Foundation phases (1, 1.5, 2a, 2b)
are done. Phase 2c (contract refactor) is the next infrastructure milestone.
Iterative quality improvement follows.

This is the canonical plan. It incorporates findings from:
- `16_extraction_quality_audit.md` — 10 quality problems (v0.2 run)
- `17_prompt_pipeline_audit.md` — root cause analysis of all pipeline prompts
- `18_prompt_simplification.md` — field-language policy and prompt simplification
- `19_phase_2c_contract_api.md` — Phase 2c Python API design
- `20_v0.3_extraction_analysis.md` — quality analysis of the v0.3 run (issue catalog #1)
- `14_cross_chunk_entity_consistency_analysis.md` — original problem analysis

## Structure

The plan has three parts:

1. **Foundation** — Caching, candidate selection, quality visibility, prompt refresh.
   These are done.

2. **Infrastructure** — Phase 2c: code-owned prompt/data-model contracts.
   This is backbone infrastructure, like the multi-round agentic loop and book
   registry. It changes how prompts are composed and how types/language policy
   are maintained. Once in place, all subsequent quality work builds on it.

3. **Iterative quality improvement** — One ongoing phase replacing the old
   Phases 3–6. Issues accumulate from LLM-backed test runs into a catalog
   (e.g., `20_v0.3_extraction_analysis.md`). Each round of fixes combines
   prompt and code changes: type definitions, merge heuristics, repair/retry
   policy, timeline grouping, known/new hints, higher-order references.
   Targeted, auditable commits — not big-bang phases.

---

## Part 1: Foundation — DONE

| # | Phase | Est. scope | Status |
|---|-------|------------|--------|
| 1 | Embedding cache | ~250 lines | Done |
| 1.5 | Per-concept candidate maps | ~180 lines | Done |
| 2a | Quality metrics scaffolding | ~60 lines | Done |
| 2b | Prompt refresh v0.3 + field-language policy | ~260 lines | Done |

### Phase 1: Embedding Cache

Text-hash-addressed two-layer (memory + disk) embedding cache in
`registry_index.py`. Eliminates ~11,200s of redundant embedding
recomputation per unit. Details in commit history.

**Key decisions**: `sha256(searchable_text)` keys (not concept_id+hash),
BM25 retained as secondary lexical signal, no vector DB needed.

### Phase 1.5: Per-Concept Candidate Maps

`candidate_map` in LLM payload gives the agent per-unit-concept local
candidate sets instead of a flat registry union. Per-concept caps: 5
embedding-signal + 3 BM25-only. `candidate_selection_warning` printed to
stderr when ≥80% of registry is selected.

### Phase 2a: Quality Metrics Scaffolding

Non-fatal visibility metrics in `tilusion/reading_quality.py` (356 lines):
source-surface grounding, reader-language issues, non-standard type counts,
canonical-name coverage by type, facet coverage, group granularity.
Human-readable merge/proposal logging in pipeline stderr.

**Motivation**: gather run data before wiring repair triggers.

### Phase 2b: Prompt Refresh v0.3 + Field-Language Policy

All five prompts rewritten with:
- **Field-language policy**: three field roles — source-grounded identity
  (surface, canonical_name, observed_surfaces), reader-facing prose (summary,
  rationale, warnings), pipeline-normalized internals (concept_type, facets,
  IDs). One policy section, consistent across all prompts.
- **Stripped overhead**: hierarchy explanations removed, schema examples
  populated (not empty JSON), anti-examples added.
- **Type vocabulary consolidation**: timeline vs temporal_sequence now
  distinct granularities (coarse arc vs local episode). Preferred + extended
  tier system. Narrowed for novels/essays: 7 preferred concept types, 9
  preferred item types, 8 group types.
- **Facet and canonical_name generation**: instructions + populated schema
  examples.
- `reader_language` parameter (default `zh-Hans`) wired through CLI,
  payloads, and cache identity.

**Validation results** (units 2–4, 浮生六记): zero English output, 100%
facet coverage, 76% canonical name coverage, only 3 non-standard concept
types (was 25), only 3 group type values (was 7).

---

## Part 2: Infrastructure

### Phase 2c: Prompt/Data-Model Contract Refactor — NEXT

**Status**: designed (`19_phase_2c_contract_api.md`), not implemented.

**Motivation**: This is backbone infrastructure, like the multi-round agentic
loop and the book registry. Five v0.3 prompt files hand-maintain the same
metadata in prose: field-language policy, type vocabularies, input field
descriptions, output schema examples. A type change requires editing 5 prompt
files + `reading_schema.py` + tests. The contract refactor makes the data
model code-owned and prompts generated from it.

**What it changes**: How prompts are composed and how types/language policy
are maintained. It does NOT change extraction behavior — it makes future
changes cheaper and safer.

**API** (detailed in `19_phase_2c_contract_api.md`):
- `FieldRole` enum + `FieldMeta` dataclass — owns field-level language policy
- `PassContract` — renders language_policy, input_contract, output_schema,
  type_vocabulary sections from metadata
- `TypeVocabulary` — preferred subsets, type definitions, prompt list rendering
- Domain registries as frozen dataclass constants (narrative, essay, technical)
- Existing `PromptComposition` machinery reused; contracts rendered as
  `generated_prompt_part` entries

**Target effect**: Adding a type definition requires editing one Python
constant; narrowing types for a domain requires passing a different
`TypeVocabulary`, not editing prompt files; field-language policy is rendered
from code, not copy-pasted across 5 files.

**Acceptance criteria** (from `19_phase_2c_contract_api.md`):
- One source of truth renders concept/item/group/edge vocabularies into prompts
- Language policy field roles declared in metadata, reused by every prompt
- Adding a non-core type requires editing the type registry + targeted tests only
- Existing 423 tests pass; new contract tests cover rendering and domain subsets

**Files**: `tilusion/prompt_contracts.py` (new, ~150), `tilusion/reading_schema.py`
(+10), `tilusion/reading_prompts.py` (~40, ~20), `tilusion/prompts/*.md`
(~50 removed per file), `tests/test_prompt_contracts.py` (new, ~80).

---

## Part 3: Iterative Quality Improvement

**Replaces old Phases 3–6.** The old plan had sequential phases for repair/retry,
soft typing, richer hints, and higher-order references — each with separate
prompt and code changes. In practice, quality issues are discovered by running
the pipeline and inspecting results. Each round of fixes touches prompts AND
code together: a type definition fix in a prompt may need a corresponding
validator change; a merge heuristic fix is purely code; a grouping fix may
need both prompt guidance and resolution logic.

### How it works

1. **Run** the pipeline on real text.
2. **Analyze** results, classify issues (prompt-only, code-only, both).
3. **Catalog** findings in a design doc (e.g., `20_v0.3_extraction_analysis.md`).
4. **Fix** a batch of related issues in targeted commits.
5. **Re-run** and verify.

Issue catalogs are numbered sequentially and reference this plan.

### Issue catalog #1: v0.3 extraction run (units 2–4, 浮生六记)

Documented in `design/20_v0.3_extraction_analysis.md`. Summary of open issues:

| Priority | Issue | Type | Where |
|----------|-------|------|-------|
| P0 | 沈复/沈三白, 芸娘 duplicates not merged | Merge heuristic | registry_delta, resolver prompt |
| P0 | 7 temporal_sequences not continued into timeline | Group resolution | group resolver prompt + pre-check |
| P1 | `source` type underused | Type definition | extraction prompt |
| P1 | Supernatural entities as `person` | Type definition | extraction prompt |
| P1 | Animals/plants as `other` | Type definition | extraction prompt |
| P1 | Facet verbosity | Prompt tightening | extraction prompt |
| P2 | Theme sets = editorial commentary | Item type scope | extraction prompt (maybe add type) |
| P2 | concept-0235 (憨) split from 憨园 | Extraction + merge | both |
| P3 | Source block click not working | Frontend | HTML template |

### Quality dimensions (ongoing)

These are the areas iterative improvement targets. They are not sequential
phases — each round may touch multiple dimensions.

**Type definitions and vocabulary.** Add missing type definitions, tighten
boundaries between similar types (method vs technique, person vs term for
supernatural entities), ensure anti-examples cover common miscategorizations.
Mostly prompt changes; Phase 2c makes them single-Python-constant edits.

**Merge heuristics.** Deterministic pre-checks before LLM resolution: same
surface + same type → auto-link; alias overlap → high-priority candidate;
single-char surface that is a prefix of an existing concept → flag. Code
changes in `registry_delta.py` and `registry_index.py`, with resolver
prompt updates for the cases that still need LLM judgment.

**Repair/retry policy.** After Phase 2c, use quality metrics from real runs
to calibrate which failures trigger repair. Source-grounded field violations
→ fatal; reader-language issues → warn (currently zero); sparse facets →
warn. Code changes in `reading_pipeline.py`.

**Timeline and grouping.** Group resolution should prefer `continue` on
existing timelines when new temporal_sequences share key entities.
Cross-group edges (`part_of`, `precedes`) should be proposed when merging isn't
appropriate. Deterministic concept-overlap pre-check before LLM resolution.
Prompt + code changes in group resolver and `registry_delta.py`.

**Known/new hints.** Per-segment known-concept lookup (reverse index
block_id → concept_id, normalized lexical matching), enriched segment
context, known_in_registry flagging. Primarily code changes in
`registry_index.py`, `overview.py`, `reading_pipeline.py`.

**Higher-order references.** Concepts that refer to items/events/groups
need first-class representation (optional `refers_to` field). Detection
first, metrics second, resolution deferred. Prompt + schema changes.

---

## Verification

After each batch of changes:

```
~/.virtualenvs/shredder/bin/python -m pytest \
  tests/test_book_registry.py tests/test_registry_delta.py \
  tests/test_reading_schema.py tests/test_reading_pipeline.py \
  tests/test_book_digest.py tests/test_cross_unit_resolution.py \
  tests/test_reading_validation.py tests/test_reading_payloads_prompts.py -q
```

After Phase 2c: existing 423 tests pass; new contract tests cover rendering
and domain subsets.

After each quality iteration: re-run LLM-backed extraction, compare metrics
against the previous catalog, verify fixed issues are resolved and no
regressions appear.
