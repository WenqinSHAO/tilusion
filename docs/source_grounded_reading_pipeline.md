# Source-Grounded Reading Pipeline Plan

This is the current design direction for tilusion extraction. It supersedes the older event/timeline-centered extraction planning docs.

## Direction

tilusion should be a source-grounded reading workspace for books and long documents. It should work across novels, essays, papers, news, notes, and mixed texts.

The revised core pipeline is:

```text
source text
-> deterministic source blocks
-> per-segment concepts + atomic items over source blocks
-> unit-level concept unification + item stabilization
-> unit-level logical/thematic grouping
-> graph-shaped derived views such as timelines, discourse graphs, claim maps, and theme maps
-> cross-unit registry deltas
```

Timelines are useful, but they are one derived view. They are not the source of truth.

The immediate correction after the unit-0002 reading trial is to stop asking the LLM to invent the grounding layer. Source blocks are deterministic, reader-owned navigation units. The LLM cites source blocks and extracts structures over them.

## Design Principles

**Schema-light, type-open, source-grounded:**

- Keep the outer data envelopes stable.
- Allow `concept_type`, `item_type`, `group_type`, `view_type`, and graph edge types to be extensible strings.
- Start with short recommended type sets. The LLM uses these as a starter vocabulary; `other` and justified custom strings are accepted.
- Validate IDs, source-block refs, provenance, context use, and references strictly.
- Do not privilege people, locations, time expressions, events, or timelines as the only extraction model.
- Do not require every genre to use every type.

**Deterministic source blocks:**

- Source blocks are built before LLM extraction.
- A block may be a natural paragraph, a dialogue turn, a line, or a short sequence of sentences, depending on source layout and size.
- The splitter should prefer natural paragraphs when they are not too large, and split oversized paragraphs into sentence-ish blocks.
- Source blocks carry stable unit/segment coordinates and exact text.
- Source blocks do not contain back-references to extracted items. Avoid two-way references in the data model.
- Extracted atomic items reference source blocks with `source_block_refs`. That is the authoritative direction.

**No confidence as a core field:**

- The current pipeline does not operationally use confidence for merging, canonicalization, or validation.
- Keep explicit `uncertainty` and `review_notes` fields instead. They are easier for humans and later repair passes to act on.
- Add confidence later only if a concrete downstream decision uses it.

**Prior document context** is guidance for aliasing, continuity, duplicate detection, and retrieval. It is never evidence for facts in the current unit.

**KV cache reuse:** source text is stable across passes. The overview pass loads the full unit text; later unit-level passes can reuse that source text prefix. Per-segment extraction loads source blocks for one segment at a time.

## Core Data Model

The core unit package should stay small and one-directional:

```json
{
  "schema_version": "reading-unit-v0.3",
  "unit_id": "unit-0002",
  "source": {},
  "source_blocks": [],
  "concepts": [],
  "atomic_items": [],
  "logical_groups": [],
  "unresolved_items": [],
  "validation": {},
  "context_metadata": {}
}
```

### Recommended Type Values

Recommended `concept_type` values:

- `person`
- `group`
- `organization`
- `place`
- `object`
- `term`
- `method`
- `theme`
- `motif`
- `time_anchor`
- `emotion`
- `social_role`
- `institution`
- `symbol`
- `scene_element`
- `technical_component`
- `dataset`
- `metric`
- `source`
- `other`

Recommended `item_type` values:

- `event`
- `scene`
- `action`
- `claim`
- `argument`
- `statement`
- `observation`
- `description`
- `method`
- `technique`
- `result`
- `limitation`
- `habit`
- `question`
- `unresolved_issue`
- `definition`
- `example`
- `comparison`
- `contrast`
- `background`
- `note`
- `other`

Recommended `group_type` values:

- `timeline`
- `temporal_sequence`
- `theme_set`
- `concept_map`
- `discourse_graph`
- `claim_evidence_map`
- `viewpoint_evolution`
- `open_thread_list`
- `method_example_set`
- `motif_development`
- `contrast_set`
- `other`

Recommended graph edge types:

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

Recommended provenance values:

- `source_grounded`
- `synthesis`
- `deterministic`
- `llm_inferred`
- `user_corrected`

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

Source blocks are the grounding primitive. They should be deterministic, navigable, and reviewable in the source reader. They should not list concepts, atomic items, groups, or views that cite them.

UI note: when a user activates a source block in the future reader, the app can navigate to the atomic items and logical groups that cite it. That is a UI/query concern, not a reason to add reverse references to the stored block.

### Concept

`Concept` replaces both old per-segment `ConceptMention` and unit-level `UnitConcept`.

```json
{
  "concept_id": "concept-000001",
  "surface": "芸",
  "concept_type": "person|place|term|method|theme|time_anchor|other|custom",
  "canonical_name": "陈芸",
  "summary": "brief source-grounded note",
  "aliases": ["淑珍"],
  "alias_candidates": [],
  "observed_surfaces": ["芸", "陈芸", "淑珍"],
  "source_block_refs": ["block-0001"],
  "facets": ["behaves_like_person", "speaker"],
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded"
  }
}
```

Per-segment extraction may emit local concepts. Unit-level stabilization may merge repeated local concepts into one unit concept. Across units, the canonical registry may update aliases and summaries. This is the same conceptual object at different scopes, so the interface should not split it into separate mention and unit-concept models.

### AtomicItem

```json
{
  "item_id": "item-0001",
  "item_type": "event|scene|action|claim|argument|statement|observation|description|method|habit|question|other",
  "summary": "short source-grounded compression",
  "source_block_refs": ["block-0001", "block-0002"],
  "concept_refs": ["concept-000001"],
  "temporal_attributes": [
    {
      "kind": "explicit|implicit|relative|none",
      "surface": "乾隆庚子正月二十二日",
      "normalized_hint": "1780-02-26",
      "source_block_ref": "block-0001",
      "uncertainty": []
    }
  ],
  "attributes": {
    "argument_role": "claim|evidence|counterpoint|null",
    "narrative_role": "setup|turning_point|resolution|null",
    "salience": "high|medium|low"
  },
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded"
  }
}
```

Atomic items are the per-segment extraction product. They replace early `logical_groups` in the per-segment pass. They are not necessarily events.

### LogicalGroup

`LogicalGroup` is the general higher-level structure. It also replaces separate `DerivedStructure`; derived timelines, discourse graphs, theme maps, and claim maps are graph-shaped logical groups.

```json
{
  "group_id": "group-0001",
  "group_type": "timeline|theme_set|claim_evidence_map|discourse_graph|viewpoint_evolution|other",
  "summary": "unit-level grouping or view over related atomic items",
  "item_refs": ["item-0001", "item-0007"],
  "concept_refs": ["concept-000001"],
  "graph": {
    "nodes": [
      {
        "node_id": "node-0001",
        "item_ref": "item-0001",
        "label": "optional display label"
      }
    ],
    "edges": [
      {
        "source": "node-0001",
        "target": "node-0002",
        "edge_type": "precedes|supports|contradicts|elaborates|related_to|other",
        "summary": "brief explanation of the relationship",
        "source_block_refs": ["block-0003"],
        "provenance": {
          "grounding": "source_grounded|synthesis"
        },
        "uncertainty": []
      }
    ]
  },
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded|synthesis"
  }
}
```

A simple group can omit `graph` and behave like a stack/list of atomic items. A timeline is a `LogicalGroup` with `group_type: "timeline"` and a graph whose edges usually include `precedes`, `continues`, or `causes`. A discourse graph is the same base structure with argument-oriented edge types.

## Document State And Registry Delta

### DocumentStateSnapshot

```json
{
  "snapshot_id": "snapshot-...",
  "document_id": "doc-...",
  "canonical_concepts": [],
  "reusable_item_summaries": [],
  "reusable_group_summaries": [],
  "cross_unit_links": [],
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
  "operations": [
    {
      "operation_type": "new_concept|alias_candidate|merge_proposal|summary_update|logical_group_continuation|cross_unit_link|ambiguity_item|user_review_needed",
      "payload": {},
      "provenance": {}
    }
  ],
  "validation": {}
}
```

No raw LLM output should destructively mutate document state. It proposes a delta. Deterministic validation and, when needed, user approval apply the delta to a new snapshot.

## Pipeline Stages

### Stage 1: Overview / Segmentation

Purpose: understand unit structure, identify coarse regions, skip sparse/front-matter areas, and create extraction windows.

Input: reader unit text, reader metadata, optional compact context summary.

Output: restored source regions with anchor quotes and extraction hints.

Backend: LLM-backed, followed by deterministic segment restoration.

Why separate: the overview pass needs the whole unit and should decide reading windows before detailed extraction begins.

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

Output: local `Concept` and `AtomicItem` records.

Backend: LLM-backed, deterministic validation after.

This pass should stop at:

- concepts,
- atomic items,
- source-block-to-item mapping through `atomic_items[*].source_block_refs`,
- item attributes,
- temporal attributes when present.

This pass should not build:

- cross-segment concept unification,
- unit-level logical groups,
- dense link graphs,
- timelines,
- discourse graphs,
- theme maps.

### Stage 4: Unit-Level Stabilization And Concept Unification

Purpose: deduplicate and stabilize per-segment records.

Input: source blocks, local concepts, atomic items, validation reports.

Output: unit package with stable concept and item IDs.

Backend: deterministic ID/ref handling plus LLM-backed semantic deduplication when useful.

### Stage 5: Unit-Level Logical/Thematic Grouping

Purpose: form logical/thematic groups from stabilized atomic items.

Input: stable concepts and atomic items.

Output: `LogicalGroup` records, with optional graph structure.

Backend: LLM-backed with deterministic validation.

This is the right level for grouping scattered items across segments. It is also where event-like items can become timelines, claims can become claim/evidence maps, and motifs can become theme maps. These are group/view outputs over atomic items, not new sources of truth.

### Stage 6: Cross-Unit Registry Delta

Future stage. It should operate only after unit-level extraction is stable.

Input: finalized unit package and selected document context pack.

Output: validated registry delta against the current document snapshot.

Backend: LLM-backed proposal plus deterministic validation and transaction write.

## Current State Review

The current `cross-unit-refactor` branch has useful infrastructure, but parts remain tied to the older event/timeline-centered model and the first generalized reading trial showed quality regressions.

Strong parts to preserve:

- Explicit prompt composition through `PromptPart` and `PromptComposition`.
- Inspectable pass artifacts: prompt composition, system prompt, request payload, raw response, parsed result, validation report, validated result, and manifest.
- Deterministic evidence relocation and source-window reconstruction.
- Separation between local validation reports and concise LLM repair payloads.
- Context packs whose hashes are included in cache keys when context injection is true.
- Reader/index layer with stable unit IDs and source coordinate extraction.

Current concepts to rename or generalize:

- `evidence_spans` / `source_spans` -> deterministic `source_blocks` as the primary grounding layer.
- `entity_mentions` + `location_mentions` + `time_expressions` -> `concepts` with extensible `concept_type`.
- `atom_mentions` / `atom_records` -> `atomic_items`.
- `thread_candidates` / `thread_records` -> `logical_groups` such as open-thread lists, theme sets, or continuity groups.
- `timelines` -> `logical_groups` with `group_type: "timeline"` and graph edges.
- `book_context` -> document state/context pack with canonical concepts, compact item/group summaries, links, and ambiguity queues.

Current concepts to remove or decenter:

- Timeline construction as a required core pass.
- Fixed `entity/location/time/event/thread/timeline` top-level schema.
- Thread as a universal organizing container.
- Separate `DerivedStructure` as a parallel top-level model.
- Separate `ConceptMention` and `UnitConcept` schemas.
- Confidence as a required core field.

## Unit-0002 Reading Trial Review

A first LLM-backed reading-pipeline trial on `unit-0002` produced a valid-looking generalized package shape, but extraction quality regressed compared with the older timeline-centered pipeline.

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

**1. The grounding layer was too LLM-driven.**

The prompt asked the LLM to invent both `source_spans` and `source_blocks`. The result was verbose and inconsistent. Source blocks should be deterministic navigation units; the LLM should cite them.

**2. The per-segment pass was overloaded.**

The pass asked for spans, blocks, concepts, logical groups, and links at once. The output became paragraph-local and verbose. Per-segment extraction should stop at concepts and atomic items.

**3. Logical grouping happened too early and too weakly.**

Many groups were single-block or single-event groups. Meaningful logical/thematic grouping needs a unit-level view after cross-segment concept and item stabilization.

**4. Timeline reasoning was absent.**

The new pipeline had no derived-view/group pass yet. Timelines should return as `LogicalGroup` records with `group_type: "timeline"`, built from event-like atomic items and temporal attributes.

**5. Segment-local IDs corrupted final references.**

Per-segment extraction repeated local IDs such as `block-0001`, `group-0001`, and `link-0001`. Reindexing by raw ID can overwrite earlier segment records. The rewrite should scope local IDs by segment before flattening or avoid LLM-generated grounding IDs entirely.

## Fix Sequence

### Step 1: Make Source Blocks Deterministic

- Build source blocks from restored segment text using deterministic paragraph/line/sentence-ish splitting.
- Assign stable unit-level coordinates and block IDs before LLM extraction.
- Pass these blocks to the LLM and ask it to cite `source_block_refs`.
- Add coverage metrics: block count, covered chars, uncovered gaps, and average block size.

### Step 2: Change Per-Segment Extraction Shape

- Emit only local concepts and atomic items.
- Require atomic items to cite one or more deterministic source blocks.
- Allow temporal attributes on event-like or time-bearing items.
- Do not emit logical groups, dense links, timelines, or derived views at this stage.

### Step 3: Fix Cross-Segment ID Scoping And Reindexing

- Scope every segment-local ID with `segment_id` before flattening, or make the reindexer operate on `(segment_id, local_id)` pairs.
- Update all refs consistently: source block refs, concept refs, item refs, group graph node refs, and graph edge evidence refs.
- Add tests with two segments that both contain local `concept-0001` and `item-0001` to prove refs do not cross-wire.
- Treat a failed final validation report as a hard failure or make the CLI clearly report it.

### Step 4: Add Unit-Level Logical/Thematic Grouping

- Merge cross-segment concepts.
- Stabilize atomic items.
- Group related items into logical groups.
- Represent timeline, discourse, claim/evidence, theme, and open-thread views as logical groups with optional graphs.

### Step 5: Add Reading Quality Metrics

Add deterministic quality checks beyond schema validity:

- atomic item/source block ratio
- singleton group rate
- average source blocks per item
- unreferenced source block count
- concepts per block
- graph edges per group
- unresolved refs
- event-like item count
- event-like items with temporal hints
- missing timeline group when event-like temporal items are present and salient

These metrics should first be reported as warnings, not hard errors.

### Step 6: Then Tune Extraction Behavior

Only after the deterministic layer and output shape are correct:

- Tighten prompts so atomic items are compact source-grounded compressions.
- Add guidance for when multiple source blocks belong to one item.
- Add unit-level grouping guidance that actively merges related items while preserving ambiguity.
- Add graph guidance for timeline/discourse/theme structures without forcing irrelevant views.

## Validation And Tests

Required tests:

- Recommended concept types are accepted.
- Custom/open concept types are accepted and preserved.
- Recommended item/group/edge types are accepted.
- Custom/open item/group/edge types are accepted and preserved.
- Source-grounded concepts require source block refs.
- Source-grounded atomic items require source block refs.
- Graph edges with `source_grounded` provenance require source block refs.
- Synthesis graph edges must be marked as synthesis and cannot pretend to be direct evidence.
- One atomic item can cite non-contiguous source blocks.
- Multiple atomic items can share a source block.
- Timeline is a logical group/view over atomic items, not a core package field.
- Discourse graph is a logical group/view over atomic items, not a core package field.
- Registry deltas validate against a base snapshot hash.
- Prior context cannot appear in source refs for current-unit records.
- Merge proposals do not mutate canonical records unless applied by a validated transaction.
- Cache keys include context-pack hashes when context injection is enabled.
- Multi-pass provenance survives from source block to concept/item/group/unit package.

Likely modules:

- `tilusion/reading_schema.py`
- `tilusion/source_blocks.py`
- `tilusion/reading_validation.py`
- `tilusion/reading_prompts.py`
- `tilusion/reading_payloads.py`
- `tilusion/reading_pipeline.py`
- `tilusion/document_state.py`
- `tilusion/registry_delta.py`

## Implementation Sequence

### Commit 1: Canonical Plan

- Revise this document around deterministic source blocks, concepts, atomic items, and logical groups.
- Compact `PROGRESS.md` around the new direction.
- Remove or ignore stale extraction planning docs that duplicate this plan.

### Commit 2: Source Block Splitter And Schema

- Add deterministic source block construction.
- Define simplified dataclasses/schema for source blocks, concepts, atomic items, and logical groups.
- Add validation tests for the simplified model.

### Commit 3: Per-Segment Extraction Over Source Blocks

- Update prompt composition to provide source blocks.
- Update per-segment prompt to emit concepts and atomic items only.
- Cache prompt composition, request payload, raw response, parsed response, validation, and validated output.

### Commit 4: Unit Stabilization

- Reindex segment outputs with segment-scoped local IDs.
- Merge concepts within a unit.
- Stabilize item IDs and refs.
- Add tests for duplicate local IDs across segments.

### Commit 5: Unit Logical Grouping

- Add a unit-level grouping prompt/pass.
- Represent timelines, discourse graphs, theme maps, and open threads as logical groups with optional graphs.
- Validate graph refs and provenance.

### Commit 6: Cross-Unit Registry Delta

- Propose document-state deltas from finalized unit packages.
- Validate deltas before snapshot writes.
- Keep raw LLM proposals inspectable and non-destructive.
