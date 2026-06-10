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

### Issue catalog #2: DingDing book (non-narrative domain, 2 units)

A business/tech book about DingDing's product development exposed gaps in a
pipeline designed and tested primarily on narrative text (浮生六记).
Registry: 99 concepts, 126 items, 7 groups. Key findings:

| # | Issue | Severity | Root cause |
|---|-------|----------|------------|
| 1 | 25% concepts typed `other` | High | LLM computes facets and concept_type independently; `method` is available but unused |
| 2 | 23% items ungrouped (29/126) | High | Grouping pass omits statement-type items; no flat "loose items" group |
| 3 | LLM reasoning leaked into group summary | Medium | Group resolution prompt doesn't separate summary from rationale |
| 4 | "7 graphs" counted but 4 are empty theme_sets | Low | Frontend counts any graph dict as having a graph |
| 5 | Statement-heavy (52%), sparse events (29%) | Medium | Source text is analytical; overview hints may not distinguish |

**Root cause detail for #1 (other overuse):** Concepts like 优先级算法
have facets `["method", "algorithm", "design"]` but concept_type=`other`.
The LLM correctly identifies the facet-level category (`method`) but doesn't
propagate it to concept_type. The two fields are generated independently
in the extraction prompt — there's no binding rule linking them.

**Root cause detail for #2 (ungrouped items):** 28/29 ungrouped items have
concept_refs. The grouping LLM simply didn't place them. Most are `statement`
type (18) — analytical observations that don't fit neatly into temporal
sequences or theme_sets. The grouping prompt asks the LLM to build groups
from items but doesn't instruct it to handle "everything else."

**Root cause detail for #3 (summary leak):** group-0007 summary reads:
"建议通过cross_group_edge建立关联" — the group resolution LLM wrote its
recommendation into the group summary instead of the `rationale` field.

#### Improvement directions from this catalog

**Direction 1: Concept type from facets.** Add a binding rule in the
extraction prompt: "`concept_type` should be consistent with `facets`.
If facets include `method`, concept_type should be `method`. If facets
include `person`, concept_type should be `person`, etc." This closes the
gap between facet-level understanding and type assignment. Pure prompt
change — ~2 lines.

**Direction 2: Ungrouped item handling.** Two complementary fixes:
- In the grouping prompt: add a rule — "Any item not placed in a group
  should be listed in `unresolved_items` with a reason."
- In the grouping pass code: after the LLM returns groups, detect
  items not referenced by any group and either auto-assign to a
  catch-all group or flag in metrics. Prompt + code — ~15 lines.

**Direction 3: Summary/rationale separation.** In the group resolution
prompt: "Write group `summary` as a standalone description of the group's
content. Put cross-group recommendations in `rationale`, not in `summary`."
Pure prompt change — ~1 line.

**Direction 4: Domain-appropriate type vocabulary.** The narrative
vocabulary (person, place, object, term, source, method) under-serves
analytical/business text. A new domain config in `type_vocabularies.json`
would add `process`, `principle`, `metric` and remove narrative-only
types. But this should follow Direction 1 — if concept_type is derived
from facets, the vocabulary matters less because facets are richer.
Defer until after Direction 1 is validated.

### Quality dimensions (ongoing)

Issues from both catalogs cluster into three work groups. Each group
combines prompt and code changes; they are ordered by impact-to-effort
ratio, not by catalog origin.

#### Work group A: Type accuracy (Dirs 1 + 4, joint)

**Problem across both catalogs:** The LLM computes correct facets but
leaves concept_type=`other` (25% rate on DingDing). Facets like
`["method", "algorithm"]` should imply concept_type=`method`. The two
fields are generated independently — there's no binding rule.

**Fix (Dir 1):** Add a binding rule in the extraction prompt: "`concept_type`
must be consistent with `facets`. If facets include `method`, concept_type
should be `method`. If facets include `person`, concept_type should be
`person`. When facets suggest multiple types, prefer the most specific."
~2 lines in the extraction prompt binding rules.

**Why Dir 4 is folded in:** If concept_type is derived from facets, the
specific concept_type vocabulary matters less — facets are richer. Domain-
specific type configs in `type_vocabularies.json` become a secondary concern
rather than a prerequisite. Defer Dir 4 until Dir 1 is validated on both
narrative and business text.

#### Work group B: Grouping quality (Dir 2 + Dir 3 + catalog #1 P2)

**Problem across both catalogs:** Three grouping issues that share the
same root: the grouping/resolution LLM doesn't have clear rules for
edge cases.
- Dir 2: 23% items ungrouped (DingDing). Statement-type items that don't
  fit temporal sequences or theme_sets are simply omitted.
- Dir 3: LLM reasoning leaks into group.summary — resolution-level
  recommendations ("建议通过cross_group_edge") appear in output fields.
- Catalog #1 P2: All theme_sets are editorial commentary (浮生六记).
  The LLM defaults to theme_set for any reflective/analytical content.

**Fix (single batch, ~20 lines):**
- **Grouping prompt** — new rule: "Every `atomic_item` in the input must
  appear in at least one group's `item_refs` or in `unresolved_items`.
  Do not silently drop items." Plus: "A `theme_set` groups items sharing
  a topic. Editorial commentary and author reflection are valid theme_sets
  only if they share a specific theme — tag them with descriptive summaries."
- **Group resolution prompt** — clarify output field roles: "`summary`
  describes the group's content. Recommendations about cross-group
  relationships go in `rationale`, never in `summary`."
- **Code** — post-grouping check: detect items not referenced by any group
  or unresolved_items, log count as a quality metric, auto-place in a
  generated catch-all `theme_set` with summary "未归类项".

#### Work group C: Timeline structure (catalog #1 P0, previously gated)

**Problem:** Temporal_sequences form around local episodes but are not
continued into existing timelines. Across both catalogs, 7 temporal
sequences exist alongside only 1–2 timelines. The group resolution
pass defaults to `new_thread`.

**Why un-gate now:** Merge infrastructure is mature — identity guards,
generic form filtering, facet-based soft typing, dedup, and
observability are all implemented and verified across multiple runs.
The merge audit was precautionary; the infrastructure has proven itself.

**Fix (~40 lines):**
- **Grouping prompt** — add concrete timeline threshold: "A `timeline`
  must span ≥10 items or multiple segments. Single episodes use
  `temporal_sequence`."
- **Group resolution prompt** — stronger continue preference: "When a
  temporal_sequence shares ≥3 concept_refs with an existing timeline,
  prefer `continue` over `new_thread`. The timeline absorbs the
  temporal_sequence's items in chronological order."
- **Group resolution prompt** — post-placement scan step: "After all
  groups are placed, review temporal_sequences. If adjacent sequences
  share key entities and cover sequential time periods, propose
  `merge_groups`. If a temporal_sequence is clearly part of a larger
  timeline, propose `continue`."
- **Code** — deterministic concept-overlap pre-check: temporal_sequences
  sharing ≥30% concept_refs with a timeline → boost as `continue`
  candidates in `select_group_candidates`.

#### Frontend (separate track)

- Catalog #1 P3: source block click not jumping for some concepts
- Catalog #2: "7 groups 7 graphs" counts empty theme_set graphs as graphs
- Not extraction quality — HTML/CSS/JS fixes in `tools/reader_view_template.html`

#### Status summary

| Work group | Contents | Est. lines | Priority |
|-----------|----------|------------|----------|
| A: Type accuracy | Dirs 1 + 4 (concept_type ← facets) | ~2 prompt | High |
| B: Grouping quality | Dirs 2 + 3 + cat#1 P2 | ~20 prompt+code | High |
| C: Timeline structure | Cat#1 P0 (continue, merge_groups, edges) | ~40 prompt+code | Medium |
| Frontend | Click targets, graph counts | HTML/JS | Low |

Groups A and B are independent and can ship together. Group C follows
naturally — once items are all grouped (B) and types are accurate (A),
timeline continuation has cleaner inputs.

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
