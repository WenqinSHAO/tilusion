# Source-Grounded Reading Pipeline Plan

This is the current design direction for tilusion extraction. It supersedes the older event/timeline-centered extraction planning docs and the first reading-pipeline trial (reading-unit-v0.1).

## Direction

tilusion is a source-grounded reading workspace for books and long documents. It should work across novels, essays, papers, news, notes, and mixed texts.

The revised core pipeline is:

```text
source text
-> deterministic source blocks
-> per-segment concepts + atomic items over source blocks
-> unit-level concept unification + item stabilization
-> unit-level logical/thematic grouping (with optional graphs)
-> cross-unit registry deltas
```

Timelines, discourse graphs, claim maps, and theme maps are all logical groups — graph-shaped views over atomic items. They are not core package fields or separate top-level models.

The key correction after the unit-0002 reading trial: source blocks must be deterministic before LLM extraction begins. The LLM cites source blocks; it does not invent them.

## Design Principles

**Schema-light, type-open, source-grounded:**

- Keep the outer data envelopes stable.
- Allow `concept_type`, `item_type`, `group_type`, and graph edge types to be extensible strings.
- Start with short recommended type sets. The LLM uses these as a starter vocabulary; `other` and justified custom strings are accepted.
- Validate IDs, source-block refs, provenance, context use, and references strictly.
- Do not privilege people, locations, time expressions, events, or timelines as the only extraction model.
- Do not require every genre to use every type.

**Deterministic source blocks:**

- Source blocks are built before any LLM extraction.
- A block may be a natural paragraph, a dialogue turn, a line, or a short sequence of sentences, depending on source layout and size.
- The splitter prefers natural paragraphs when they are not too large, and splits oversized paragraphs into sentence-ish blocks.
- Source blocks carry stable unit/segment coordinates and exact text, verified by round-trip: `source_text[start:end] == text`.
- Source blocks do not contain back-references to extracted items. Extracted items reference source blocks with `source_block_refs`. That is the authoritative direction.

**No backward compatibility:**

- No consumers exist for reading-unit-v0.1. The schema is rebuilt from scratch as v0.3.
- Old extraction pipeline (`extraction*.py`) remains untouched as a working regression baseline.
- Reading modules (`reading_*.py`) are rewritten in-place.

**No confidence as a core field:**

- The pipeline does not operationally use confidence for merging, canonicalization, or validation.
- Keep explicit `uncertainty` and `review_notes` fields instead. They are easier for humans and later repair passes to act on.
- Add confidence later only if a concrete downstream decision uses it.

**Prior document context** is guidance for aliasing, continuity, duplicate detection, and retrieval. It is never evidence for facts in the current unit.

**KV cache reuse:** source text is stable across passes. The overview pass loads the full unit text; later unit-level passes can reuse that source text prefix. Per-segment extraction loads source blocks for one segment at a time.

## Core Data Model

The core unit package (schema version `reading-unit-v0.3`):

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
  "context_metadata": {},
  "metrics": {
    "validation": {},
    "counts": {}
  }
}
```

### What changed from v0.1

| v0.1 field | v0.3 | Reason |
|---|---|---|
| `source_spans` | **removed** | LLM-invented spans are redundant with deterministic source blocks |
| `source_blocks` | `source_blocks` | Now deterministic, with `start`/`end` offsets and exact `text` |
| `concept_mentions` | `concepts` | Single model for both per-segment and unit-level; no mention/concept split |
| `logical_groups` | `atomic_items` | Per-segment meaning units (events, claims, observations, etc.) |
| `links` | **removed** | Edges live inside `logical_groups[*].graph.edges` |
| `derived_views` | `logical_groups` | Unit-level views with optional graph structure |
| — | `unresolved_items` | Same as before |
| — | `validation` | Same as before |
| — | `context_metadata` | Same as before |

### SourceBlock

```json
{
  "block_id": "seg-0003-block-0001",
  "unit_id": "unit-0002",
  "segment_id": "seg-0003",
  "block_index": 0,
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

Block IDs are deterministic: `{segment_id}-block-{index:04d}`. They embed the segment scope so flattening never produces collisions.

Source blocks are the grounding primitive. They should be deterministic, navigable, and reviewable in the source reader. They do not list concepts, atomic items, or groups that cite them (that is a UI/query concern).

### Concept

Replaces both the old per-segment `ConceptMention` and unit-level `UnitConcept`. The same object shape is used at both scopes; unit-level stabilization merges local concepts into unit concepts.

```json
{
  "concept_id": "concept-000001",
  "surface": "芸",
  "concept_type": "person",
  "canonical_name": "陈芸",
  "summary": "brief source-grounded note",
  "aliases": ["淑珍"],
  "observed_surfaces": ["芸", "陈芸", "淑珍"],
  "source_block_refs": ["seg-0003-block-0001"],
  "facets": ["behaves_like_person", "speaker"],
  "uncertainty": [],
  "provenance": {
    "grounding": "source_grounded"
  }
}
```

### AtomicItem

Per-segment extraction product. Replaces the old `logical_groups` (which were per-segment meaning units like events, claims, observations).

```json
{
  "item_id": "item-0001",
  "item_type": "event|scene|action|claim|argument|statement|observation|description|method|habit|question|other",
  "summary": "short source-grounded compression",
  "source_block_refs": ["seg-0003-block-0001", "seg-0003-block-0002"],
  "concept_refs": ["concept-000001"],
  "temporal_attributes": [
    {
      "kind": "explicit|implicit|relative|none",
      "surface": "乾隆庚子正月二十二日",
      "normalized_hint": "1780-02-26",
      "source_block_ref": "seg-0003-block-0001",
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

Atomic items stop at source-grounded compressions. They do not build cross-segment concept unification, logical groups, dense link graphs, timelines, discourse graphs, or theme maps.

### LogicalGroup

Unit-level derived view over stabilized atomic items. Replaces the old `derived_views` and old timeline/thread construction passes.

```json
{
  "group_id": "group-0001",
  "group_type": "timeline|theme_set|claim_evidence_map|discourse_graph|viewpoint_evolution|open_thread_list|other",
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
        "source_block_refs": ["seg-0003-block-0005"],
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

**Cross-group references:** the same atomic item can appear as a node in multiple logical groups. Edges live within a group's graph and connect nodes within that group. If two items in different groups are related, that relationship is expressed by including both items in a shared group or by creating a cross-cutting group. There is no top-level `links` array — graphs are the only edge container.

A simple group can omit `graph` and behave like a stack/list of atomic items. A timeline is a `LogicalGroup` with `group_type: "timeline"` and a graph whose edges usually include `precedes`, `continues`, or `causes`. A discourse graph is the same base structure with argument-oriented edge types.

### Recommended Type Values

Recommended `concept_type` values:

- `person`, `group`, `organization`, `place`, `object`, `term`, `method`, `theme`, `motif`, `time_anchor`, `emotion`, `social_role`, `institution`, `symbol`, `scene_element`, `technical_component`, `dataset`, `metric`, `source`, `other`

Recommended `item_type` values:

- `event`, `scene`, `action`, `claim`, `argument`, `statement`, `observation`, `description`, `method`, `technique`, `result`, `limitation`, `habit`, `question`, `unresolved_issue`, `definition`, `example`, `comparison`, `contrast`, `background`, `note`, `other`

Recommended `group_type` values:

- `timeline`, `temporal_sequence`, `theme_set`, `concept_map`, `discourse_graph`, `claim_evidence_map`, `viewpoint_evolution`, `open_thread_list`, `method_example_set`, `motif_development`, `contrast_set`, `other`

Recommended graph edge types:

- `mentions`, `refers_to`, `aliases`, `same_as_candidate`, `part_of`, `elaborates`, `supports`, `contradicts`, `qualifies`, `contrasts`, `causes`, `enables`, `explains`, `follows_from`, `precedes`, `continues`, `resolves`, `raises_question`, `answers_question`, `exemplifies`, `defines`, `uses_method`, `produces_result`, `has_limitation`, `related_to`, `other`

Recommended provenance values:

- `source_grounded`, `synthesis`, `deterministic`, `llm_inferred`, `user_corrected`

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

Purpose: understand unit structure, identify coarse regions, classify segment types, and create extraction windows.

Input: reader unit text, reader metadata, optional compact context summary.

Output: restored source regions with anchor quotes, extraction hints, and a coarse region classification per segment (narrative, dialogue, expository, front-matter, table, sparse).

Backend: LLM-backed, followed by deterministic segment restoration.

Region classification lets the per-segment prompt adjust extraction strategy — e.g., dialogue-heavy segments produce more character concepts and action items, expository segments produce more term concepts and claim items, sparse segments may be skipped entirely.

### Stage 2: Deterministic Source Block Construction

Purpose: build the source-grounding layer before LLM extraction.

Input: restored segment text and unit-level segment offsets.

Output: deterministic `SourceBlock` records.

Backend: deterministic.

Splitter rules:

- Prefer natural paragraphs (split on `\n\n` or equivalent) when the resulting block is ≤ 800 chars.
- Split oversized paragraphs (> 800 chars) on sentence boundaries (`. `, `! `, `? `, `。`, etc.).
- Newline-separated short lines (dialogue, verse, lists) → `block_type: "line"`, one block per line.
- Blocks carry `start`/`end` character offsets relative to the full unit text.
- Every block must pass round-trip verification: `unit_text[block.start:block.end] == block.text`.
- Block IDs are deterministic: `{segment_id}-block-{index:04d}`, embedding segment scope to prevent collisions on flattening.
- Coverage check: every character in each extraction-worthy segment must be covered by exactly one block. Non-extraction segments (front matter, blank) may be excluded.
- Do not add back-references from source blocks to extracted objects.

Splitter API:

```python
def split_source_blocks(
    segment_text: str,
    *,
    segment_id: str,
    unit_id: str,
    unit_text: str,
    unit_offset: int,  # segment start position in unit_text
) -> list[SourceBlock]:
    ...
```

Quality metrics reported by the splitter:
- Block count per segment
- Coverage % (characters covered / total characters)
- Average block size
- Blocks flagged as oversized (still > 800 chars after sentence splitting — likely a wall of text)

### Stage 3: Per-Segment Concept And Atomic Item Extraction

Purpose: annotate deterministic source blocks with concepts and atomic items.

Input: source blocks for one segment, segment metadata (including region classification), optional context pack.

Output: local `Concept` and `AtomicItem` records.

Backend: LLM-backed, deterministic validation after.

This pass stops at concepts and atomic items. It does not build:
- cross-segment concept unification,
- unit-level logical groups,
- dense link graphs,
- timelines,
- discourse graphs,
- theme maps.

The prompt provides the deterministic source blocks and asks the LLM to cite them by `block_id`. The LLM does not invent blocks, spans, or grounding coordinates.

Per-segment prompt contract:
- Input: `unit_id`, `segment` (with region classification), `source_blocks` (the deterministic blocks for this segment), `text` (raw segment text), optional `context`.
- Output: `unit_id`, `segment_id`, `concepts[]`, `atomic_items[]`, `warnings[]`.
- Concepts must cite `source_block_refs` from the provided blocks.
- Atomic items must cite `source_block_refs` and may cite `concept_refs` using local concept IDs.
- Concept and item IDs are segment-local (e.g., `concept-0001`, `item-0001`). The reindexer scopes them with segment ID before flattening.

### Stage 4: Unit-Level Stabilization And Concept Unification

Purpose: deduplicate and stabilize per-segment records into unit-scoped IDs.

Input: source blocks, per-segment local concepts, atomic items, validation reports.

Output: unit package with stable concept and item IDs.

Backend: deterministic ID/ref handling for the first iteration; LLM-backed semantic deduplication can be added as a follow-up.

Steps:
1. Prefix every segment-local ID with `segment_id` to create unit-scoped IDs (e.g., `seg-0003-concept-0001`).
2. Update all refs consistently: `source_block_refs`, `concept_refs`, `item_refs`.
3. Merge concepts: exact surface match + same `concept_type` → merge into one concept with combined `source_block_refs` and `observed_surfaces`.
4. Reindex all records with sequential unit-level IDs for readability.
5. Validate all refs resolve.

The reindexer must be verified with a test where two segments both contain local `concept-0001` and `item-0001` — after reindexing, no refs should cross-wire.

### Stage 5: Unit-Level Logical/Thematic Grouping

Purpose: form logical/thematic groups from stabilized atomic items.

Input: stable concepts and atomic items, source blocks.

Output: `LogicalGroup` records, with optional graph structure.

Backend: LLM-backed with deterministic validation.

This is the right level for:
- Grouping scattered items into timelines, theme sets, discourse graphs, claim/evidence maps.
- Building graphs: nodes reference atomic items by `item_ref`, edges connect nodes within a group.
- The same atomic item can appear as a node in multiple groups (cross-group membership).

Validation checks:
- All `node.item_ref` values resolve to existing atomic items.
- All `edge.source` and `edge.target` values resolve to nodes within the same group.
- Edges with `source_grounded` provenance must cite `source_block_refs`.
- Synthesis edges must be marked as `synthesis`, not `source_grounded`.

### Stage 6: Cross-Unit Registry Delta

Future stage. It should operate only after unit-level extraction is stable.

Input: finalized unit package and selected document context pack.

Output: validated registry delta against the current document snapshot.

Backend: LLM-backed proposal plus deterministic validation and transaction write.

## What To Preserve From Current Codebase

These are well-designed and should be reused or adapted:

- **Prompt composition framework** (`extraction_prompts.py`): `PromptPart` and `PromptComposition` with YAML frontmatter resource loading. The reading pipeline already uses this via `reading_prompts.py`.
- **Pass artifact caching** (`extraction_pipeline.py`): `build_pass_cache_key`, `pass_artifact_paths`, `write_pass_artifacts`. Inspectable artifacts (prompt composition, system prompt, request payload, raw response, parsed result, validation report, manifest) per pass.
- **Reader/index layer** (`book_reader.py`): stable unit IDs, source coordinate extraction, unit text extraction. No changes needed.
- **Overview segmentation** (`extraction_pipeline.py`): `run_overview_segmentation_pass` and `resolve_overview_segments`. Needs a prompt update for region classification but the pass structure is sound.
- **Context pack hashing** (`book_context.py`): cache key isolation when context injection is enabled.

## What To Rewrite

These modules carry the v0.1 model and should be rewritten from scratch:

- `tilusion/reading_schema.py` — new dataclasses for v0.3 model, no SourceSpan/ConceptMention/GroupLink/DerivedStructure.
- `tilusion/reading_validation.py` — updated field checks, coverage metrics, graph edge validation.
- `tilusion/reading_prompts.py` — new prompt versions for per-segment (concepts + atomic items only) and unit-level grouping.
- `tilusion/reading_payloads.py` — payload builders aligned with new schema and prompt contracts.
- `tilusion/reading_pipeline.py` — updated pass functions and orchestrator for the new stages.

New module to create:

- `tilusion/source_blocks.py` — deterministic source block splitter with coverage verification.

## What To Leave Untouched

- `tilusion/extraction.py`, `extraction_pipeline.py`, `extraction_prompts.py`, `extraction_payloads.py`, `extraction_quality.py`, `extraction_unit_validation.py` — the old working pipeline stays as the regression baseline.
- `tilusion/book_reader.py` — stable.
- `tilusion/book_context.py` — needs eventual alignment with the new concept model, but not yet. Per-segment extraction works without context initially.

## Metrics (Factual Stage Telemetry)

Each stage reports factual counts as part of its cached output. The final unit package aggregates these counts under:

```json
"metrics": {
  "validation": {},
  "counts": {
    "overview": {},
    "per_segment": {},
    "segment_merge": {},
    "grouping": {}
  }
}
```

Metrics are not quality judgments. Validation checks structural correctness and only records validation counts under `metrics.validation`; it does not interpret thresholds such as "low density" or "weak grouping" at this stage.

Overview counts:
- Segment count requested by the overview pass
- Resolved segment count after deterministic reconstruction
- Unit character count

Per-segment counts:
- Source block count
- Concept count
- Atomic item count
- Source block references emitted by atomic items
- Simple ratios such as concepts/items per block and average source blocks per item
- Source block splitter counts for the segment, including coverage and oversized block data

Segment merge counts:
- Source block count
- Concepts before/after same-surface merge
- Concept merge count
- Atomic item count
- Unresolved item count
- Ambiguous surface count
- Propagated warning count

Grouping counts:
- Logical group count
- Singleton group count
- Group count with graph edges
- Graph edge count
- Atomic items grouped/ungrouped
- Event-like items with temporal hints
- Timeline or temporal-sequence group count

## Implementation Sequence

Each step is a focused, reviewable commit.

### Commit 1: Update plan and PROGRESS.md

- Revise this document with the v0.3 model, clarified naming, splitter spec, and updated commit sequence.
- Update PROGRESS.md to reflect the fresh start and current direction.

### Commit 2: Deterministic source block splitter

- New `tilusion/source_blocks.py` with `split_source_blocks()`.
- Paragraph/sentence splitting, dialogue/line handling, coverage verification.
- Tests: round-trip text fidelity, paragraph splitting, sentence splitting, empty/whitespace-only segments, coverage.
- Quality metrics: block count, coverage %, average block size, oversized block count.

### Commit 3: Rewrite reading schema (v0.3)

- Replace `tilusion/reading_schema.py` with new dataclasses: `SourceBlock`, `Concept`, `AtomicItem`, `LogicalGroup` (with graph nodes/edges), `ExtractionUnitPackage`.
- Remove: `SourceSpan`, `ConceptMention`, `CanonicalConcept`, `GroupLink`, `DerivedStructure`.
- Keep: `DocumentStateSnapshot`, `RegistryDelta`, `AmbiguityQueueItem`, `UserCorrectionOperation` (unchanged for now).
- Update recommended type sets and `schema_version` to `reading-unit-v0.3`.
- Tests: recommended and custom types accepted, serialization round-trips, removed fields rejected.

### Commit 4: Rewrite reading validation

- Replace `tilusion/reading_validation.py` for the new schema fields.
- Validate: package shape, source block coverage, concept/atomic item refs, logical group graph edges.
- Add provenance checks: source-grounded edges must cite source blocks, synthesis edges must be marked as such.
- Remove confidence validation (confidence is no longer a core field).
- Tests: valid package passes, missing fields fail, broken refs fail, synthesis edges without blocks flagged, source-grounded edges without blocks flagged.

### Commit 5: Rewrite per-segment extraction prompt

- New `tilusion/prompts/prompt_per_segment_extraction_v0.2.md`.
- Input provides deterministic source blocks. Output is only concepts and atomic items.
- Region classification guidance (dialogue → more character concepts, expository → more term/claim items, sparse → minimal output).
- Schema-light type guidance: recommended types as starter vocabulary, `other` and custom strings accepted.
- Tests: prompt resource loads, prompt composition builds, contract keys present.

### Commit 6: Rewrite per-segment pass (prompts, payloads, pipeline)

- Update `reading_prompts.py` for v0.2 prompt.
- Rewrite `reading_payloads.py` for the new per-segment contract.
- Update `reading_pipeline.py` per-segment pass to use deterministic source blocks as input.
- Update mock backend for new output shape.
- Tests: mock backend returns valid concepts + atomic items, validation passes, source_block_refs resolve.

### Commit 7: Segment-scoped ID reindexing

- Update `flatten_segment_results` to prefix local IDs with segment ID.
- Update all refs during flattening: `source_block_refs`, `concept_refs`, `item_refs`.
- Reindex to sequential unit-level IDs after flattening.
- Tests: two segments with same local IDs don't cross-wire, all refs resolve after reindexing.

### Commit 8: Unit-level concept unification and item stabilization

- Merge concepts by exact surface match + same `concept_type`.
- Stabilize item IDs.
- Generate unresolved items list for concepts with ambiguous surfaces.
- Tests: duplicate concepts merged, distinct concepts preserved, item refs remain valid after merge.

### Commit 9: Unit-level logical grouping prompt

- New `tilusion/prompts/prompt_unit_logical_grouping_v0.1.md`.
- Takes stabilized concepts and atomic items, outputs logical groups with optional graphs.
- Guidance for timeline construction from event-like items with temporal attributes.
- Guidance for discourse/claim graphs from claim/argument items.
- Do not force groups when items don't naturally cluster together.

### Commit 10: Unit-level logical grouping pass

- Add `run_unit_logical_grouping_pass` to `reading_pipeline.py`.
- Wire into orchestrator as stage 5.
- Validate graph refs and provenance.
- Update mock backend for grouping responses.
- Tests: valid groups pass validation, graph edges resolve, source-grounded edges cite blocks.

### Commit 11: Metrics wiring

- Wire source block splitter counts into per-segment pass artifacts.
- Wire per-segment counts into extraction pass output.
- Rename the segment merge helper to match the `metrics.counts.segment_merge` stage.
- Wire segment-merge counts into the merge helper output.
- Wire grouping counts into grouping pass output.
- Aggregate final factual telemetry under top-level `metrics.validation` and `metrics.counts`.
- Keep validation focused on structural correctness; do not turn metric thresholds into warnings yet.

### Commit 12: Update CLI

- Add `run-reading` command to `cli.py` that runs the full v0.3 source-grounded reading pipeline.
- Add `split-blocks` command to run just the deterministic source block splitter and inspect output as text or JSON.
- Keep old extraction commands working as regression baselines.
- Ensure mock `run-reading` uses reading-pipeline mock contracts instead of the old extraction mock backend.

### Future: Cross-unit registry delta

Only after unit-level extraction is stable on multiple real units.

## Validation And Tests

Tests are written alongside each implementation commit (see sequence above). Cross-cutting test principles:

- Recommended concept/item/group/edge types are accepted.
- Custom/open types are accepted and preserved.
- Source-grounded concepts require source block refs.
- Source-grounded atomic items require source block refs.
- Graph edges with `source_grounded` provenance require source block refs.
- Synthesis graph edges must be marked as synthesis, not source_grounded.
- One atomic item can cite non-contiguous source blocks.
- Multiple atomic items can share a source block.
- A timeline is a logical group over atomic items, not a core package field.
- A discourse graph is a logical group over atomic items, not a core package field.
- The same atomic item can appear in multiple logical groups.
- Registry deltas validate against a base snapshot hash.
- Prior context cannot appear in source refs for current-unit records.
- Merge proposals do not mutate canonical records unless applied by a validated transaction.
- Cache keys include context-pack hashes when context injection is enabled.
- Multi-pass provenance survives from source block to concept/item/group/unit package.

Modules after the rewrite:

- `tilusion/reading_schema.py`
- `tilusion/source_blocks.py`
- `tilusion/reading_validation.py`
- `tilusion/reading_prompts.py`
- `tilusion/reading_payloads.py`
- `tilusion/reading_pipeline.py`
- `tilusion/document_state.py` (future)
- `tilusion/registry_delta.py` (future)
