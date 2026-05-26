# Source-Grounded Reading Pipeline Plan

This is the current design direction for tilusion extraction. It supersedes the older event/timeline-centered extraction planning docs.

## Direction

tilusion should be a source-grounded reading workspace for books and long documents. It should work across novels, essays, papers, news, notes, and mixed texts.

The core pipeline is:

```text
source text
→ overview / segmentation
→ per-segment extraction (one pass, source spans + concepts + groups + links together)
→ cross-segment finalization (dedup, stabilize IDs, emit unit package)
→ derived views (optional, downstream)
→ cross-unit registry deltas (future)
```

Timelines are useful, but they are one derived view. They are not the source of truth.

## Design Principles

**Schema-light, type-open, source-grounded:**

- Keep the outer data envelopes stable.
- Allow `concept_type`, `group_type`, and `link_type` to be extensible strings.
- Start with short recommended type sets (~8-10 each). The LLM uses these as a starter vocabulary; `other` and justified custom strings are always accepted.
- Validate IDs, source refs, provenance, confidence, and context use strictly.
- Do not privilege people, locations, time expressions, events, or timelines as the only extraction model.
- Do not require every genre to use every type.

**Prior document context** is guidance for aliasing, continuity, duplicate detection, and retrieval. It is never evidence for facts in the current unit.

**KV cache reuse:** source text is stable across passes. The overview pass loads the full unit text; later passes (finalization, derived views) can reuse that KV cache rather than re-encoding the same text. Per-segment extraction loads one segment at a time — segment text is short, so one pass is the default, with optional repair passes only when validation fails.

## Generalized Data Model

### Confidence values

`high`, `medium`, `low`, `unknown`

### Grounding / provenance values

`source_grounded`, `synthesis`, `deterministic`, `llm_inferred`, `user_corrected`

### Recommended concept_type values (~8)

`person`, `place`, `object`, `term`, `method`, `theme`, `motif`, `time_anchor`, `other`

### Recommended group_type values (~8)

`event`, `claim`, `argument`, `observation`, `description`, `explanation`, `question`, `other`

### Recommended link_type values (~10)

`mentions`, `supports`, `contradicts`, `causes`, `precedes`, `elaborates`, `part_of`, `exemplifies`, `related_to`, `other`

### SourceSpan

```json
{
  "span_id": "span-0001",
  "unit_id": "unit-0002",
  "source_range": {
    "kind": "unit-char-span",
    "start": 120,
    "end": 180
  },
  "quote": "source quote",
  "relocation": {
    "status": "exact|relocated|ambiguous|missing",
    "strategy": "exact|annotation_whitespace_tolerant|..."
  },
  "provenance": {
    "created_by": "llm|deterministic|user",
    "pass_id": "..."
  }
}
```

### SourceBlock

```json
{
  "block_id": "block-0001",
  "block_type": "sentence|paragraph|line|quote|region|clause|other",
  "span_refs": ["span-0001"],
  "source_order": 12,
  "text_digest": "sha256...",
  "confidence": "high|medium|low|unknown"
}
```

### ConceptMention

```json
{
  "mention_id": "mention-0001",
  "surface": "exact source surface",
  "concept_type": "person|place|term|method|theme|other|custom",
  "canonical_name": "optional normalized name",
  "local_summary": "brief source-grounded note",
  "aliases_or_candidates": [],
  "source_block_refs": ["block-0001"],
  "source_span_refs": ["span-0001"],
  "confidence": "high|medium|low|unknown",
  "facets": ["behaves_like_person", "speaker", "time_anchor"],
  "uncertainty": []
}
```

### CanonicalConcept

```json
{
  "concept_id": "concept-000001",
  "canonical_name": "陈芸",
  "concept_types": ["person", "social_role"],
  "facets": ["behaves_like_person"],
  "aliases": ["芸", "淑珍"],
  "observed_surfaces": [],
  "summary": "compact rolling summary",
  "first_seen_unit": "unit-0002",
  "last_seen_unit": "unit-0005",
  "salience": 0.82,
  "evidence_refs": [],
  "alias_candidates": [],
  "merge_split_uncertainty": []
}
```

### LogicalGroup

```json
{
  "group_id": "group-0001",
  "group_type": "event|claim|argument|observation|description|other|custom",
  "summary": "short source-grounded compression",
  "source_block_refs": ["block-0001", "block-0007"],
  "concept_refs": ["mention-0001", "mention-0002"],
  "source_order_hints": {
    "first_block": "block-0001",
    "last_block": "block-0007"
  },
  "temporal_hints": [],
  "confidence": "high|medium|low|unknown",
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded"
  }
}
```

### GroupLink

```json
{
  "link_id": "link-0001",
  "source_ref": "group-0001",
  "target_ref": "group-0002",
  "link_type": "supports|contradicts|causes|elaborates|precedes|related_to|other",
  "evidence_block_refs": ["block-0003"],
  "confidence": "medium",
  "rationale": "brief reason",
  "grounding": "source_grounded|synthesis",
  "uncertainty": []
}
```

### DerivedStructure

```json
{
  "view_id": "view-0001",
  "view_type": "timeline|discourse_graph|claim_evidence_map|theme_map|viewpoint_evolution|open_thread_list",
  "input_group_refs": [],
  "input_link_refs": [],
  "structure": {},
  "confidence": "high|medium|low|unknown",
  "generated_by": "deterministic|llm",
  "is_source_of_truth": false
}
```

### ExtractionUnitPackage

```json
{
  "schema_version": "reading-unit-v0.1",
  "unit_id": "unit-0002",
  "source": {},
  "source_spans": [],
  "source_blocks": [],
  "concept_mentions": [],
  "logical_groups": [],
  "links": [],
  "derived_views": [],
  "unresolved_items": [],
  "validation": {},
  "context_metadata": {}
}
```

### DocumentStateSnapshot

```json
{
  "snapshot_id": "snapshot-...",
  "document_id": "doc-...",
  "canonical_concepts": [],
  "reusable_group_summaries": [],
  "cross_unit_links": [],
  "derived_checkpoints": [],
  "ambiguity_queue": [],
  "transactions": []
}
```

### RegistryDelta

```json
{
  "delta_id": "delta-...",
  "base_snapshot_id": "snapshot-...",
  "unit_id": "unit-0003",
  "operations": [],
  "validation": {}
}
```

Delta operation types:

- `new_canonical_concept`
- `alias_candidate`
- `merge_proposal`
- `split_proposal`
- `summary_update`
- `concept_salience_update`
- `logical_group_continuation`
- `new_cross_unit_link`
- `derived_checkpoint_update`
- `unresolved_ambiguity_item`
- `user_review_needed`

## Pipeline Stages (Simplified)

### Stage 1: Overview / Segmentation

Purpose: understand unit structure, identify coarse regions, skip sparse/front-matter areas, and create extraction windows (segments).

Input: reader unit text, reader metadata, optional compact context summary.

Output: source regions with anchor quotes and extraction hints.

Backend: LLM-backed, followed by deterministic span restoration.

The full unit text is loaded into context here. Its KV cache can be reused by later stages (finalization, derived views) — they share the same source text prefix.

Cache key: source text hash, overview prompt hash, model identity, optional context pack hash.

### Stage 2: Per-Segment Extraction (One Pass)

Purpose: from one segment's source text, extract source spans, concept mentions, logical groups, and links — all together in a single LLM call.

Input: restored segment text, source coordinates, overview hints, optional context pack.

Output: `SourceSpan`, `SourceBlock`, `ConceptMention`, `LogicalGroup`, `GroupLink`.

Backend: LLM-backed, deterministic grounding validation after.

Why one pass: a segment is short enough that the LLM can make coherent decisions about what concepts exist, how they group, and how groups relate — all from the same reading of the text. Separating these into multiple passes adds latency without improving quality when the text window is small.

Optional repair: if validation fails, a follow-up repair pass reuses the same segment text (KV cache shared) with repair hints. This is pay-as-you-go — most segments need no repair.

Validation: required fields, local IDs, exact/relocatable quotes, source ranges, concept source refs, type string shape, confidence, source-grounded links must cite evidence blocks.

Cache key: region text hash, prompt hash, model identity, overview result hash, optional context pack hash.

### Stage 3: Cross-Segment Finalization

Purpose: deduplicate local records across segments, stabilize unit-level IDs, resolve local aliases, preserve provenance, and emit a unit package.

Input: all segment-level source blocks, concepts, groups, links, validation reports, repair hints.

Output: `ExtractionUnitPackage`.

Backend: LLM-backed plus deterministic cleanup.

Can reuse the unit-text KV cache from stage 1 (source text is a shared prefix).

Why separate: finalization is the only stage that should decide unit-level IDs, local merges, and unresolved items.

Validation: unit-level ID uniqueness, ref integrity, duplicate handling, no evidence from prior context, unresolved ambiguity preservation.

Cache key: all prior pass result hashes, finalization prompt hash, model identity, optional context pack hash.

### Stage 4: Derived Views (Optional, Downstream)

Purpose: build timeline, discourse graph, claim/evidence map, viewpoint evolution, theme map, or open-thread list from finalized records.

Input: finalized unit package.

Output: `DerivedStructure` records.

Backend: deterministic where possible, LLM-backed when synthesis is needed.

Can reuse the unit-text KV cache from stage 1.

Why separate: derived views should never mutate core records.

Validation: input refs exist, view states `is_source_of_truth: false`, timeline DAG checks where applicable.

Cache key: unit package hash, view config hash, prompt hash if LLM-backed.

## Context Assembly and KV Cache Strategy

The unit source text is loaded in stage 1. Stages 3 and 4 can reuse that KV cache — they share the same source text prefix and only differ in the instruction and structured data that follows.

Per-segment extraction (stage 2) loads one segment at a time. Since segments are short, one pass is the default. When repair is needed, the follow-up call shares the segment text KV cache.

Context packs for cross-unit continuity:

- Deterministic surface scan over canonical concepts and observed surfaces.
- Recent/high-salience concept summaries.
- Active unresolved questions or ambiguity items.
- Relevant cross-unit links and derived checkpoints.
- Selection report explaining every inclusion and exclusion category.

Large context windows should be used for periodic consolidation, alias merge audits, conflict resolution, rebuilding compact summaries, and long-range synthesis — not for dumping the whole registry into every routine unit extraction.

## Cross-Unit Update And Registry Delta (Future)

For each new unit:

1. Extract local records from current source text.
2. Build a unit package with source-grounded concepts, groups, and links.
3. Compare the unit package with selected prior document state.
4. Emit a registry delta.
5. Validate the delta deterministically.
6. Apply accepted operations to a new snapshot.
7. Write transaction logs and snapshot artifacts.

Rules:

- The current unit text is the only evidence for new local records.
- Prior extraction is guidance for continuity, alias resolution, duplicate detection, and retrieval.
- Prior context must not be cited as evidence.
- LLM output proposes deltas, not direct global mutations.
- Destructive merges require deterministic safety or user approval.
- Ambiguity is preserved in the ambiguity queue instead of being hidden in warnings.

This subsystem needs a more detailed plan once unit-level extraction is stable.

## Derived Views

Derived views consume concepts, source blocks, logical groups, and links. They do not become source of truth.

Timeline:

- Built from event-like groups, temporal hints, and links such as `precedes`, `continues`, and `causes`.
- Stored as `DerivedStructure(view_type="timeline")`.
- May be absent when the unit has no meaningful temporal structure.

Discourse graph:

- Built from claim, argument, evidence, counterevidence, and explanation groups.
- Uses links such as `supports`, `contradicts`, `qualifies`, `follows_from`, and `explains`.

Viewpoint evolution around concept X:

- Selects groups linked to a concept and orders them by source order or temporal hints.
- Useful for novels, essays, news, papers, and notes.

Claim/evidence map:

- Connects claim-like groups to evidence, counterevidence, and source statements.
- Useful for papers, news, argumentative essays, and research notes.

Theme/concept map:

- Clusters motif, theme, description, example, comparison, and contrast groups.
- Useful for literary reading and essay analysis.

Unresolved question / open-thread list:

- Built from question, problem, unresolved issue, and related links.
- Replaces thread as a general downstream view.

## Current State Review

The current `cross-unit-refactor` branch has a usable but narrative-biased pipeline:

- `run-chain` performs overview segmentation, deterministic segment restoration, per-segment extraction, validation, and repair-hint generation.
- `finalize-unit` merges segment records into unit-level entities, locations, atoms, threads, unresolved items, and quality notes.
- `repair-unit` repairs unit-level extraction from deterministic hints.
- `timeline-unit` builds optional partially ordered timelines from atom records.
- `repair-timeline` repairs timeline validation failures.
- `run-all` writes `.tilusion_cache/units/<unit_id>/unit_package.json`.

Strong parts to preserve:

- Explicit prompt composition through `PromptPart` and `PromptComposition`.
- Inspectable pass artifacts: prompt composition, system prompt, request payload, raw response, parsed result, validation report, validated result, and manifest.
- Deterministic evidence relocation and source-window reconstruction.
- Separation between local validation reports and concise LLM repair payloads.
- Context packs whose hashes are included in cache keys when `context_injection.enabled` is true.
- Reader/index layer with stable unit IDs and source coordinate extraction.

Current concepts to rename or generalize:

- `evidence_spans` → `source_spans` and `source_blocks`.
- `entity_mentions` + `location_mentions` → `concept_mentions`.
- `time_expressions` → concept mentions of type `time_anchor`, plus temporal hints on groups when useful.
- `atom_mentions` / `atom_records` → `logical_groups`.
- `thread_candidates` / `thread_records` → open-question, theme, continuity, or thread-like derived structures built from groups and links.
- `timelines` → `derived_views` of type `timeline`.
- `book_context` → document state/context pack with canonical concepts, compact group summaries, links, ambiguity queues, and derived checkpoints.

Current concepts to remove or decenter:

- Timeline construction as a required core pass.
- Fixed `entity/location/time/event/thread/timeline` top-level schema.
- Thread as a universal organizing container.
- Old `event_records` compatibility shims.

## Validation And Tests

Required tests:

- Recommended concept types are accepted.
- Custom/open concept types are accepted and preserved.
- Recommended group/link types are accepted.
- Custom/open group/link types are accepted and preserved.
- Source-grounded concept mentions require source block/span refs.
- Source-grounded links require evidence block refs.
- Synthesis links must be marked as synthesis and cannot pretend to be direct evidence.
- One logical group can cite non-contiguous source blocks.
- Multiple logical groups can share a source block.
- Timeline is a derived view, not a core package field.
- Discourse graph is a derived view, not a core package field.
- Registry deltas validate against a base snapshot hash.
- Prior context cannot appear in evidence refs for current-unit records.
- Merge proposals do not mutate canonical records unless applied by a validated transaction.
- Cache keys include context-pack hashes when context injection is enabled.
- Multi-pass provenance survives from source span to concept/group/link/unit package.

Likely modules:

- `tilusion/reading_schema.py`
- `tilusion/reading_validation.py`
- `tilusion/reading_prompts.py`
- `tilusion/reading_payloads.py`
- `tilusion/reading_pipeline.py`
- `tilusion/document_state.py`
- `tilusion/registry_delta.py`
- `tilusion/derived_views.py`

## Implementation Sequence

### Commit 1: Canonical Plan (done)

- Add this document.
- Compact `PROGRESS.md` around the new direction.
- Remove stale extraction planning docs.

### Commit 2: Reading Schema (done)

- Add `tilusion/reading_schema.py`.
- Define stable outer envelopes, recommended type constants (~8 each), confidence values, grounding values, and JSON helpers.
- Add tests for type-open schema behavior.

### Commit 3: Generalized Validation (done)

- Add `tilusion/reading_validation.py`.
- Validate spans, blocks, concepts, groups, links, derived views, unit packages, and provenance.
- Add deterministic tests before LLM prompt changes.

### Commit 4: Prompt Resources (done)

- Add per-segment extraction prompt (source spans + concepts + groups + links, one pass).
- Add unit finalization prompt.
- Keep prompts externalized and versioned.
- Do not run expensive LLM calls yet.

### Commit 5: Payload And Prompt Composition (done)

- Add `tilusion/reading_payloads.py` and `tilusion/reading_prompts.py`.
- Reuse `PromptPart` / `PromptComposition`.

### Commit 6: Mock Backend

- Add mock responses for per-segment extraction and unit finalization.
- Keep tests LLM-free.

### Commit 7: Reading Pipeline Orchestrator

- Add `tilusion/reading_pipeline.py`.
- Wire stages 1-3: overview/segmentation (reuse existing), per-segment extraction (one pass), cross-segment finalization.
- Write artifacts for every pass.
- Produce `ExtractionUnitPackage`.

### Commit 8: Derived Views

- Add timeline, discourse graph, claim/evidence map, theme map, viewpoint evolution, and open-thread list builders.
- Keep them downstream of unit packages.

### Commit 9: CLI Wiring

- Add or replace CLI commands for the new reading pipeline.
- Compatibility with old event/timeline commands is not required.

### Commit 10: LLM Trials

- Run mock first.
- Then run DeepSeek on unit-0002 and unit-0003 after schema, validation, cache, and artifact layout are stable.

### Later: Document Context + Registry Delta

- Replace entity/location/thread/event/timeline registry with canonical concepts, group summaries, links, ambiguity queue, and derived checkpoints.
- Add registry delta schema, deterministic validation, transaction logs, and snapshot writes.
- This needs a more detailed plan once unit-level extraction is stable.
