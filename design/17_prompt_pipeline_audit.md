# Pipeline Prompt Audit: Root Causes and Improvement Actions

Status: **audit complete** — traces all prompts and pipeline passes for
units 2–4 of 浮生六记, identifies root causes for the 10 quality problems
documented in `16_extraction_quality_audit.md`, and recommends concrete
improvements ordered by impact-to-effort ratio.

---

## Pipeline Architecture (per unit, book scope)

```
Step 0: Book Digest (LLM)
  Prompt: prompt_book_digest_v0.1.md
  Input: registry entities, previous digest
  Output: digest (Chinese prose) → fed to Step 1

Step 1: Overview Segmentation (LLM)
  Prompt: overview_segmentation_v0.2.md
  Input: unit_text, context.digest
  Output: segments + extraction_hints → fed to Step 2

Step 2: Per-Segment Extraction × N (LLM, parallel, N≤4)
  Prompt: prompt_per_segment_extraction_v0.2.md
  Input: segment, source_blocks, text+markers, context (extraction_hints)
  Output: concepts + atomic_items → fed to Step 3

Step 3: Within-Unit Deterministic Merge (no LLM)
  Merges concepts by surface/canonical_name/alias overlap

Step 4: Cross-Unit Concept Resolution (LLM, agentic multi-round)
  Prompt: prompt_concept_resolution_v0.2.md + tool definitions
  Input: concepts, registry_index, candidate_map
  Output: resolution_proposals (link, merge, split, new_concept, etc.)

Step 5: Unit Logical Grouping (LLM, v0.2)
  Prompt: prompt_unit_grouping_v0.2.md
  Input: resolved concepts, atomic_items, unit_text, segments
  Output: logical_groups → fed to Step 6

Step 6: Cross-Unit Group Resolution (LLM, agentic multi-round)
  Prompt: prompt_group_resolution_v0.2.md + tool definitions
  Input: groups, concepts, registry_groups
  Output: group_resolution_proposals
```

---

## Root Cause Analysis: One Problem at a Time

### Problem 1+2: Language Non-Compliance + English Surfaces

**Symptom**: 79 concepts have English summaries, 15 groups have English
summaries, 19 concepts have English-only surfaces. Concentrated in unit-0003.

**Where it originates**: Step 2 (Per-Segment Extraction) for concepts,
Step 5 (Unit Logical Grouping) for groups.

**All six prompts have a language instruction.** The extraction prompt
(line 3) says:

> **CRITICAL — Language:** Write ALL text fields in the source language
> (Chinese→Chinese, English→English). Never translate or mix.

The grouping prompt (line 3) has identical wording. So why did unit-0003
ignore it?

**Root cause: "Lost in the middle."** The language instruction is at
line 3 of a 115-line extraction prompt. By the time the model's attention
reaches the concept schema definition at line 44 and the field rules at
line 91, the CRITICAL instruction at line 3 has low attention weight.

Compounding factors:

1. **The overview segmentation prompt (Step 1) lacks the CRITICAL banner.**
   Line 43 says "Write segment-specific attention cues (1-2 sentences in
   the source language)" — a weak, buried instruction. If the overview
   pass produces English hints, those English tokens prime the extraction
   LLM to respond in English.

2. **The book digest (Step 0) has a language instruction** (line 3: "Write
   the entire digest in the source language") but only targets the digest
   prose — concept names are copied from input, which may already contain
   English surfaces from a prior unit.

3. **No structural enforcement.** The prompts rely entirely on natural
   language instructions. There's no JSON schema constraint, no validator,
   no re-extraction fallback. The LLM writes English and the pipeline
   silently accepts it.

4. **Unit-0003 specifically**: The unit covers flower arranging, garden
   design, and tea ceremony — domains where English horticultural/design
   vocabulary exists. The model may have associated these topics with
   English descriptive prose.

5. **English surfaces (Problem 2)**: The extraction prompt line 91 says
   "concept.surface must be copied exactly from source block text." But
   for common objects (boat, alley, door, luggage), the model defaults
   to the *concept* rather than the *lexical form* — it writes "boat"
   because it's thinking of the concept BOAT, not the Chinese word 船.

**Recommended actions** (ordered by impact):

| # | Action | Effort | File |
|---|--------|--------|------|
| A1 | Repeat language constraint before the concept schema block (line ~44) and before the item schema block (line ~58) | 5 lines | extraction prompt |
| A2 | Add explicit anti-example: "DO NOT write `surface: \"congee\"` when the source text says 粥. DO NOT write `summary: \"The method of arranging flowers\"` when the source is Chinese." | 5 lines | extraction prompt |
| A3 | Add CRITICAL language banner to `overview_segmentation_v0.2.md` (currently missing) | 2 lines | overview prompt |
| A4 | Add post-extraction language validator: if source language is Chinese and >30% of concept summaries have more ASCII alpha than CJK characters, emit warning | ~30 lines | `reading_pipeline.py` |
| A5 | Add `"language": "zh"` or similar to the extraction payload so the model sees the language constraint as data, not just instruction | 1 line | `reading_payloads.py` |

---

### Problem 3+5: Group Type Proliferation + Misclassification

**Symptom**: 7 group types for 26 groups. "timeline" vs "temporal_sequence"
are semantically identical but track the language split (English units say
"timeline", Chinese units say "temporal_sequence"). Non-standard types
("claim_evidence_map", "discourse_graph") appear as one-offs. Three groups
labeled "timeline" are not chronological sequences.

**Where it originates**: Step 5 (Unit Logical Grouping), prompt
`prompt_unit_grouping_v0.2.md`.

**Root cause**: The group_type schema in the prompt is self-contradictory:

```json
"group_type": "timeline|temporal_sequence|theme_set|concept_map|discourse_graph|
                claim_evidence_map|viewpoint_evolution|open_thread_list|
                method_example_set|motif_development|contrast_set|other|custom"
```

This is 13 types. Problems:

1. **"timeline" AND "temporal_sequence" are BOTH in the list.** The prompt
   never explains the difference (because there is none). The model picks
   whichever matches its current language mode — English → "timeline",
   Chinese → "temporal_sequence". This is a schema design error, not a model
   error.

2. **"schema-light" + "custom" invites invention.** Line 98 says "group_type
   is schema-light. Use a recommended type when it fits; otherwise use
   other or a concise custom string." This is a license to invent types.
   The model invents "claim_evidence_map" and "discourse_graph" because
   the prompt told it custom strings are fine.

3. **No type definitions.** The prompt lists type names but never explains
   what distinguishes a "timeline" from a "theme_set" from a
   "method_example_set". The model guesses based on the English word
   meanings — and guesses differently across units.

4. **No scale distinction for temporal groups.** "A single outing to South
   Garden" (8 items) and "the author's entire love story" (140 items) both
   qualify as "timeline" under the current rules. The prompt has no concept
   of temporal scale (micro-episode vs. macro-arc).

**Recommended actions**:

| # | Action | Effort | File |
|---|--------|--------|------|
| B1 | Merge "timeline" and "temporal_sequence" into ONE type. Use "temporal_sequence" consistently (the Chinese pipeline already uses it). | 3 lines | grouping prompts |
| B2 | Remove "custom" from allowed group_type values. Keep "other" as the escape hatch. | 1 line | grouping prompts |
| B3 | Add type definitions with distinguishing criteria: temporal_sequence = sequential events with temporal ordering; theme_set = flat collection of items sharing a theme; method_example_set = techniques and examples. | ~15 lines | grouping prompts |
| B4 | Add scale guidance: "A temporal_sequence should represent a meaningful narrative arc. Single events or outings with ≤5 items should use a different group type or be merged into a larger temporal_sequence." | ~5 lines | grouping prompts |
| B5 | Reclassify non-standard group types in the group resolution pass (Step 6). Already supported via `mutate` proposals with `changes.group_type`. | 0 lines (prompt already allows) | group resolution prompt |

---

### Problem 4: Temporal Sequence Fragmentation

**Symptom**: Groups 0018-0023 are 6 sequential temporal_sequences covering
one continuous narrative (Yun's decline → death → aftermath, 109 items
total). Also: unit-0002 produced one 140-item timeline, unit-0004 produced
six smaller ones.

**Where it originates**: Step 5 (Unit Logical Grouping). The grouping LLM
sees atomic items organized by segment. It creates one group per narrative
episode rather than recognizing the macro-structure.

**Root cause**: The grouping prompt says "Prefer fewer meaningful groups
over many tiny ones" (line 92) — a correct principle — but provides no
mechanism for the model to recognize which items belong together across
segment boundaries. The model defaults to the segment structure: each
segment gets its own groups.

The group resolution pass (Step 6) SHOULD merge adjacent temporal_sequences
via `merge_groups` proposals. But:

1. group-0020 (20 items, 西人索债与夜遁) and group-0021 (14 items, 寄居华家与靖江求助)
   have different concept_refs sets, so the concept-overlap candidate filter
   may not surface them as candidates for each other.
2. The group resolution prompt doesn't explicitly instruct the LLM to look
   for adjacent temporal_sequences and propose `merge_groups`.
3. The temporal adjacency signal (items in group-0020 precede items in
   group-0021 chronologically) isn't visible in the compact group index.

**Recommended actions**:

| # | Action | Effort | File |
|---|--------|--------|------|
| C1 | Add to grouping prompt: "When multiple temporal_sequences form a continuous narrative arc (sequential in time, same key characters/entities), merge them into one temporal_sequence with the combined items in chronological order." | ~5 lines | grouping prompt |
| C2 | Add to group resolution prompt: "Look for adjacent temporal_sequences (sequential time periods, same key concepts). Propose `merge_groups` to combine them." | ~5 lines | group resolution prompt |
| C3 | Consider adding `precedes` cross-group edges as a lighter-weight alternative to full merge, so groups remain independently navigable but their temporal order is explicit. | Already supported | group resolution prompt |

---

### Problem 6: Unmerged Semantically Identical Groups

**Symptom**: group-0016 (EN: "Footnotes and commentary items") and
group-0026 (CN: "注释类（脚注）") are the same thing, not merged.

**Where it originates**: Failure at Step 6 (Cross-Unit Group Resolution).

**Root cause**: The group resolution pass has two gates, and this case
fails both:

1. **Candidate selection gate** (`select_group_candidates`): Uses
   concept-overlap as the primary signal. group-0026 has empty
   `concept_refs` — it was never linked to any concepts. So the concept
   overlap filter returns nothing. Falls through to dual-signal retrieval.

2. **Dual-signal gate**: BM25 compares "Footnotes and commentary items"
   against "注释类（脚注）". Zero token overlap (different languages).
   Embedding similarity: Qwen3-Embedding-0.6B may or may not capture
   cross-lingual semantic similarity for short phrases. Even if it does,
   the similarity score may be below the 0.3 threshold.

3. **Agentic gate**: Even if the groups ARE in each other's candidate
   lists, the LLM must call `get_group` for both, read the full records,
   recognize they describe the same concept (footnotes), and propose
   `merge_groups`. This requires the LLM to bridge English/Chinese, which
   it can do, but only if it investigates both groups.

**Root cause summary**: Cross-lingual group matching fails at every
retrieval stage. The pipeline assumes all text is in one language, and
when some groups are English and others Chinese, the retrieval signals
(lexical BM25, monolingual embedding) break down.

**Recommended actions**:

| # | Action | Effort | File |
|---|--------|--------|------|
| D1 | This is primarily a downstream effect of Problem 1. Fixing language compliance eliminates the cross-lingual gap. | — | — |
| D2 | In the group resolution prompt, add: "Groups with empty concept_refs may still match registry groups with different surface language — call `get_group` to inspect the items when the summary topic sounds familiar." | ~3 lines | group resolution prompt |

---

### Problem 7: Non-Standard Concept Types

**Symptom**: 25 concepts use types outside the standard vocabulary:
`event` (8), `statement` (6), `action` (7), `activity` (4).

**Where it originates**: Step 2 (Per-Segment Extraction).

**Root cause**: The extraction prompt lists allowed types but doesn't
prohibit others strongly enough. Line 92 says:

> "Use `other` or a custom string only when clearly needed."

This is permissive. A model uncertain between `method` and `other` might
invent `action` as a compromise. The same issue exists with group types.

The within-unit grouping pass (Step 5, v0.1) has `reclassify` deltas to
fix non-standard types. But in v0.2 (current pipeline), the grouping pass
no longer emits concept deltas — concepts are "already resolved" by the
prior cross-unit pass. So non-standard types from extraction survive
unchanged.

**Recommended actions**:

| # | Action | Effort | File |
|---|--------|--------|------|
| E1 | Tighten extraction prompt: "Use ONLY the types listed above. Do not invent new concept types. If none fit, use `other`." | 1 line | extraction prompt |
| E2 | Add type reclassification to the cross-unit concept resolution pass (Step 4): the `reclassify` proposal type already exists but the LLM may not prioritize it. Add: "Scan for non-standard concept types and propose `reclassify` to map them to standard vocabulary." | ~3 lines | concept resolution prompt |

---

### Problem 8+9: No Facets + Missing Canonical Names

**Symptom**: 0/722 concepts have facets. 282/722 (39%) lack canonical_name.

**Where it originates**: Step 2 (Per-Segment Extraction).

**Root cause**: 

For **facets**: The field exists in the schema example (line 53:
`"facets": []`) but there's NO instruction to populate it. The prompt
never says what facets are, how many to provide, or at what abstraction
level. The model sees an empty array in the example and faithfully
reproduces it.

For **canonical names**: The schema shows `"canonical_name": ""` (empty
string) as the example (line 48). The model follows the example literally
and leaves canonical_name empty unless it's obvious. There's no explicit
instruction like "Every concept MUST have a canonical_name — the
standardized, full form of the entity's name in the source language."

**Recommended actions**:

| # | Action | Effort | File |
|---|--------|--------|------|
| F1 | Add facet generation instruction: "For each concept, provide 2-5 type-describing phrases at different abstraction levels in `facets`. Example: a treaty concept → `[\"treaty\", \"legal document\", \"historical event\"]`. Facets supplement concept_type — they do not replace it." This is Phase 2. | ~10 lines | extraction prompt |
| F2 | Add canonical_name instruction: "Every concept MUST have a `canonical_name`. For persons, use the full standard name. For places, use the complete place name. For terms, use the normalized form. Leave empty ONLY if no standardized form exists." | ~5 lines | extraction prompt |
| F3 | Change the schema example from `"canonical_name": ""` to `"canonical_name": "沈复"` (a realistic example) so the model doesn't learn to leave it empty. | 1 line | extraction prompt |
| F4 | Add canonical_name coverage metric to pipeline metrics. | ~10 lines | `reading_pipeline.py` |

---

### Problem 10: Provenance Tag Inconsistency

**Symptom**: Only 58/722 concepts have `[unit-N]:` provenance tags.
10 have `[?]` (unresolved).

**Where it originates**: The `merge_concepts` function in
`book_registry.py` concatenates summaries during merge. If the unit concept
had no provenance tag (extractor didn't emit one), the merged concept also
lacks it.

**Root cause**: The extractor is not instructed to prepend `[unit-N]:` to
summaries. This is a pipeline convention that happens in
`merge_segment_extraction_results` (reading_pipeline.py), not in the LLM
output. When concepts are merged across units, the provenance is appended
by the pipeline code, but only if the field is populated.

**Recommended actions**:

| # | Action | Effort | File |
|---|--------|--------|------|
| G1 | Ensure the pipeline consistently prepends `[unit-N]:` when merging concepts into the registry. Check `compute_registry_delta` / `apply_registry_delta`. | ~10 lines | `registry_delta.py` |

---

## Summary: Prioritized Improvement Plan

### Tier 1 — Prompt fixes (highest impact, lowest effort, ≤50 lines total)

These directly address Problems 1, 2, 3, 5, 7 and can be done before Phase 2.

| Order | Action | Problem(s) | Lines |
|-------|--------|-----------|-------|
| 1 | Repeat language constraint before concept/item schema blocks in extraction prompt | 1, 2 | +5 |
| 2 | Add anti-examples (DO NOT translate surface/summary) to extraction prompt | 1, 2 | +5 |
| 3 | Add CRITICAL language banner to overview segmentation prompt | 1 | +2 |
| 4 | Merge "timeline" + "temporal_sequence" into single type in both grouping prompts | 3, 5 | -1 |
| 5 | Remove "custom" from group_type, add type definitions with criteria | 3, 5 | +15 |
| 6 | Add "Do not invent concept types; use `other`" to extraction prompt | 7 | +1 |
| 7 | Add facet generation instruction to extraction prompt | 8 | +10 |
| 8 | Add canonical_name requirement + realistic example to extraction prompt | 9 | +5 |
| 9 | Add scale guidance for temporal sequences | 4, 5 | +5 |
| 10 | Add "merge adjacent temporal sequences" to grouping prompt | 4 | +5 |
| 11 | Add reclassify scan instruction to concept resolution prompt | 7 | +3 |

### Tier 2 — Pipeline validation (medium effort, ~60 lines)

| Order | Action | Problem(s) | Lines |
|-------|--------|-----------|-------|
| 12 | Post-extraction language validator | 1 | +30 |
| 13 | Add `"source_language"` field to extraction payload | 1 | +1 |
| 14 | Canonical name coverage metric | 9 | +10 |
| 15 | Fix provenance tag propagation in registry delta | 10 | +10 |

### Tier 3 — Phase 2 proper (soft typing, ~400 lines)

| Order | Action | Problem(s) |
|-------|--------|-----------|
| 16 | Facet-aware type compatibility in deterministic filter | 7, 8 |
| 17 | Identity-gated soft typing in merge boundary | 7, 8 |
| 18 | Facet population validation | 8 |

### Tier 4 — Phase 3 (richer hints, ~350 lines)

| Order | Action | Problem(s) |
|-------|--------|-----------|
| 19 | Per-segment known-concept lookup | 4, 6 |
| 20 | Known vs. new flagging in extraction | 4, 6 |
| 21 | Enriched segment context with known concepts | 4, 6 |
