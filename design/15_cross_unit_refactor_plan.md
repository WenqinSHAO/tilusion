# Cross-Unit Entity Consistency: Unified Refactoring Plan

Status: **Part 1 done, Part 2 implemented and hardened, Part 3 starting**.
Foundation phases (1, 1.5, 2a, 2b) are done. Phase 2c contract
infrastructure is implemented. Recent hardening fixed repair-loop
normalization, prompt/code cooperation around known registry concepts, and
unsafe alias-only registry dedup. The project is now entering iterative
quality improvement.

This is the canonical plan. It incorporates findings from:
- `16_extraction_quality_audit.md` — 10 quality problems (v0.2 run)
- `17_prompt_pipeline_audit.md` — root cause analysis of all pipeline prompts
- `18_prompt_simplification.md` — field-language policy and prompt simplification
- `19_phase_2c_contract_api.md` — Phase 2c Python API design
- `20_v0.3_extraction_analysis.md` — quality analysis of the v0.3 run (issue catalog #1)
- `21_v0.3_unit5_analysis.md` — unit-0005 merge-corruption analysis and hardening verification
- `22_registry_merge_contract.md` — current registry merge/facet/soft-type contract
- `14_cross_chunk_entity_consistency_analysis.md` — original problem analysis

## Structure

The plan has three parts:

1. **Foundation** — Caching, candidate selection, quality visibility, prompt refresh.
   These are done.

2. **Infrastructure** — Phase 2c: code-owned prompt/data-model contracts.
   This backbone infrastructure is implemented. It changed how prompts are
   composed and how types/language policy are maintained. Subsequent quality
   work now builds on it instead of copy-editing multiple prompt files.

3. **Iterative quality improvement** — One ongoing phase replacing the old
   Phases 3–6. Issues accumulate from LLM-backed test runs into catalogs
   (e.g., `20_v0.3_extraction_analysis.md`, `21_v0.3_unit5_analysis.md`).
   Each round of fixes combines prompt and code changes: type definitions,
   merge heuristics, facet semantics, repair/retry policy, timeline grouping,
   known/new hints, higher-order references. Targeted, auditable commits — not
   big-bang phases.

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

## Part 2: Infrastructure — IMPLEMENTED

### Phase 2c: Prompt/Data-Model Contract Refactor — DONE

**Status**: implemented and hardened.

**Motivation**: This is backbone infrastructure, like the multi-round agentic
loop and the book registry. Five v0.3 prompt files hand-maintain the same
metadata in prose: field-language policy, type vocabularies, input field
descriptions, output schema examples. A type change requires editing 5 prompt
files + `reading_schema.py` + tests. The contract refactor makes the data
model code-owned and prompts generated from it.

**What it changes**: How prompts are composed and how types/language policy
are maintained. It does NOT by itself solve extraction quality; it makes
future quality changes cheaper, more local, and safer.

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

**Acceptance status**:
- One source of truth renders concept/item/group/edge vocabularies into prompts.
- Language policy field roles are declared in metadata and reused by prompts.
- Type vocabulary changes are concentrated in code/config plus targeted tests.
- Contract rendering is covered by prompt-contract tests.
- The current full suite is 462 tests passing after repair/dedup hardening.

**Follow-on hardening already done**:
- Output fields are included in pass contracts, so generated language policy
  covers fields produced by the LLM, not only fields in the input payload.
- Cross-category type warnings are deterministically normalized before a pass
  is accepted.
- Repair propagation now copies fixed validation-subject fields back into the
  returned LLM data.
- `known_concepts` in per-segment extraction is documented as merge hints, not
  evidence and not valid local `concept_refs`.
- Registry dedup ignores generic alias-only identity signals and logs skipped
  merge attempts instead of swallowing them silently.

**Files**: `tilusion/prompt_contracts.py`, `tilusion/reading_schema.py`,
`tilusion/reading_prompts.py`, `tilusion/prompts/*.md`,
`tests/test_prompt_contracts.py`, plus hardening tests in repair, prompt, and
registry suites.

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
phases — each round may touch multiple dimensions. The first priority is to
make merge behavior observable and contract-driven before making larger prompt
or soft-type changes.

**Current status across all dimensions:**

| Dimension | Status | Next action |
|-----------|--------|-------------|
| Type definitions | Done | — |
| Merge heuristics + facets | Observability + facet weighting implemented | **→ Next: LLM-run audit** |
| Repair/retry | Done | Tune thresholds from run data |
| Timeline/grouping | Not started | After merge observability |
| Known/new hints | Done | — |
| Higher-order refs | Deferred | After grouping |

**Type definitions and vocabulary.** DONE — method type restored, time_anchor
cname guidance, cross-category anti-examples, type_vocabularies.json
externalized, cross-category auto-fix in repair loop.

**Merge heuristics, facets, and soft typing.** Core identity-gated merge is
done: generic identity forms filtered, facet-based soft typing active,
first-write-wins surface preservation, deterministic dedup with alias safety.
Part 3 observability is also implemented: accepted/rejected merge counters,
dedup stats, soft-type bridge traces, hard-boundary enforcement before facet
bridging, and applied-only LLM-link counts. The merge contract is documented
in `22_registry_merge_contract.md`.

**Repair/retry policy.** DONE — cross-category type warnings auto-fixed
deterministically, repair propagation direction fixed, auto-fixable issues
applied in all repair paths including full retry fallback.

**Timeline and grouping.** NOT STARTED. Group resolution should prefer
`continue` on existing timelines when new temporal_sequences share key
entities. Cross-group edges (`part_of`, `precedes`) should be proposed when
merging isn't appropriate. Deterministic concept-overlap pre-check before
LLM resolution. Depends on clean concept identity — merge observability
must come first.

**Known/new hints.** DONE — `known_concepts_for_blocks()` passes block-overlap
registry concepts to per-segment extraction context. The extractor sees what's
already known. Further work (explicit `known_in_registry` labels) deferred.

**Higher-order references.** DEFERRED. Concepts that refer to items/events/
groups need first-class representation. Detection first, metrics second,
resolution deferred. Low priority until merge and grouping quality stabilizes.

---

### Part 3 current move: Merge Observability + Facet Overlap Weighting

**Joint rationale.** The merge contract (`22_registry_merge_contract.md`)
correctly prescribes observability before further heuristic changes. The
timeline/grouping dimension depends on clean concept identity, which merge
observability directly verifies. Facet overlap weighting closes the
remaining gap in soft typing: binary facet intersection is too permissive
(class-only overlaps like `person` + `person` should not bridge types).

Do these together: they share code paths in `book_registry.py` and the
observability counters make facet weighting auditable from day one.

#### A. Merge observability

Add a structured merge summary to `registry_delta.py` / `book_registry.py`,
logged to stderr and persisted in run metadata. Counters by reason:

| Category | Reason | What to count |
|----------|--------|---------------|
| Accepted | `same_surface` | Merges where all members share one surface |
| Accepted | `shared_canonical_name` | All members share a canonical_name |
| Accepted | `usable_alias_overlap` | Non-generic alias overlap + same type |
| Accepted | `soft_type_facet_bridge` | Different types, facet overlap bridged |
| Accepted | `llm_link_proposal` | LLM resolver proposed `link` |
| Rejected | `no_identity_signal` | No surface/cname/alias overlap |
| Rejected | `hard_boundary_type` | place/time_anchor/source type mismatch |
| Rejected | `generic_alias_only` | Only generic forms overlap (余, 吾, 先生...) |
| Rejected | `type_mismatch_no_facets` | Different types, no facet overlap |
| Rejected | `type_mismatch_generic_facets_only` | Different types, only class-only facet overlap |

Also track:
- `dedup_candidates_found` / `dedup_accepted` / `dedup_rejected` — from
  `find_registry_duplicates`
- `soft_type_bridge_pairs` — list of (type_a, type_b, shared_facets) for
  each soft-type merge
- `split_candidates` — concepts with same/similar canonical forms still
  split after resolution

Implementation status:
- `tilusion/book_registry.py`: `MergeStats` dataclass, counters at merge/reject
  decision points, and specific-facet soft bridge traces
- `tilusion/registry_delta.py` / `tilusion/reading_pipeline.py`: collect, log,
  and persist merge metrics in package metrics
- LLM-link counts are applied-only, not proposal-only

#### B. Facet overlap weighting

Replace binary `_facets_overlap()` with a weighted check that ignores
class-only overlaps for soft-type bridging:

```python
# Facets that are merely type-class labels — too generic to bridge types
_GENERIC_FACETS: frozenset[str] = frozenset({
    "person", "place", "object", "term", "theme", "motif",
    "method", "time_anchor", "source", "event", "other",
})

def _facets_overlap(members, *, require_specific: bool = False) -> bool:
    """True if any two members share at least one meaningful facet."""
    ...
```

Two modes:
- `require_specific=False` — current behavior (any overlap, used for
  same-type merge confirmation)
- `require_specific=True` — only non-generic overlaps count (used for
  soft-type bridging across different types)

The soft-type path in `_check_merge_boundary` calls with
`require_specific=True`. The same-type path is unchanged.

Implementation status: done in `book_registry.py`; soft-type bridges require
non-generic shared facets, while same-type merge behavior remains unchanged.

#### C. Connection to timeline/grouping (plan, not implement)

After merge observability and facet weighting are in place:
1. Re-run units 2-4, inspect merge summaries
2. If concept identity is clean (low rejection rate, meaningful facet
   bridges), proceed to timeline/grouping improvements
3. If concept identity still has issues, fix those first — grouping
   quality depends on resolved concepts

The grouping improvements themselves will be:
- Deterministic concept-overlap pre-check: temporal_sequences sharing
  ≥30% of concept_refs with a timeline → surface as high-priority
  `continue` candidates
- Group resolution prompt: prefer `continue` over `new_thread` when
  concept overlap exists
- Group resolution prompt: post-placement scan for adjacent temporal
  sequences → propose `merge_groups` or `part_of` edges

#### Implementation order

| Step | What | Est. lines | Depends on |
|------|------|------------|------------|
| 1 | Merge observability counters | Done | — |
| 2 | Facet overlap weighting | Done | — |
| 3 | Re-run + audit merge summaries | Next | 1, 2 |
| 4 | Timeline/grouping improvements | ~40 | 3 (clean identity verified) |

Steps 1 and 2 are independent and can ship together. Step 4 is gated on
step 3 results.

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

After Phase 2c and recent hardening: full suite is 462 tests passing; new
contract, repair-loop, prompt, and registry tests cover rendering, warning
normalization, known-concept hint wording, and generic alias merge safety.

After each quality iteration: re-run LLM-backed extraction, compare metrics
against the previous catalog, verify fixed issues are resolved and no
regressions appear.
