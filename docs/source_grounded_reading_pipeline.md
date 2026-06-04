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
-> [NEW] cross-unit concept resolution (LLM: link/merge/split/refine/reclassify)
-> unit-level logical/thematic grouping (with optional graphs)
-> [NEW] cross-unit group resolution (LLM: continue/mutate/new_thread/cross_group_edge)
-> cross-unit registry deltas (deterministic + LLM proposals)
```

Timelines, discourse graphs, claim maps, and theme maps are all logical groups. They share the same `LogicalGroup` schema with an optional `graph` — there is no separate `Timeline` class, `DiscourseGraph` class, etc. The `logical_groups` array is a core package field; individual group types are differentiated by the `group_type` string and the edge types used in their graph, not by having their own top-level model.

The key correction after the unit-0002 reading trial: source blocks must be deterministic before LLM extraction begins. The LLM cites source blocks; it does not invent them.

Phase 3 (2026-05-31) added cross-unit LLM resolution: concept identity resolution against the book registry (Conversation D), revised unit grouping without concept deltas (Conversation C v0.2), and cross-unit group resolution (Conversation E). See `design/08_cross_unit_llm_merge.md`.

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
- Old event/timeline-centered extraction modules have been removed; source-grounded reading is now the active extraction path. Shared backend, overview, and pass-cache utilities live in dedicated modules.
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
    "validation_counts": {},
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

`facets` are machine-actionable behavioral/situational tags orthogonal to `concept_type`: `behaves_like_person`, `speaker`, `antagonist`, `unreliable_narrator`, `disputed_identity`, etc. They help deterministic concept merging disambiguate same-surface concepts in different roles, and help the grouping LLM understand character functions without parsing prose summaries. They are kept separate from `summary` (which is prose) so deterministic code can filter/group by facet without NL parsing.

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

The `attributes` dict uses `argument_role`, `narrative_role`, and `salience` as recommended keys. Additional keys beyond these are accepted — use any attribute that helps downstream grouping and graph-building (e.g., `emotional_valence`, `pov_character`, `tension_level`). The grouping LLM sees these attributes and can use them to form richer groups.

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

**Edge `source_block_refs`** attests to the edge inference itself — the text passage that supports the relationship claim (e.g., "and then," "the next day," a date expression linking two events). It is not a duplication of the source/target node source blocks. It answers: "where in the text does the LLM derive this relationship?" It is optional — purely structural edges (`related_to` without a specific passage) may omit it. In visualization, it enables "show me the passage supporting this edge" on click; in review, it lets a human verify edge fidelity.

**Edge provenance rules:**

| Condition | `grounding` |
|---|---|
| Edge has non-empty `source_block_refs` that directly support the relationship | `source_grounded` |
| Edge is inferred from structure, pattern, or cross-item reasoning without a direct textual statement | `synthesis` |

A `synthesis` edge may still carry `source_block_refs` — the LLM cited relevant passages but the relationship itself is an inference (e.g., two events both describe economic hardship → `related_to`).

**Group-level provenance rule:** `source_grounded` if **all** edges in the group's graph are `source_grounded`. `synthesis` if any edge is `synthesis`, or if the group itself is a constructed view (e.g., a `theme_set` pulling items from across the unit without direct textual connective tissue). Downstream: `source_grounded` groups get a verified badge in the reading UI; cross-unit group continuation prefers `source_grounded` groups as anchors.

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

## Book Registry And Registry Delta

### Snapshots

The BookRegistry (`tilusion/book_registry.py`) uses git to version `registry.json`. A snapshot is simply the registry state at a given commit SHA — no separate snapshot data structure is needed. `registry.save()` writes the current state as a git commit; `registry.rollback(sha)` restores a prior commit. The git log serves as the audit trail.

### RegistryDelta

`RegistryDeltaResult` (`tilusion/registry_delta.py`) is a sequence of BookRegistry API calls derived from LLM resolution proposals plus deterministic operations:

```json
{
  "unit_id": "unit-0003",
  "operations": [
    {"op_type": "merge_concepts", "unit_concept": {...}, "book_concept_id": "book-concept-0042", "match_reason": "llm_link_proposal"},
    {"op_type": "add_concept", "concept": {...}, "unit_id": "unit-0003"},
    {"op_type": "add_item", "item": {...}, "unit_id": "unit-0003"},
    {"op_type": "continue_group", "group": {...}, "book_group_id": "book-group-0017", "unit_id": "unit-0003"},
    {"op_type": "mutate_group", "group": {...}, "book_group_id": "book-group-0004", "unit_id": "unit-0003"}
  ],
  "ambiguity_items": [...],
  "id_remap": {"unit-concept-0001": "book-concept-0042"},
  "stats": {"merge_concepts": 1, "add_concept": 1, "add_item": 12, "add_group": 0, "continue_group": 1}
}
```

The flow is: LLM resolution proposals → `compute_registry_delta()` → `RegistryDeltaResult.operations` → `apply_registry_delta()` → BookRegistry method calls (`add_concept`, `merge_concepts`, `add_item`, `add_group`) → `registry.save()` (git commit). Each operation maps directly to a BookRegistry API call. The LLM proposes identity judgments; deterministic code validates and executes via the API. No raw LLM output mutates registry state directly.

## Pipeline Stages (Current — 8 stages, 5 LLM-backed)

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

### Stage 6: Cross-Unit Concept Resolution (NEW — Phase 3)

Purpose: resolve concept identity across units by comparing unit concepts against the book registry.

Input: merged unit concepts, compact registry index (built via `registry_index.py`), unresolved items, N-1 book digest. No unit text needed — concept summaries provide semantic context.

Output: `resolution_proposals` — `link` (cross-unit identity), `merge` (within-unit correction), `split`, `refine`, `reclassify`, `new_concept`. Each `link` may carry `implicit_refs` for items that reference the linked concept without naming it.

Backend: LLM-backed (Conversation D) with deterministic post-processing.

### Stage 7: Cross-Unit Group Resolution (NEW — Phase 3)

Purpose: determine how unit logical groups relate to book-level groups from prior units.

Input: resolved concepts, unit logical groups, registry group candidates (filtered by concept/item overlap via `select_group_candidates()`).

Output: `group_resolution_proposals` — `continue` (extend existing group), `mutate` (modify structure), `new_thread` (novel group), `cross_group_edge` (group-to-group relationship), `merge_groups` (retrospective merge of registry groups).

Backend: LLM-backed (Conversation E) with deterministic post-processing.

### Stage 8: Cross-Unit Registry Delta

Applies LLM resolution proposals and deterministic operations to the BookRegistry.

Input: finalized unit package, concept and group resolution proposals.

Output: validated registry delta (`RegistryDeltaResult` with operations, ambiguity items, ID remapping, stats). Applied via `apply_registry_delta()`.

Backend: deterministic — `registry_delta.py` with LLM proposals from steps 6-7.

Note: Step 6 runs for all units (including unit 1 with empty registry for within-unit corrections). Step 7 is skipped for unit 1 (no prior registry groups).

**LLM → BookRegistry API translation** happens in `compute_registry_delta()`. The translation is direct and reliable:

| LLM proposal type | RegistryDelta op_type | BookRegistry API call |
|---|---|---|
| `link` | `merge_concepts` | `registry.add_concept(force=True)` + `registry.merge_concepts([book_id, new_id])` |
| `new_concept` | `add_concept` | `registry.add_concept(concept)` |
| (deterministic) | `add_item` | `registry.add_item(item)` |
| `continue` | `continue_group` | `registry.add_group(group)` |
| `mutate` | `mutate_group` | `registry.add_group(group)` |

Concept and group operations flow through this path. Item-level deltas are currently deterministic-only (no LLM item merging). The LLM's `changes` field (canonical_name updates, new surfaces, summary refinements) is applied to unit concepts before they reach the registry, so the registry always receives well-formed `Concept` objects. If the LLM produces malformed `changes`, the `Concept` constructor or validation catches it.

The mapping is already BookRegistry API-friendly: the LLM proposes identity/continuation judgments, and `compute_registry_delta` + `apply_registry_delta` translate them into API calls deterministically. Each operation is a trackable record before any git commit.

### Registry Index And Dual-Signal Candidate Detection

`tilusion/registry_index.py` builds the compact registry concept index for LLM concept resolution and selects candidate concepts/groups:

- **`build_registry_index()`**: One-line-per-concept compact representation (concept_id, canonical_name, type, summary truncated to ~120 chars, observed_surfaces first 10).
- **`select_concept_candidates()`**: Hybrid candidate selection. When registry ≤50 concepts, returns the full index. When larger, unions deterministic pre-filter (surface collision + type family + canonical_name) with dual-signal retrieval (BM25 lexical + Qwen3-Embedding-0.6B semantic similarity + Reciprocal Rank Fusion).
- **`select_group_candidates()`**: Pre-filters registry groups by concept overlap with unit groups.

Current limitations and next iteration — groups lack a compact index and embedding-based shortlisting (only concepts have dual-signal). The single-pass LLM can't request more detail for ambiguous cases. See `design/09_agentic_registry_resolution.md` for the planned agentic multi-round approach with registry API tool calling and unified concept/group shortlisting.

The dual-signal approach uses Qwen3-Embedding-0.6B (Apache 2.0, 0.6B params, 32K context, 0.988 R@1 on ZH→EN cross-lingual retrieval) for semantic similarity. Degrades gracefully to BM25-only if the model is unavailable.

## What To Preserve From Current Codebase

These are well-designed and should be reused or adapted:

- **Prompt composition framework** (`extraction_prompts.py`): `PromptPart` and `PromptComposition` with YAML frontmatter resource loading. The reading pipeline already uses this via `reading_prompts.py`.
- **Pass artifact caching** (`pass_utils.py`): `build_pass_cache_key`, `pass_artifact_paths`, and JSON artifact path helpers. Inspectable artifacts (prompt composition, system prompt, request payload, raw response, parsed result, validation report, manifest) per pass.
- **Reader/index layer** (`book_reader.py`): stable unit IDs, source coordinate extraction, unit text extraction. No changes needed.
- **Overview segmentation** (`overview.py`): `run_overview_segmentation_pass` and `resolve_overview_segments`. The pass restores source regions, de-overlaps segments, and extends the final segment to the unit end for source completeness.
- **Context pack hashing** (`book_context.py`): cache key isolation when context injection is enabled.
- **BookRegistry** (`book_registry.py`): concept/item/group store with deterministic merge, collision detection, and git-backed persistence.
- **Registry delta** (`registry_delta.py`): `compute_registry_delta` and `apply_registry_delta` with LLM proposal support.
- **Registry index** (`registry_index.py`): compact concept index builder, dual-signal candidate detection (BM25 + Qwen3-Embedding-0.6B + RRF).
- **Conversation infrastructure** (`conversation.py`): `ConversationContext` + `TurnMetadata` for multi-turn LLM conversations.
- **Agentic repair** (`repair.py`): `run_agentic_pass()` with validation + repair loop.

## New Modules Created (Phase 2-3)

- `tilusion/source_blocks.py` — deterministic source block splitter with coverage verification.
- `tilusion/book_registry.py` — BookRegistry with deterministic merge and git-backed persistence.
- `tilusion/registry_delta.py` — deterministic diff of unit extraction against BookRegistry.
- `tilusion/registry_index.py` — compact concept index, dual-signal candidate selection.
- `tilusion/conversation.py` — multi-turn conversation context and turn metadata.
- `tilusion/book_digest.py` — book context digest generation and consumption.
- `tilusion/prompts/prompt_concept_resolution_v0.1.md` — Conversation D (cross-unit concept resolution).
- `tilusion/prompts/prompt_group_resolution_v0.1.md` — Conversation E (cross-unit group resolution).
- `tilusion/prompts/prompt_unit_grouping_v0.2.md` — Revised Conversation C (grouping without concept deltas).

## What To Leave Untouched

- `tilusion/backend.py`, `overview.py`, `pass_utils.py`, `extraction_prompts.py`, and `extraction_quality.py` — shared backend, overview, prompt, pass-cache, and legacy evidence-quality utilities used by the reading pipeline.
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
    "concept_resolution": {},
    "grouping": {},
    "group_resolution": {}
  }
}
```

Metrics are not quality judgments. Validation checks structural correctness and records aggregate counts under `metrics.validation_counts` (e.g., `{"errors": 0, "warnings": 2}`). The outer `validation` field on the unit package is the full `ReadingValidationReport` (issues list, pass/fail boolean) — the gating signal for downstream operations. `metrics.validation_counts` is the dashboard/telemetry summary of the same report; it does not interpret thresholds such as "low density" or "weak grouping."

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

Concept resolution counts (Phase 3):
- Registry index size
- Candidate count (deterministic + dual-signal)
- Resolution proposals by type (link, merge, split, refine, reclassify, new_concept)
- Implicit refs captured

Grouping counts:
- Logical group count
- Singleton group count
- Group count with graph edges
- Graph edge count
- Atomic items grouped/ungrouped
- Event-like items with temporal hints
- Timeline or temporal-sequence group count

Group resolution counts (Phase 3):
- Registry group candidate count
- Group resolution proposals by type (continue, mutate, new_thread, cross_group_edge, merge_groups)

## Implementation Status

### Done (Phase 1-3, 2026-05-30 through 2026-05-31)

1. Update plan and PROGRESS.md
2. Deterministic source block splitter (`tilusion/source_blocks.py`)
3. Rewrite reading schema v0.3 (`tilusion/reading_schema.py`)
4. Rewrite reading validation (`tilusion/reading_validation.py`)
5. Rewrite per-segment extraction prompt (v0.2)
6. Rewrite per-segment pass (prompts, payloads, pipeline)
7. Segment-scoped ID reindexing
8. Unit-level concept unification and item stabilization
9. Unit-level logical grouping prompt (v0.1 → v0.2)
10. Unit-level logical grouping pass
11. Metrics wiring
12. Update CLI
13. Quality cleanup (concept type normalization, post-delta dedupe)
14. BookRegistry + deterministic merge + git persistence (`book_registry.py`)
15. Registry delta compute/apply (`registry_delta.py`)
16. Multi-turn conversation infrastructure (`conversation.py`)
17. Agentic repair loop (`repair.py`)
18. Book digest as Conversation C turn (`book_digest.py`)
19. Overview pass digest ingestion
20. Cross-unit concept resolution — Conversation D (`registry_index.py`, prompt, pass)
21. Revised unit logical grouping v0.2 — no concept deltas
22. Cross-unit group resolution — Conversation E (prompt, pass)
23. Dual-signal candidate detection — BM25 + Qwen3-Embedding-0.6B + RRF
24. Pipeline wiring — TOTAL_STEPS=5
25. Tests — 356 passing (3 pre-existing validation failures deselected)

### Future

- LLM-driven concept summary refinement after deterministic merge
- Re-extraction when concept resolution reveals major missed links
- Item-level cross-unit linking
- User feedback / correction engine

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
