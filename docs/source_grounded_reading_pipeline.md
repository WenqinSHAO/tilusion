# Source-Grounded Reading Pipeline Plan

This is the current design direction for tilusion extraction. It supersedes the older event/timeline-centered extraction planning docs.

## Direction

tilusion should be a source-grounded reading workspace for books and long documents. It should work across novels, essays, papers, news, notes, and mixed texts.

The core pipeline is:

```text
source text
→ source blocks
→ concepts
→ logical groups
→ links
→ derived views
→ cross-unit registry deltas
```

Timelines are useful, but they are one derived view. They are not the source of truth.

The extraction model should be schema-light, type-open, and source-grounded:

- Keep the outer data envelopes stable.
- Allow `concept_type`, `group_type`, and `link_type` to be extensible strings.
- Start with recommended type sets, but allow `other` and justified custom strings.
- Validate IDs, source refs, provenance, confidence, and context use strictly.
- Do not privilege people, locations, time expressions, events, or timelines as the only extraction model.
- Do not require every genre to use every type.

Prior document context is guidance for aliasing, continuity, duplicate detection, and retrieval. It is never evidence for facts in the current unit.

## Current State Review

The current `cross-unit-refactor` branch has a usable but still narrative-biased pipeline:

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

- `evidence_spans` -> `source_spans` and `source_blocks`.
- `entity_mentions` + `location_mentions` -> `concept_mentions`.
- `time_expressions` -> concept mentions of type `time_anchor`, plus temporal hints on groups when useful.
- `atom_mentions` / `atom_records` -> `logical_groups`.
- `thread_candidates` / `thread_records` -> open-question, theme, continuity, or thread-like derived structures built from groups and links.
- `timelines` -> `derived_views` of type `timeline`.
- `book_context` -> document state/context pack with canonical concepts, compact group summaries, links, ambiguity queues, and derived checkpoints.

Current concepts to remove or decenter:

- Timeline construction as a required core pass.
- Fixed `entity/location/time/event/thread/timeline` top-level schema.
- Thread as a universal organizing container. It works for narrative books but not for papers, essays, news, or notes.
- Old `event_records` compatibility shims. The project has no stable API consumers yet, so schema cleanup is preferred over preserving old names.

## Generalized Data Model

Recommended confidence values:

- `high`
- `medium`
- `low`
- `unknown`

Recommended grounding/provenance values:

- `source_grounded`
- `synthesis`
- `deterministic`
- `llm_inferred`
- `user_corrected`

Recommended `concept_type` values:

- `person`
- `group`
- `organization`
- `place`
- `object`
- `term`
- `theory`
- `method`
- `motif`
- `theme`
- `problem`
- `hypothesis`
- `claim_target`
- `emotion`
- `social_role`
- `institution`
- `symbol`
- `scene_element`
- `technical_component`
- `dataset`
- `metric`
- `source`
- `time_anchor`
- `other`

Recommended `group_type` values:

- `event`
- `scene`
- `action`
- `observation`
- `description`
- `claim`
- `argument`
- `evidence`
- `counterevidence`
- `hypothesis`
- `inference`
- `explanation`
- `definition`
- `example`
- `method`
- `technique`
- `result`
- `limitation`
- `problem`
- `question`
- `unresolved_issue`
- `motif`
- `theme_development`
- `comparison`
- `contrast`
- `causal_link`
- `background`
- `source_statement`
- `note`
- `other`

Recommended `link_type` values:

- `mentions`
- `refers_to`
- `aliases`
- `same_as_candidate`
- `part_of`
- `elaborates`
- `supports`
- `contradicts`
- `qualifies`
- `contrasts`
- `causes`
- `enables`
- `explains`
- `follows_from`
- `precedes`
- `continues`
- `resolves`
- `raises_question`
- `answers_question`
- `exemplifies`
- `defines`
- `uses_method`
- `produces_result`
- `has_limitation`
- `related_to`
- `other`

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
  "concept_type": "person|place|method|theme|other|custom",
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
  "group_type": "event|claim|argument|method|description|other|custom",
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

## Multi-Pass Pipeline

### 1. Overview / Source Region Pass

Purpose: understand local structure, identify coarse regions, skip sparse/front-matter/noisy areas, and create extraction windows.

Input: reader unit text, reader metadata, optional compact context summary.

Output: source regions with anchor quotes and extraction hints.

Backend: LLM-backed, followed by deterministic span restoration.

Why separate: segmentation quality controls all downstream grounding. It should not be mixed with detailed extraction.

Validation: anchor relocation, source order, overlap/gap policy, segment size warnings.

Cache key: source text hash, overview prompt hash, model identity, optional context pack hash.

### 2. Source Block And Concept Pass

Purpose: extract source blocks and salient concepts without prematurely building high-level structures.

Input: restored source region text, source coordinates, overview hints, optional context pack.

Output: `SourceSpan`, `SourceBlock`, `ConceptMention`.

Backend: LLM-backed, deterministic grounding validation.

Why separate: concept recognition and source block construction are shared foundations for every later view.

Validation: required fields, local IDs, exact/relocatable quotes, source ranges, concept source refs, type string shape, confidence.

Cache key: region text hash, prompt hash, model identity, overview result hash, optional context pack hash.

### 3. Logical Group Pass

Purpose: compose blocks and concepts into source-grounded meaning units.

Input: source blocks, concepts, local text windows.

Output: `LogicalGroup`.

Backend: LLM-backed.

Why separate: grouping requires interpretation, but should operate over stabilized source blocks and concepts.

Validation: group refs exist, cited blocks exist, non-contiguous blocks allowed, group type string accepted, group has grounding unless explicitly synthesis.

Cache key: source-block/concept result hash, prompt hash, model identity.

### 4. Link Pass

Purpose: identify relationships among concepts, blocks, and groups.

Input: source blocks, concepts, logical groups.

Output: `GroupLink`.

Backend: LLM-backed with deterministic ref validation.

Why separate: relation extraction benefits from stabilized groups and should not bloat the group prompt.

Validation: refs exist, link type string accepted, source-grounded links cite evidence blocks, synthesis links are marked.

Cache key: group result hash, prompt hash, model identity.

### 5. Unit Finalization Pass

Purpose: deduplicate local records, stabilize IDs, resolve local aliases, preserve provenance, and emit unresolved items.

Input: all region-level source blocks, concepts, groups, links, validation reports, repair hints.

Output: `ExtractionUnitPackage`.

Backend: LLM-backed plus deterministic cleanup.

Why separate: finalization is the only pass that should decide unit-level IDs and local merges.

Validation: unit-level ID uniqueness, ref integrity, duplicate handling, no evidence from prior context, unresolved ambiguity preservation.

Cache key: all prior pass result hashes, finalization prompt hash, model identity, optional context pack hash.

### 6. Deterministic Validation And Repair-Hint Pass

Purpose: produce local user-facing QC and concise LLM-native repair instructions.

Input: unit package and source text.

Output: full validation report, repair payload, enriched source locations.

Backend: deterministic.

Why separate: validation should be reproducible and should not depend on an LLM.

Validation: this is the validation layer.

Cache key: unit package hash, validation policy version, source text hash.

### 7. Optional Derived-View Pass

Purpose: build timeline, discourse graph, claim/evidence map, viewpoint evolution, theme map, or open-thread list from finalized records.

Input: finalized unit package.

Output: `DerivedStructure` records.

Backend: deterministic where possible, LLM-backed when synthesis is needed.

Why separate: derived views should never mutate core records.

Validation: input refs exist, view states `is_source_of_truth: false`, timeline DAG checks where applicable.

Cache key: unit package hash, view config hash, prompt hash if LLM-backed.

### 8. Cross-Unit Registry Delta Pass

Purpose: compare the unit package with prior document state and propose canonical concept updates, alias candidates, continuations, cross-unit links, and ambiguity items.

Input: unit package, selected context pack, base document snapshot.

Output: `RegistryDelta`.

Backend: LLM proposes, deterministic validator gates.

Why separate: applying document-level state requires transaction safety and should not be mixed with local unit extraction.

Validation: base snapshot hash, operation schema, evidence refs, no destructive auto-merge, no prior context as evidence.

Cache key: unit package hash, base snapshot hash, context pack hash, delta prompt hash, model identity.

## Derived Views

Derived views consume concepts, source blocks, logical groups, and links. They do not become source of truth.

Timeline:

- Built from event-like groups, temporal hints, and links such as `precedes`, `continues`, and `causes`.
- Stored as `DerivedStructure(view_type="timeline")`.
- May be absent when the unit has no meaningful temporal structure.

Discourse graph:

- Built from claim, argument, evidence, counterevidence, limitation, inference, and explanation groups.
- Uses links such as `supports`, `contradicts`, `qualifies`, `follows_from`, and `explains`.

Viewpoint evolution around concept X:

- Selects groups linked to a concept and orders them by source order, document structure, or temporal hints.
- Useful for novels, essays, news, papers, and notes.

Claim/evidence map:

- Connects claim-like groups to evidence, counterevidence, source statements, and limitations.
- Useful for papers, news, argumentative essays, and research notes.

Theme/concept map:

- Clusters motif, theme, description, example, comparison, and contrast groups.
- Useful for literary reading and essay analysis.

Unresolved question / open-thread list:

- Built from question, problem, unresolved issue, raises-question, answers-question, and resolves links.
- Replaces thread as a general downstream view.

## Cross-Unit Update And Registry Delta

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

Context packs should be selected and cache-keyed:

- Deterministic surface scan over canonical concepts and observed surfaces.
- Recent/high-salience concept summaries.
- Active unresolved questions or ambiguity items.
- Relevant cross-unit links and derived checkpoints.
- Selection report explaining every inclusion and exclusion category.

Large context windows should be used for periodic consolidation, alias merge audits, conflict resolution, rebuilding compact summaries, and long-range synthesis, not for dumping the whole registry into every routine unit extraction.

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
- Viewpoint evolution can be built from groups linked to a concept.
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

### Commit 1: Canonical Plan

- Add this document.
- Compact `PROGRESS.md` around the new direction.
- Remove stale extraction planning docs that describe timeline-first architecture.

### Commit 2: Reading Schema

- Add `tilusion/reading_schema.py`.
- Define stable outer envelopes, recommended type constants, confidence values, grounding values, and JSON helpers.
- Add tests for type-open schema behavior.

### Commit 3: Generalized Validation

- Add `tilusion/reading_validation.py`.
- Validate spans, blocks, concepts, groups, links, derived views, unit packages, and provenance.
- Add deterministic tests before LLM prompt changes.

### Commit 4: Prompt Resources

- Add source-block/concept, logical-group, link-structure, and unit-finalization prompts.
- Keep prompts externalized and versioned.
- Do not run expensive LLM calls yet.

### Commit 5: Payload And Prompt Composition

- Add `tilusion/reading_payloads.py` and `tilusion/reading_prompts.py`.
- Reuse or move `PromptPart` / `PromptComposition`.
- Add cache-key tests.

### Commit 6: Mock Backend

- Add mock responses for new reading tasks.
- Keep tests LLM-free.

### Commit 7: Reading Pipeline Orchestrator

- Add `tilusion/reading_pipeline.py`.
- Write artifacts for every pass.
- Produce `ExtractionUnitPackage`.

### Commit 8: Generalized Document Context

- Replace entity/location/thread/event/timeline registry with canonical concepts, group summaries, links, ambiguity queue, and derived checkpoints.
- Keep `context_injection.enabled`.
- Generalize deterministic surface scanner to canonical concepts and observed surfaces.

### Commit 9: Registry Delta And Transactions

- Add registry delta schema, deterministic validation, transaction logs, and snapshot writes.
- No destructive auto-merge.

### Commit 10: Derived Views

- Add timeline, discourse graph, claim/evidence map, theme map, viewpoint evolution, and open-thread list builders.
- Keep them downstream of unit packages.

### Commit 11: CLI Wiring

- Add or replace CLI commands for the new reading pipeline.
- Compatibility with old event/timeline commands is not required unless temporarily useful for comparison.

### Commit 12: LLM Trials

- Run mock first.
- Then run DeepSeek on unit-0002 and unit-0003 after schema, validation, cache, and artifact layout are stable.
