# Prompt Simplification & Field-Language Policy

Status: **design proposal** — builds on findings from
`16_extraction_quality_audit.md` and `17_prompt_pipeline_audit.md`.

## Problem

Two issues compound each other:

1. **Prompt inflation**: ~48% of each prompt is structural overhead the LLM
   doesn't need — pipeline hierarchy explanations, bloated schema examples,
   repeated edge-case rules. This dilutes attention on the actual task.

2. **Underspecified language semantics**: The prompt treats all text fields
   as if they should use one language. In reality, source identity fields
   should remain grounded in the original text, reader-facing prose should
   follow a reader preference, and internal merge signals should be stable
   normalized tags. Mixing these categories produced translated surfaces and
   inconsistent merge signals.

## Solution: Two Changes, One Prompt Refresh (v0.3)

### 1. Strip overhead, tighten examples

Each prompt should contain ONLY what the LLM needs to do its job. The
pipeline architecture is not that.

**Current extraction prompt structure (115 lines):**

| Section | Lines | Keep? | Why |
|---------|-------|-------|-----|
| Language policy | 3 | Keep, reposition | Put field-language rules next to output schema |
| Hierarchy explanation | 10 | **Drop** | LLM doesn't need to know about cross-unit passes, deterministic merge, or grouping. Its job is one segment, one extraction. |
| Input field descriptions | 8 | Keep | Needed to parse JSON |
| Output schema example | 42 | **Tighten to ~12** | Full empty JSON with every optional field is noise. Show one realistic concept + one realistic item with populated fields. |
| Rules | 32 | **Tighten to ~18** | Many rules repeat the schema. Keep binding constraints such as grounding, valid source_block_refs, observed_surfaces, and no invented block IDs; drop only the obvious or architectural narration. |
| Region guidance | 6 | **Tighten to ~3** | Useful signal but overly detailed per region. |

**Target: ~55 lines (52% reduction).** Same principle applies to grouping,
concept resolution, and group resolution prompts.

**General principle for deciding what to drop:**
- Is this information the LLM needs to produce correct output? → Keep
- Is this information about pipeline architecture for human readers? → Drop (belongs in design docs, not system prompts)
- Is this an edge case the model handles implicitly? → Drop (e.g., "Use simple IDs like concept-0001" — the model does this naturally)
- Is this a binding constraint that changes behavior? → Keep (e.g., "surface must be copied from source block text", "source_block_refs must use provided IDs")

### 2. Field-language policy instead of language-specific prompt files

Do not create separate prompt files only because the source language changes.
Keep one compact prompt template and make language roles explicit in the
payload:

```
source_language: auto|zh|en|...
reader_language: zh-Hans|en|...
normalized_language: normalized
```

The prompt should classify output fields by purpose:

| Field class | Examples | Policy |
|-------------|----------|--------|
| Source-grounded identity | `surface`, `canonical_name`, `aliases`, `observed_surfaces` | Copy from or normalize within the original source text. Never translate due to reader preference. |
| Reader-facing prose | `summary`, `rationale`, `extraction_hints`, group summaries, graph edge notes | Write in `reader_language`; default `zh-Hans`. |
| Pipeline-normalized internals | `concept_type`, `facets`, normalized time anchors, merge/type hints | Use stable controlled vocabulary/tags consistently across the pipeline. |

**Why this works**: The model no longer has to infer whether a field is an
identity anchor, UI prose, or merge signal. The prompt tells it directly.
A Chinese book can keep Chinese surfaces while producing Simplified Chinese
reader summaries by default; a future reader can request English summaries
without corrupting entity identity.

**Why this avoids drift**: One prompt template avoids duplicated zh/en files
whose schema examples, hard constraints, and allowed type lists can diverge.
Language preference becomes data, not duplicated prompt resources.

### 3. Scope: which prompts change?

| Prompt | v0.3 needed? | Why |
|--------|-------------|-----|
| Per-segment extraction | **Yes** | Primary source of translated surfaces, non-standard types, empty facets, and weak canonical names. |
| Unit logical grouping | **Yes** | Source of group type/granularity inconsistency and reader-facing language drift. |
| Overview segmentation | **Yes** | Produces `extraction_hints`; they should be reader-facing prose in `reader_language`. |
| Concept resolution (v0.2 agentic) | **Minor** | Mostly emits IDs and links, but `rationale` is reader-facing and facets/type hints are normalized internals. |
| Group resolution (v0.2 agentic) | **Minor** | Needs temporal aggregation guidance and field-language policy for summaries/rationales. |
| Book digest | **Minor** | Digest is reader-facing prose; ensure it uses `reader_language` and does not translate source-grounded identity fields. |

### 4. What this does NOT change

- **Schema**: No major field changes. v0.3 prompts produce the same JSON shape as v0.2, but payloads/manifests gain language policy fields.
- **Prompt resources**: One prompt per pass, not one prompt per language.
- **Type vocabulary**: Same concept_type list, with `custom` removed. Keep both `timeline` and `temporal_sequence`, but define their granularity.
- **Tool definitions**: The generated markdown for agentic passes is language-agnostic (tool names are English identifiers).

## Interaction with Tier 1 Prompt Fixes

The Tier 1 fixes from `17_prompt_pipeline_audit.md` (repeat language
constraint, anti-examples, type consolidation, facet/canonical_name
instructions) are mostly **absorbed** by the v0.3 refresh:

| Tier 1 fix | In v0.3? |
|------------|---------|
| Repeat language constraint | **Replaced** by field-language policy near schema |
| Anti-examples | **Included** — examples distinguish source-grounded, reader-facing, and normalized fields |
| Merge timeline+temporal_sequence | **Revised** — keep both, define hierarchy and granularity |
| Remove "custom" from group_type | **Included** |
| Type definitions with criteria | **Included** |
| "Do not invent types; use `other`" | **Included** |
| Facet generation instruction | **Included** — normalized internal tags |
| Canonical name requirement | **Revised** — required for stable named/recurring concepts, source-grounded when present |
| Scale guidance for temporal groups | **Included** — temporal sequences can aggregate into timelines |

## Implementation Order

1. Add non-fatal quality metrics first so the next run can show field-language, type, canonical-name, facet, and group-granularity failures.
2. Create `prompt_per_segment_extraction_v0.3.md` — compact single-template extraction prompt.
3. Create `prompt_unit_grouping_v0.3.md` — compact single-template grouping prompt with `timeline` / `temporal_sequence` granularity.
4. Update overview segmentation prompt to treat `extraction_hints` as reader-facing prose.
5. Update concept/group resolution prompts for field-language policy and temporal aggregation.
6. Add `source_language`, `reader_language`, and `normalized_language` to prompt payloads, pass manifests, and cache identity. Defaults: `source_language=auto`, `reader_language=zh-Hans`, `normalized_language=normalized`.
7. Update `reading_prompts.py` composition builders to inject the field-language policy and language parameters.
8. Wire into `run_reading_pipeline()`.
9. Remove or retire v0.2 prompts once v0.3 is validated; add repair/retry behavior only after metrics are calibrated.

## Validation

Run unit-0003 with v0.3 prompts, `reader_language=zh-Hans`, and the same source text. Compare:
- Reader-facing English summaries/rationales (target: 0)
- Translated or non-source `surface` / `observed_surfaces` values (target: 0)
- Concept type distribution (target: 0 non-standard types)
- Canonical name coverage for stable named/recurring concepts (target: >90%)
- Normalized facet coverage for merge-relevant concepts (target: >90%)
- Group type distribution and granularity (`timeline` vs `temporal_sequence`)
