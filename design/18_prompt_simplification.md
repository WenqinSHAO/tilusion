# Prompt Simplification & Language-Specific Variants

Status: **design proposal** — builds on findings from
`16_extraction_quality_audit.md` and `17_prompt_pipeline_audit.md`.

## Problem

Two issues compound each other:

1. **Prompt inflation**: ~48% of each prompt is structural overhead the LLM
   doesn't need — pipeline hierarchy explanations, bloated schema examples,
   repeated edge-case rules. This dilutes attention on the actual task.

2. **Fragile language constraint**: An English instruction ("Write ALL text
   fields in the source language, Chinese→Chinese, English→English") tells
   the model to suppress its default output language. The English tokens
   prime English output; the model must detect the source language and
   switch. This fails ~11% of the time (79/722 concepts) and is sensitive
   to instruction placement in long prompts ("lost in the middle").

## Solution: Two Changes, One Prompt Refresh (v0.3)

### 1. Strip overhead, tighten examples

Each prompt should contain ONLY what the LLM needs to do its job. The
pipeline architecture is not that.

**Current extraction prompt structure (115 lines):**

| Section | Lines | Keep? | Why |
|---------|-------|-------|-----|
| Language instruction | 3 | Keep, reposition | Move next to output schema |
| Hierarchy explanation | 10 | **Drop** | LLM doesn't need to know about cross-unit passes, deterministic merge, or grouping. Its job is one segment, one extraction. |
| Input field descriptions | 8 | Keep | Needed to parse JSON |
| Output schema example | 42 | **Tighten to ~12** | Full empty JSON with every optional field is noise. Show one realistic concept + one realistic item with populated fields. |
| Rules | 32 | **Tighten to ~18** | Many rules repeat the schema or cover edge cases the model handles naturally (e.g., "Do not invent block IDs"). Keep the binding constraints (grounding, source_block_refs, observed_surfaces), drop the obvious. |
| Region guidance | 6 | **Tighten to ~3** | Useful signal but overly detailed per region. |

**Target: ~55 lines (52% reduction).** Same principle applies to grouping,
concept resolution, and group resolution prompts.

**General principle for deciding what to drop:**
- Is this information the LLM needs to produce correct output? → Keep
- Is this information about pipeline architecture for human readers? → Drop (belongs in design docs, not system prompts)
- Is this an edge case the model handles implicitly? → Drop (e.g., "Use simple IDs like concept-0001" — the model does this naturally)
- Is this a binding constraint that changes behavior? → Keep (e.g., "surface must be copied from source block text")

### 2. Language-specific prompt variants

Instead of one prompt with a "write in the source language" instruction,
create language-specific variants where the ENTIRE system prompt — instructions,
examples, field descriptions, rules — is in the target language.

**Mechanism**: The pipeline knows the book's primary language (detected from
text or specified by caller). At composition time, select the matching
prompt variant:

```
prompt_per_segment_extraction_v0.3_zh.md   ← Chinese
prompt_per_segment_extraction_v0.3_en.md   ← English (default)
```

Alternatively: one canonical file + generated language prefix. The
`PromptComposition` system already supports `generated_prompt_parts`.
The language-specific content (instructions + examples in target language)
is prepended as a generated part, replacing the current static "CRITICAL
— Language" banner.

**What changes per language:**
- All instructional prose (Chinese for zh, English for en)
- Schema example field values (Chinese surfaces/summaries for zh, English for en)
- Anti-examples ("Don't write `surface: \"congee\"`" → "不要写 `surface: \"congee\"`")

**What stays language-agnostic:**
- JSON field names (`surface`, `concept_type`, `source_block_refs`)
- Type vocabulary (`person`, `place`, `time_anchor`, `temporal_sequence`)
- Concept/item/group ID patterns

**Why this works**: The model reads Chinese instructions, sees a Chinese
example concept, and naturally produces Chinese output. There's no
"detect-and-switch" cognitive load. The language is correct by construction
rather than by instruction.

**Why this doesn't overfit**: The mechanism is `language → prompt variant`.
It works for any language, not just Chinese. English books use the English
variant. French books would use a French variant. The prompt structure is
identical across variants — only the human-language prose changes.

**Fallback**: If a language lacks a dedicated variant, use the English
variant with the current "write in source language" instruction. This is a
graceful degradation — the pipeline works, just with the existing fragility
for that language.

### 3. Scope: which prompts change?

| Prompt | v0.3 needed? | Why |
|--------|-------------|-----|
| Per-segment extraction | **Yes** | Primary source of Problems 1, 2, 7, 8, 9. Highest impact. |
| Unit logical grouping | **Yes** | Source of Problems 3, 4, 5. English groups from Chinese text. |
| Overview segmentation | **Yes** | Missing language banner. Produces hints that prime extraction. |
| Concept resolution (v0.2 agentic) | **Minor** | Already has language instruction. Mostly emits IDs and links. But proposals have `rationale` and `changes` fields that should match source language. |
| Group resolution (v0.2 agentic) | **Minor** | Same — mostly structural output. Group `summary` in proposals should match source language. |
| Book digest | **Already OK** | Already has strong language instruction and is prose-only. |

### 4. What this does NOT change

- **Schema**: No field changes. v0.3 prompts produce the same JSON shape as v0.2.
- **Pipeline code**: Same pass functions, same validation. Only the prompt resource loaded changes.
- **Type vocabulary**: Same concept_type and group_type lists (minus the "timeline" merge and "custom" removal from the Tier 1 fixes).
- **Tool definitions**: The generated markdown for agentic passes is language-agnostic (tool names are English identifiers).

## Interaction with Tier 1 Prompt Fixes

The Tier 1 fixes from `17_prompt_pipeline_audit.md` (repeat language
constraint, anti-examples, type consolidation, facet/canonical_name
instructions) are mostly **absorbed** by the v0.3 refresh:

| Tier 1 fix | In v0.3? |
|------------|---------|
| Repeat language constraint | **Obsoleted** — entire prompt is in target language |
| Anti-examples | **Included** — examples are in target language, anti-examples use realistic wrong values |
| Merge timeline+temporal_sequence | **Included** — use single type in both language variants |
| Remove "custom" from group_type | **Included** |
| Type definitions with criteria | **Included** — in target language |
| "Do not invent types; use `other`" | **Included** |
| Facet generation instruction | **Included** — in target language |
| Canonical name requirement | **Included** — in target language |
| Scale guidance for temporal groups | **Included** |

## Implementation Order

1. Create `prompt_per_segment_extraction_v0.3_zh.md` — the canonical tight Chinese extraction prompt (~55 lines)
2. Create `prompt_per_segment_extraction_v0.3_en.md` — English variant, same structure
3. Same for grouping prompts (zh + en)
4. Update overview segmentation prompt (add language banner, tighten)
5. Update `reading_prompts.py` — add v0.3 composition builders that load the language-appropriate resource
6. Add `source_language` parameter to pipeline — detected or caller-specified, defaults to `"en"`
7. Wire into `run_reading_pipeline()` so v0.3 prompts are used when `source_language` is set
8. Remove v0.2 prompts once v0.3 is validated

## Validation

Run unit-0003 with v0.3_zh prompts against the same source text. Compare:
- English concept count (target: 0)
- English group count (target: 0)
- Concept type distribution (target: 0 non-standard types)
- Canonical name coverage (target: >90%)
- Facet coverage (target: >90%)
- Group type distribution (target: 0 non-standard types)
