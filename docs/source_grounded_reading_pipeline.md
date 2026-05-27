# Source-Grounded Reading Pipeline Plan

This is the current design direction for tilusion extraction. It supersedes the older event/timeline-centered extraction planning docs.

## Direction

tilusion should be a source-grounded reading workspace for books and long documents. It should work across novels, essays, papers, news, notes, and mixed texts.

The revised core pipeline is:

```text
source text
→ deterministic source blocks
→ per-segment concept mentions + atomic items over source blocks
→ unit-level concept unification + item stabilization
→ unit-level logical/thematic grouping
→ derived views such as timelines, discourse graphs, claim maps, and theme maps
→ cross-unit registry deltas
```

Timelines are useful, but they are one derived view. They are not the source of truth.

The immediate correction after the unit-0002 reading trial is to stop asking the LLM to invent the grounding layer. Source blocks are deterministic, reader-owned navigation units. The LLM cites source blocks and extracts structures over them.

## Design Principles

**Schema-light, type-open, source-grounded:**

- Keep the outer data envelopes stable.
- Allow `concept_type`, `item_type`, `group_type`, and `view_type` to be extensible strings.
- Start with short recommended type sets. The LLM uses these as a starter vocabulary; `other` and justified custom strings are always accepted.
- Validate IDs, source-block refs, provenance, confidence, and context use strictly.
- Do not privilege people, locations, time expressions, events, or timelines as the only extraction model.
- Do not require every genre to use every type.

**Deterministic source blocks:**

- Source blocks are built before LLM extraction.
- A block may be a natural paragraph, a dialogue turn, a line, or a short sequence of sentences, depending on source layout and size.
- The block splitter should prefer natural paragraphs when they are not too large, and split oversized paragraphs into sentence-ish blocks.
- Source blocks should carry stable unit/segment coordinates and exact text.
- Source blocks do not contain back-references to extracted items. Avoid two-way references in the data model.
- Extracted atomic items reference source blocks with `source_block_refs`. That is the authoritative direction.

**Prior document context** is guidance for aliasing, continuity, duplicate detection, and retrieval. It is never evidence for facts in the current unit.

**KV cache reuse:** source text is stable across passes. The overview pass loads the full unit text; later unit-level passes can reuse that source text prefix. Per-segment extraction loads source blocks for one segment at a time.

## Generalized Data Model

### Confidence values

`high`, `medium`, `low`, `unknown`

### Grounding / provenance values

`source_grounded`, `synthesis`, `deterministic`, `llm_inferred`, `user_corrected`

### Recommended concept_type values

`person`, `place`, `object`, `term`, `method`, `theme`, `motif`, `time_anchor`, `other`

### Recommended item_type values

`event`, `scene`, `action`, `claim`, `argument`, `statement`, `observation`, `description`, `method`, `habit`, `question`, `other`

### Recommended group_type values

`temporal_sequence`, `theme_set`, `claim_evidence_set`, `method_example_set`, `motif_development`, `open_question_set`, `contrast_set`, `other`

### Recommended view_type values

`timeline`, `discourse_graph`, `claim_evidence_map`, `theme_map`, `viewpoint_evolution`, `open_thread_list`, `other`

### SourceBlock

```json
{
  "block_id": "block-0001",
  "unit_id": "unit-0002",
  "segment_id": "overview-segment-0003",
  "block_index": 3,
  "block_type": "paragraph|sentence_group|line|dialogue|note|other",
  "start": 1200,
  "end": 1480,
  "text": "exact source text",
  "text_hash": "sha256...",
  "provenance": {
    "created_by": "deterministic",
    "splitter": "source-block-splitter-v0.1"
  }
}
```

Source blocks are the grounding primitive. They should be deterministic and navigable. They should not list concepts, atomic items, groups, or views that cite them.

### ConceptMention

```json
{
  "mention_id": "mention-0001",
  "surface": "exact source surface",
  "concept_type": "person|place|term|method|theme|time_anchor|other|custom",
  "canonical_name": "optional normalized name",
  "local_summary": "brief source-grounded note",
  "aliases_or_candidates": [],
  "source_block_refs": ["block-0001"],
  "confidence": "high|medium|low|unknown",
  "facets": ["behaves_like_person", "speaker", "time_anchor"],
  "uncertainty": []
}
```

### AtomicItem

```json
{
  "item_id": "item-0001",
  "item_type": "event|scene|action|claim|argument|statement|observation|description|method|habit|question|other",
  "summary": "short source-grounded compression",
  "source_block_refs": ["block-0001", "block-0002"],
  "concept_refs": ["mention-0001"],
  "temporal_attributes": [
    {
      "kind": "explicit|implicit|relative|none",
      "surface": "乾隆庚子正月二十二日",
      "normalized_hint": "1780-02-26",
      "source_block_ref": "block-0001",
      "confidence": "high",
      "uncertainty": []
    }
  ],
  "attributes": {
    "argument_role": "claim|evidence|counterpoint|null",
    "narrative_role": "setup|turning_point|resolution|null",
    "salience": "high|medium|low"
  },
  "confidence": "high|medium|low|unknown",
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded"
  }
}
```

Atomic items are the per-segment extraction product. They replace early `logical_groups` in the per-segment pass. They are not necessarily events.

### UnitConcept

```json
{
  "concept_id": "concept-000001",
  "canonical_name": "陈芸",
  "concept_types": ["person"],
  "facets": ["behaves_like_person"],
  "aliases": ["芸", "淑珍"],
  "observed_surfaces": [],
  "summary": "compact unit-level summary",
  "mention_refs": ["mention-0001"],
  "confidence": "high|medium|low|unknown",
  "alias_candidates": [],
  "merge_split_uncertainty": []
}
```

### LogicalGroup

```json
{
  "group_id": "group-0001",
  "group_type": "temporal_sequence|theme_set|claim_evidence_set|method_example_set|other",
  "summary": "unit-level grouping of related atomic items",
  "item_refs": ["item-0001", "item-0007"],
  "concept_refs": ["concept-000001"],
  "confidence": "high|medium|low|unknown",
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded|synthesis"
  }
}
```

Logical groups are unit-level structures. They should usually be built after cross-segment item stabilization, not in the first per-segment extraction pass.

### DerivedStructure

```json
{
  "view_id": "view-0001",
  "view_type": "timeline|discourse_graph|claim_evidence_map|theme_map|viewpoint_evolution|open_thread_list",
  "input_item_refs": [],
  "input_group_refs": [],
  "structure": {},
  "confidence": "high|medium|low|unknown",
  "generated_by": "deterministic|llm",
  "is_source_of_truth": false
}
```

### ExtractionUnitPackage

```json
{
  "schema_version": "reading-unit-v0.2",
  "unit_id": "unit-0002",
  "source": {},
  "source_blocks": [],
  "concept_mentions": [],
  "atomic_items": [],
  "unit_concepts": [],
  "logical_groups": [],
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
  "reusable_item_summaries": [],
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

## Pipeline Stages (Revised)

### Stage 1: Overview / Segmentation

Purpose: understand unit structure, identify coarse regions, skip sparse/front-matter areas, and create extraction windows.

Input: reader unit text, reader metadata, optional compact context summary.

Output: restored source regions with anchor quotes and extraction hints.

Backend: LLM-backed, followed by deterministic span restoration.

### Stage 2: Deterministic Source Block Construction

Purpose: build the source-grounding layer before LLM extraction.

Input: restored segment text and unit-level segment offsets.

Output: deterministic `SourceBlock` records.

Backend: deterministic.

Rules:

- Prefer natural paragraphs when appropriate.
- Split oversized paragraphs into sentence-ish groups.
- Preserve exact text and unit-level character offsets.
- Do not add back-references from source blocks to extracted objects.

### Stage 3: Per-Segment Concept And Atomic Item Extraction

Purpose: annotate deterministic source blocks with concepts and atomic items.

Input: source blocks for one segment, segment metadata, optional context pack.

Output: `ConceptMention` and `AtomicItem` records.

Backend: LLM-backed, deterministic validation after.

This pass should stop at:

- concept mentions,
- source-block-to-item mapping through `atomic_items[*].source_block_refs`,
- item attributes,
- temporal attributes when present.

This pass should not build:

- cross-segment concept unification,
- unit-level logical groups,
- dense links,
- timelines,
- discourse graphs,
- theme maps.

### Stage 4: Unit-Level Stabilization And Concept Unification

Purpose: deduplicate and stabilize per-segment records.

Input: source blocks, concept mentions, atomic items, validation reports.

Output: unit package with stabilized item IDs and unit concepts.

Backend: LLM-backed plus deterministic ID/ref handling.

### Stage 5: Unit-Level Logical/Thematic Grouping

Purpose: form logical/thematic groups from stabilized atomic items.

Input: unit concepts and atomic items.

Output: `LogicalGroup` records.

Backend: LLM-backed with deterministic validation.

This is the right level for grouping scattered items across segments.

### Stage 6: Derived Views

Purpose: build timelines, discourse graphs, claim/evidence maps, theme maps, viewpoint evolution, or open-thread lists from stabilized records.

Input: unit concepts, atomic items, logical groups, temporal attributes.

Output: `DerivedStructure` records.

Backend: deterministic where possible, LLM-backed when synthesis is needed.

Timeline is a special derived view over event-like atomic items with explicit or inferable temporal attributes. It should reuse the old successful timeline practices: partial order, DAG validation, no forced total order, and no unnecessary timelines.

### Stage 7: Cross-Unit Registry Delta

Future stage. It should operate only after unit-level extraction is stable.

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


## Unit-0002 Reading Trial Review

A first LLM-backed reading-pipeline trial on `unit-0002` produced a valid-looking generalized package shape, but the extraction quality regressed compared with the older timeline-centered pipeline.

Artifacts reviewed:

- New reading package: `.tilusion_cache/reading_passes/units/unit-0002/unit_package.json`
- Previous finalization reference: `.tilusion_cache/extraction_chains/b1c97fb428677d02d4401f47ace5bc2d1559c0ba9375c8d68fad54f4497aa497/unit_finalization/344977836ae24da4f34be672ad2c21d0b51c7693d752326cef1f48a31768a452/result.json`
- Previous timeline references: later `unit_timeline` / `unit_timeline_repair` artifacts under the same old chain cache.

Observed new package shape:

- `source_spans`: 193
- `source_blocks`: 78
- `concept_mentions`: 169
- `logical_groups`: 71
- `links`: 84
- `derived_views`: 0
- validation: failed with 7 unresolved `logical_groups[*].link_refs` references.

### What Went Wrong

**1. Timeline reasoning is absent, not merely weaker.**

The new pipeline currently runs overview, per-segment extraction, and finalization only. Derived views are documented as downstream and optional, but no derived-view pass exists yet. Therefore timeline reasoning and timeline construction are completely missing from the new output. This is an implementation gap, not evidence that the generalized reading model cannot support timelines.

The older reference path supplied for comparison is itself only unit finalization and does not contain final timelines. The useful timeline behavior lived in subsequent `unit_timeline` and `unit_timeline_repair` artifacts, which built 43 event records into timeline views. A fair comparison must include a new derived timeline view built from logical groups.

**2. Finalization has a deterministic ID/reference bug.**

Per-segment extraction outputs repeat local IDs such as `block-0001`, `group-0001`, and `link-0001` across segments. The current deterministic reindexing maps by raw local ID only, so later segment IDs overwrite earlier segment IDs. This corrupts references after reindexing.

The current result also leaves stale `logical_groups[*].link_refs` pointing to old link IDs, producing validation errors. This is a correctness bug that should be fixed before prompt tuning or additional LLM trials.

**3. Source block extraction is too LLM-driven.**

The prompt asks the LLM to invent both `source_spans` and `source_blocks`. The result has many source spans and blocks, with block construction varying by segment. Source blocks should be a deterministic navigation layer wherever possible. The LLM should cite source blocks and optionally propose smaller evidence spans, not be responsible for creating the whole block layer.

**4. The all-in-one per-segment pass is overloaded.**

The simplified pass asks for source spans, blocks, concepts, logical groups, and links in one response. This is efficient, but the output suggests the LLM is doing mostly paragraph-local extraction instead of higher-level semantic grouping. Many logical groups are single-block or single-event groups, and finalization does not merge them enough.

**5. Finalization is not doing semantic finalization.**

The finalization response mostly preserves or concatenates per-segment records. It does not sufficiently deduplicate concepts, merge cross-segment groups, compress related events/observations, or prepare derived views. The deterministic reindexer then normalizes IDs but cannot create semantic quality.

## Fix Sequence After Unit-0002 Review

The next work should prioritize deterministic correctness before prompt tuning.

### Step 1: Fix Deterministic ID Scoping And Reindexing

- Scope every segment-local ID with `segment_id` before flattening, or make the reindexer operate on `(segment_id, local_id)` pairs.
- Update all refs consistently: span refs, block refs, concept refs, group refs, link refs, source-order hints, evidence refs, and derived-view refs when present.
- Update `logical_groups[*].link_refs`; do not leave stale link IDs after reindexing.
- Add tests with two segments that both contain `block-0001`, `group-0001`, and `link-0001` to prove refs do not cross-wire.
- Treat a failed final validation report as a hard failure or at least make the CLI clearly report it.

### Step 2: Make Source Blocks Deterministic

- Build source blocks from restored segment text using deterministic paragraph/line/sentence-ish splitting.
- Assign stable unit-level coordinates and block IDs before LLM extraction.
- Pass these blocks to the LLM and ask it to cite `source_block_refs`.
- Keep `source_spans` as smaller evidence spans or quotes when useful, but do not let the LLM define the primary navigation block layer.
- Add coverage metrics: block count, covered chars, uncovered gaps, and average block size.

### Step 3: Add Reading Quality Metrics

Add deterministic quality checks beyond schema validity:

- group/block ratio
- singleton group rate
- average source blocks per group
- unreferenced source block count
- concepts per block
- links per group
- unresolved link refs
- event-like group count
- event-like groups with temporal hints
- derived timeline absent when event-like temporal groups exist

These metrics should first be reported as warnings, not hard errors.

### Step 4: Restore Timeline As A Derived View

- Add a derived timeline pass that consumes finalized `logical_groups`, `links`, and temporal hints.
- Only event-like groups need timeline placement.
- Store the result under `derived_views` with `view_type: timeline` and `is_source_of_truth: false`.
- Reuse the old timeline validation ideas: DAG checks, phantom refs, self-loop detection, missing event-like groups when expected, and no forced total ordering.

### Step 5: Then Tune Extraction Behavior

Only after the deterministic layer is correct:

- Reconsider whether per-segment extraction should remain one pass or split into source-block/concept extraction followed by logical grouping/linking.
- Tighten prompts so logical groups are meaning units, not one group per paragraph or one group per surface event.
- Add guidance for when to merge blocks into one logical group and when not to.
- Add finalization instructions that actively deduplicate concepts and merge cross-segment logical groups while preserving ambiguity.

### Current Priority

Do not tune the LLM prompt first. The next commit should change source-block ownership: deterministic source blocks first, then per-segment extraction over those blocks. Segment-scoped ID/ref fixes should follow after the new per-segment output shape is stable.

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
