# Extraction Roadmap

This document records the current plan for the extraction side of the project.

The main decision is:

- treat this as an extraction and evidence-modeling problem first
- do not treat it as a dynamic ontology induction problem first
- do not expect one LLM call over a text block to produce usable structure

## Core Position

The tool needs an LLM-friendly structural index, but the real challenge starts after indexing:

- extracting grounded narrative structure from long text
- keeping every extracted object tied to source evidence
- growing from local structure to cross-book structure incrementally
- controlling token cost and error propagation

The system should not jump directly to:

- global timeline generation
- free-form ontology induction
- graph construction with weak provenance
- one-shot summarization pretending to be structure

Instead, the system should grow layer by layer, with each layer testable on its own.

## What The Reader Already Gives Us

The reader layer now gives us:

- structural units
- normalized unit IDs
- navigation metadata
- per-unit text extraction

That is enough to begin extraction in a controlled way.

Later modules should operate over reader units, not raw source files.

## Guiding Principles

### 1. Fixed Core Schema First

Start with a small hand-defined narrative schema.

Do not begin with open-ended ontology induction.

Initial extraction objects should be limited to:

- `entity_mention`: a character, role, organization, group, or concept mention, with possible alias candidates recorded separately
- `location_mention`: a physical, conceptual, or relative place mention, with enough context to disambiguate repeated names
- `event_mention`: a narrative occurrence, usually associated with time, location, participants, and evidence; granularity may vary by narrative context
- `time_expression`
- `temporal_claim`
- `alias_candidate`
- `thread_candidate`
- `evidence_span`

This is the extraction substrate.

It is intentionally smaller than the eventual canonical graph.

`thread_candidate` means a possible unresolved narrative line that the text opens, advances, complicates, or resolves.

Examples:

- an unanswered identity question
- an unresolved promise, threat, quest, or mystery
- an object whose importance is implied but not yet explained
- a prophecy, debt, accusation, disappearance, or hidden motive

It is called a candidate because early extraction should not claim that every such item is a durable plot thread. Later passes can confirm, merge, reject, or mark it resolved.

### 2. Local Extraction Before Global Reasoning

Extraction should begin at the smallest useful structural unit, usually a chapter or later a scene-like segment.

First extract:

- what entities are mentioned
- what locations are mentioned
- what events are described
- what temporal cues are present
- what unresolved threads are introduced or updated

Do not attempt cross-book resolution in the first pass.

Local extraction should not be context-blind extraction.

Each local pass should receive compact structured context from what has already been extracted and confirmed, such as:

- confirmed entities and aliases
- confirmed locations
- active unresolved threads
- recent event summaries where relevant
- known temporal constraints
- explicit frontier boundary

This prior state should be passed as structured data, not as a growing prose summary.

Even when a large chapter or book structure is given, it is useful to break it into smaller pieces while preserving the narrative boundary.

### 3. Evidence Is Mandatory

Every extracted object should point to source evidence, with location information from the original file.

No object should exist only as a model claim without:

- a supporting span
- a source unit ID, and later a more fine-grained span where possible
- enough local context for a human to inspect it later

Evidence quotes do not have to be byte-exact copies from the source if they can still be deterministically relocated.

Classical or annotated editions often insert inline note markers, spaces, or punctuation that an LLM may omit while still preserving the intended quote.

The acceptance rule should therefore be:

- exact quote match is best
- relaxed deterministic relocation is acceptable
- ambiguous relocation requires review or repair
- unrelocatable quotes are treated as likely invented or too distorted

Accepted evidence should store the pipeline-resolved source offsets, not only the LLM-provided quote string.

### 4. Corrections Must Beat Raw Model Confidence

The system should be designed so that:

- human correction overrides model output
- corrected objects are durable
- downstream views recompute from corrected state

This matters more than chasing raw extraction recall in early milestones.
The extraction model should be able to use accumulated book-specific knowledge to improve gradually.
Some of that knowledge comes from corrected extraction results. Some may come from gradually improving understanding of what the book is about.

### 5. Complexity Must Grow Only After Passing Local Gates

Do not add:

- event canonicalization
- temporal graph solving
- global entity merging
- schema induction

until local extraction quality is acceptable and inspectable.

## What Not To Build First

Do not start with:

- dynamic schema induction
- GraphRAG-style global graph summaries
- a graph database as the central problem
- a single “extract everything” prompt
- a giant event ontology

These may become useful later, but they are not the first bottleneck.

## Recommended Growth Order

## Phase 1: Grounded Local Extraction

Input:

- one reader structural unit at a time

Output:

- structured local mentions and claims with evidence

Required objects:

- entity mentions
- location mentions
- event mentions
- time expressions
- thread candidates

Rules:

- no cross-unit canonicalization
- no global timeline
- no inferred total order
- use compact confirmed prior state where available
- store output by pass name and schema version

Success criteria:

- output schema validates
- every object has evidence
- evidence quotes can be deterministically relocated in the source unit
- invalid or weak evidence can be repaired without rerunning unrelated work
- rerunning on the same input is reasonably stable
- token cost per unit is measurable and acceptable

### Phase 1 Implementation Pattern: Validate Before Trust

The first extraction pass should be treated as a draft, not as a trusted result.

The intended loop is:

1. Run segment extraction over the reader unit or chunk.
2. Run deterministic validation over the structured result.
3. If validation finds fixable problems, run a repair pass with the source text, first-pass result, and structured validation errors.
4. Re-run deterministic validation.
5. Accept, retry, downgrade invalid objects to warnings, or fail the pass based on explicit gates.

This is preferred over relying on prompt tuning alone.

LLMs are useful for semantic extraction, but deterministic code should enforce mechanical constraints.

Initial deterministic checks should include:

- every required top-level field exists and has the expected type
- response-local IDs are unique within each object class
- every cross-reference resolves
- every extractable object cites at least one evidence span
- every evidence quote is exact or can be relocated by deterministic relaxed matching
- ambiguous repeated or relaxed matches are flagged
- evidence spans are short enough to be inspectable
- entity and location surfaces appear in at least one cited evidence quote when applicable
- event, time, thread, and alias objects do not cite missing evidence IDs
- model output did not stop due to output truncation

Evidence relocation should run before repair.

Suggested relocation order:

1. Direct exact substring search.
2. Normalized search that tolerates whitespace differences.
3. Annotation-tolerant search that ignores inline note markers such as `[1]`.
4. Punctuation-tolerant search for common Chinese/English punctuation variants.
5. Punctuation-dropping search for recoverable wrapper punctuation drift, such as omitted Chinese quote marks around speech.
6. Source-window search using distinctive quote fragments.

Relocation outcomes should be explicit:

- `exact`: quote matched exactly once
- `relocated`: quote matched once after deterministic normalization
- `ambiguous`: quote matched multiple possible source spans
- `missing`: no plausible source span found

Only `missing` should be treated as a hard evidence failure by default.

`relocated` is acceptable for go-to-location review if the resolved source span is stored.

The repair pass should receive:

- original text or source windows around failed evidence
- first-pass structured result
- structured validation errors
- the same output schema

For small units, the repair pass can receive the full source unit.

For large units, prefer source windows around failed evidence and enough nearby context to repair locators and references.

The repair prompt should preserve valid objects where possible and fix only invalid or weak parts.

It should not use repair as an excuse to rewrite the whole extraction or introduce new global canonical records.

Important early gates:

- no unrelocatable evidence quotes
- no unresolved references
- no overlong evidence spans except explicitly allowed short segments
- no accepted object whose evidence is mechanically missing

If a result fails these gates after repair, the pipeline should fail explicitly or mark invalid objects as rejected instead of silently passing them downstream.

### Validation Report Audience Split

Current validation output mixes three audiences:

- local developer/debug inspection
- human review and UI navigation
- compact LLM repair input

These should be separated before adding the LLM repair pass.

Implemented artifact split:

- `validation_report.json`: full local report. Keep all evidence locations, relocation strategies, source windows, warnings, issue metadata, and computed offsets. This is for debugging and human/user inspection.
- `repair_hints.json`: compact LLM-facing payload. Include only actionable issues that need semantic correction, such as missing/ambiguous evidence, unresolved references, schema/type errors, and selected high-confidence warnings. Low-priority local warnings, such as surface grounding warnings that require human interpretation, are excluded by default.
- `validated_result.json`: enriched machine-facing result. Preserve the original extraction objects and add deterministic locator metadata, such as computed evidence `start`, `end`, `match_text`, and relocation status.

Clean evidence locations with computed `start` and `end` normally stay local.

They are useful for go-to-location review, UI navigation, downstream merge, and proving that evidence was not hallucinated.

They should not be sent to an LLM repair pass unless the repair task specifically asks the model to judge whether a located quote semantically supports an object.

The older `surface_not_in_cited_evidence` warning was too broad.

The validator should check reconstructed evidence context, not only the exact locator quote.

Current refinement:

1. If the surface appears in a cited evidence quote, pass.
2. If the surface appears in the resolved evidence window or paragraph around the cited quote, treat it as locator-supported and keep it local or low-priority.
3. If the surface appears elsewhere in the segment, warn that the object may need better cited evidence.

Surface matching allows conservative prefix/suffix support after normalization.

This avoids text-specific rules such as hard-coded relational prefixes while still accepting cases where the extracted surface includes a local possessor or relation but the source support contains the core surface.
4. If the surface is not found in the segment, flag it as likely hallucinated or unsupported.

This matters because some evidence quotes are being used as paragraph/scene locators, not as complete semantic support snippets.

In those cases, a source window can help a human inspect the object, but it should not automatically become LLM repair input.

The current `start_hint` and `end_hint` fields are weak deterministic anchors.

They are useful for human readability and for reminding the LLM where it thought the evidence came from, but deterministic reconstruction should rely on quote relocation.

Longer term, hints should be optional and secondary:

- primary locator: `quote`
- deterministic enrichment: computed `start`, `end`, `match_text`, relocation strategy
- optional human hint: one natural-language `hint`, or retained `start_hint`/`end_hint` only for display

### Validation Output Refinement

The three-artifact audience split is in place, but the chain-level validation report and repair hints still have gaps that make them harder to scan for humans, agents, and LLM repair passes.

Three problems identified in audit (2026-05-17):

**Gap 1 — No chain-level segment quality overview.** The chain validation report aggregates error/warning totals and includes full per-segment reports, but has no scannable per-segment summary. A human or agent cannot quickly answer: how many overview segments were proposed vs. resolved, which segments have the most problems, or what issue types dominate each segment. Unresolved overview segments (ambiguous or inverted anchors) are counted but not explained.

**Gap 2 — Duplicate relocation work between validation and resolution.** `validate_overview_structure()` calls `relocate_evidence_quote()` on every overview segment anchor for validation, and `resolve_overview_segments()` calls the same function again on the same anchors for actual resolution. The validation results are discarded rather than passed through.

**Gap 3 — Non-actionable warnings invisible in repair hints.** `build_chain_repair_hints()` only includes segments with `llm_actionable_issue_count > 0`. Surface grounding warnings are intentionally excluded from LLM repair input, but there is no summary of them in `repair_hints.json`. A segment that accumulates surface grounding warnings may have a real extraction problem that warrants attention, but the current output provides no signal.

Planned improvements:

**A — Add `segment_quality_overview` to chain validation report.** A new section in `build_chain_validation_report()` with: total vs. resolved vs. unresolved overview segment counts; unresolved reasons per segment; per-segment issue code breakdown; per-segment evidence relocation stats; dominant issue codes across the chain.

**B — Unify relocation: pass validated locations into resolution.** Have `validate_overview_structure()` return computed locations alongside the report dict. `resolve_overview_segments()` accepts pre-computed locations, eliminating the double call and ensuring resolution uses the same locations that were validated.

**C — Add non-actionable warning summary to repair hints.** Even when `llm_actionable_issue_count == 0`, include a `non_actionable_warning_summary` in `repair_hints.json` with total count, breakdown by code, and affected segment IDs.

All three are independent, additive changes touching `extraction_pipeline.py`, `extraction_quality.py`, and `cli.py`. No schema migration needed.

Proposals A, B, and C are now implemented (commits `69aab40`, `ac66822`, `9db21bb`).

### First LLM-Backed Chain Trial Findings (2026-05-17)

A `run-chain --backend deepseek` trial over `unit-0002` (浮生六记, ~15.7K chars) completed with the following results and observations.

#### Segment Restoration

All 6 overview segments resolved to character offsets with 56 exact and 25 relocated evidence spans. Zero ambiguous or missing anchors. The relocation logic handles Classical Chinese punctuation variations well — omitted quote marks, annotation-style whitespace, and wrapper punctuation drift are all recovered.

#### Validation Summary

- 0 errors, 16 warnings — chain passed.
- All 16 warnings are `surface_not_in_evidence_context` (non-actionable).
- `ready_for_llm_repair: false` — no LLM repair pass needed.

#### Warning Pattern Analysis

The `surface_not_in_evidence_context` warnings fall into two categories:

1. **Canonical names vs. narrative surface forms.** The LLM correctly identifies "沈复" (author name) and "陈芸" (full wife name) as canonical entities, but the Classical Chinese first-person narrative uses "余" (I) and "芸" (diminutive). The evidence paragraphs contain the narrative surface forms but not the canonical names. These are correct extractions with a surface-form mismatch between entity normalization and the evidence context.

2. **Short/high-frequency CJK characters.** Surfaces like "余", "母", "芸", "吴", "吾姊" appear throughout the full text but not necessarily within the specific evidence paragraphs the LLM selected. For CJK text, 1-2 character surfaces are particularly sensitive to this because they can appear as substrings of other words or be distributed across paragraph boundaries.

The validator's `containing_paragraph()` reconstructs evidence context from paragraphs containing the cited evidence spans. When a canonical name like "沈复" appears only in the preface/colophon while the narrative body uses "余", the evidence paragraphs correctly do not contain "沈复". The warning is accurate — the entity surface genuinely differs from the source form — but it does not indicate hallucination.

#### Extraction Quality Observations

- **Strengths:** Entity mentions include `alias_candidate_of` with confidence and rationale. Events cross-reference participant, location, and time mention IDs. Segment summaries are semantically coherent.
- **Evidence granularity is coarse:** Many evidence spans quote entire paragraphs (50-150+ chars) rather than the specific phrases supporting each claim. This dilutes evidence-to-claim precision and enlarges the evidence context window, which in turn makes surface grounding harder.
- **No cross-segment entity linking:** Each segment independently extracts entities. "沈复", "余", "芸", "陈芸" appear in multiple segments as separate mentions. The `alias_candidate_of` field works within segments but cross-segment linking is absent.
- **Overview caching works:** The overview pass hit cache; all 6 segment passes ran with fresh cache keys.

#### Proposed Improvements

These are ordered by impact and will be implemented over subsequent commits.

**1. Separate `canonical_name` from `surface` in the extraction schema.** The LLM should express "沈复" as `canonical_name` while keeping "余" as `surface` (the attested text form). This eliminates most `surface_not_in_evidence_context` warnings without losing information. The validator would ground `surface` in evidence context and treat `canonical_name` as an editorial normalization.

**2. Add evidence-granularity guidance to the segment extraction prompt.** The system prompt should prefer sentence-level or phrase-level evidence quotes over paragraph-level quotes. This tightens grounding and shrinks the evidence context window, making surface validation more precise.

**3. CJK-aware surface validation.** For segments where >50% of characters are CJK (Unicode ranges `一-鿿` etc.), single-character surfaces should skip the evidence-context grounding check. These are almost always valid pronouns, names, or common nouns that permeate the text.

**4. Add cross-segment entity aliasing to the chain-level validation report.** The chain-level QC should flag entity mentions with identical or subset surfaces across segments as candidate aliases. This gives downstream merging a head start without requiring a full cross-unit canonicalization pass.

**5. Revisit evidence context reconstruction for CJK.** `containing_paragraph()` splits on `\n\n`. Classical Chinese paragraphs can be hundreds of characters long with no blank-line breaks between semantic units. Consider sentence-boundary-based context windows (`。`, `！`, `？`) as an alternative or supplement to paragraph-based windows for CJK text.

### Second LLM-Backed Chain Trial: Follow-Up Issues (2026-05-17)

A later `run-chain --backend deepseek` trial over the same `unit-0002` cache (`b1c97fb4...`) used the v0.5 segment extraction prompt and restored all overview segments.

This run is a better stress test for finishing a full unit because it produced 14 finer segments instead of the earlier 6.

#### Current Status

- Overview proposed 14 segments; all 14 resolved to source offsets.
- Segment extraction completed for all 14 segments.
- Chain validation failed with 7 errors and 4 warnings.
- Evidence relocation summary across segment results: 96 exact, 44 relocated, 4 missing, 1 ambiguous.
- `repair_hints.ready_for_llm_repair` is true.
- Non-actionable warnings are separated: 3 `surface_not_in_evidence_context` warnings affecting segments `overview-segment-0005` and `overview-segment-0011`.

The failures are concentrated rather than systemic.

They do not block cross-segment planning work, but they are worth tracking because they define the kinds of defects a final repair/QC pair should be able to catch.

#### Segment Restoration Observations

Segment restoration is much improved: every overview anchor is locatable.

However, restored segments are not yet guaranteed to form a clean non-overlapping partition of the unit.

Observed issue:

- `overview-segment-0008` overlaps `overview-segment-0007` by 763 characters.

Most other gaps appear to be note/commentary gaps or boundaries between extracted narrative ranges.

The overlap is not fatal for local segment extraction, but it can create duplicate events, duplicated entities, or inconsistent summaries during merge.

This is a whole-unit QC concern.

#### Validation Issues Worth Patching Or Repairing

The current validator surfaced these actionable issue types:

- `evidence_quote_missing`: 4 cases. These are mostly LLM quote drift rather than hallucination. Examples include expanding source text from `亭在土山之巅` to `沧浪亭在土山之巅`, and using ellipsis-compressed quotes such as `明午，憨果至。……芸曰...`.
- `missing_evidence_refs`: 2 cases. Segment `overview-segment-0007` emitted entity mentions with empty `evidence_span_ids`.
- `evidence_quote_ambiguous`: 1 case. Segment `overview-segment-0010` used the quote `老妪`, which appears four times.
- `unresolved_evidence_ref`: 1 case. Segment `overview-segment-0013` included prose inside an evidence reference: `evidence-0004尾段? 实际在段落4...`.
- `surface_not_in_evidence_context`: 3 non-actionable cases. These remain useful human/QC signals but should not by themselves trigger local repair.

These issues are not structural blockers for the next cross-segment pass design.

They are exactly the kind of defects expected after first-pass segment extraction and should be handled by the repair/QC loop before accepting a unit as finished.

#### Quality Interpretation

The v0.5 prompt improved `surface` vs `canonical_name` behavior.

Most canonical names are now editorial normalizations while surfaces remain closer to the attested text form, for example `余` with `canonical_name: 沈复` and `芸` with `canonical_name: 陈芸` or `芸娘`.

The remaining failures are mostly mechanical:

- quotes are semantically correct but not directly relocatable
- some evidence references are missing or malformed
- very short evidence quotes are ambiguous
- segment overlap can duplicate downstream records

This suggests final unit completion should not rely only on local segment validation.

It needs at least one cross-segment merge/QC phase that checks overlap, duplicates, alias continuity, event continuity, unresolved local repair hints, and whether accepted outputs are still source-navigable.

### Long Unit Handling

Some reader units are too long and semantically dense for one detailed extraction call.

For example, `unit-0002` of the current Fu Sheng Liu Ji test text is about 15.7K characters, 42.8K UTF-8 bytes, 458 lines, and 224 non-empty paragraph-like blocks.

It covers many scenes and time jumps, so one detailed extraction pass tends to either over-compress events or produce brittle evidence.

For long units, prefer a multi-size pass loop:

1. Whole-unit overview pass.
2. Segment planning pass or deterministic chunking.
3. Chunk-level detailed extraction.
4. Chunk-level deterministic relocation and validation.
5. Targeted repair on failed chunks or failed evidence windows.
6. Whole-unit quality-control pass over merged chunk outputs.

The whole-unit overview pass should extract only coarse structure:

- major time anchors
- major locations
- main roles/entities
- broad event sequence
- thread candidates
- suggested segmentation boundaries

It should not try to produce final detailed evidence for every event.

Detailed evidence extraction belongs to smaller chunks.

Current implementation note:

- `run-pass` remains the one-pass baseline for comparison.
- `run-chain` runs an overview segmentation pass first, then resolves each segment's `start_quote` and `end_quote` back into character offsets inside the parent reader unit.
- The per-segment detailed extraction pass receives only the sliced segment text, not the entire parent unit.
- The chain manifest records source length and per-segment length stats so extraction density and segment size can be inspected before judging output quality.
- LLM repair, per-segment LLM review, and parent-unit QC are intentionally not part of the first chain implementation.

Chunk boundaries can be based on:

- paragraph groups
- time changes
- location changes
- major event transitions
- main role focus shifts
- LLM-suggested boundaries from the overview pass, if they can be mapped back to source offsets

The whole-unit quality-control pass should review merged chunk outputs for:

- duplicate events across chunks
- missing obvious transitions
- inconsistent aliases
- thread continuity errors
- temporal contradictions
- unbalanced extraction density

### Unit Completion Pass

The next extraction milestone is to finish extraction for one reader unit after local segment extraction has completed.

This should be treated as unit-level stabilization, not as timeline visualization or future-context retrieval.

End goal for a finished unit:

- local segment outputs are merged into a source-grounded unit extraction package
- obvious local validation errors are carried forward as repair targets or unresolved items
- duplicate entities, locations, events, and threads across segments are detected or merged
- aliases are explicit rather than silently collapsed
- event records are stable enough for a later timeline-construction task
- every accepted record remains navigable back to source evidence
- unresolved ambiguity is visible to a human reader and to later LLM passes

Expected artifacts:

- `unit_extraction.json`: unit-level structured extraction assembled from segment outputs.
- `unit_qc_report.json`: deterministic and LLM-facing QC findings, including unresolved repair hints, duplicate candidates, segment overlaps, alias conflicts, and source-navigation status.
- `unit_reader_view.md`: human-readable overview for inspecting major entities, events, unresolved threads, and source-linked quality concerns.

Out of scope for this branch:

- final timeline construction from events
- visualization-specific layout
- reusable context selection for future units or future incremental extractions

Those are downstream consumers of a clean unit extraction package.

#### Cross-Segment Finalization Inputs

Current implementation entry points:

- CLI: `python -m tilusion.cli finalize-unit <chain_cache_dir>`.
- Pass runner: `run_unit_finalization_pass()` in `tilusion/extraction_pipeline.py`.
- Context assembly: `build_unit_finalization_payload()` in `tilusion/extraction_payloads.py`.
- Segment compaction: `compact_segment_result()` in `tilusion/extraction_pipeline.py`.
- Static prompt: `tilusion/prompts/unit_finalization_v0.1.md`.

The unit finalization payload currently contains:

- unit metadata and source length stats
- overview segments and resolved source ranges
- compact segment quality overview
- repair hints
- compacted segment extraction results

Context economy becomes important here.

The first implementation passes compact segment objects, not the full source text.

The compacted objects still include local evidence, entity, location, event, time, thread, and warning arrays.

Later compaction can reduce this further by selecting only relevant fields, issue-linked windows, or high-value records.

`chain_validation` and `repair_hints` are related but should not carry the same details.

`chain_validation` should provide shape and distribution: how many segments were resolved, which segments have issues, and dominant issue types.

`repair_hints` should provide concrete actionable payloads: exact segment repair issues, unresolved evidence locations, and non-actionable warning summaries.

This keeps the finalization prompt cheaper than passing full validation reports twice.

#### Reference Scope

Merged unit records should use unit-scope IDs, for example `unit-entity-0001` and `unit-event-0001`.

Provenance references should stay segment-local:

- `mention_refs`: `{segment_id, mention_id}`
- `time_refs`: `{segment_id, time_expression_id}`
- `evidence_refs`: `{segment_id, evidence_id}`

This is cheaper and simpler for the LLM backend because it can reuse IDs already present in the input segment results.

It is also sufficient for deterministic navigation because source relocation is already computed and cached at segment scope in each segment `validated_result.json`.

Creating unit-scope IDs for every mention, time expression, and evidence span would require an additional mapping table without improving current source restoration.

#### Quality Notes Ownership

The LLM finalization output should use `quality_notes` for concise human-facing commentary.

It should not be the authority for deterministic readiness flags such as `ready_for_timeline`.

The deterministic pipeline should compute readiness later from concrete checks such as unresolved repair hints, duplicate events, broken refs, and segment overlaps.

This keeps model output useful for humans without asking the LLM to certify pipeline state.

#### Unit Finalization Responsibilities

The finalization pass should focus on:

- entity and location alias resolution across segments
- event deduplication and correction across overlapping or adjacent segments
- preserving or reporting local repair issues from segment validation
- marking ambiguous evidence or malformed references as unresolved rather than accepting them silently
- creating a compact reader-facing QC summary

It should not infer a fully ordered timeline.

It may preserve event order hints by segment order and local source order, but the timeline view should be a separate task consuming the stable event set.

It should not rewrite the entire extraction from scratch.

## Phase 2: Intra-Unit Grouping

Goal:

- group local mentions into coherent local narrative structure

Examples:

- attach participants to event mentions
- attach location to event mentions
- attach time expressions to event mentions
- split independent events within one unit

This is where “scene/event grouping” begins.

Still avoid:

- cross-unit entity merging
- global event deduplication

Success criteria:

- local event cards feel structurally plausible
- evidence still remains inspectable
- grouping errors can be reviewed and corrected

## Phase 3: Cross-Unit Canonicalization

Goal:

- link repeated local objects across units

Examples:

- same character across chapters
- same location referred to with aliases
- repeated references to the same story-world event

Output additions:

- canonical entities
- canonical locations
- possible duplicate-event candidates
- alias review items

Rules:

- preserve local mentions
- preserve uncertainty
- do not auto-merge aggressively

Success criteria:

- high-value merges are suggested
- false merges are limited
- review burden stays manageable

## Phase 4: Temporal Constraints

Goal:

- represent sequence without overcommitting

Represent only:

- before
- after
- same-time
- unknown
- flashback or backstory cue

Do not force:

- a total timeline
- one global linear order

Success criteria:

- local and cross-unit ordering claims are inspectable
- contradictions are surfaced rather than hidden
- unordered regions remain allowed

## Phase 5: Review And Correction Infrastructure

Goal:

- make imperfect extraction usable

Needed review surfaces:

- alias candidates
- duplicate events
- uncertain event grouping
- weak temporal links
- unresolved threads

This phase is not optional.

Without review, later structure becomes brittle and untrustworthy.

## Phase 6: Schema Growth

Only after the above is working:

- enrich entity categories
- enrich event types
- induce recurring role patterns
- cluster relation types

This is the point where ontology induction ideas become relevant.

Dynamic schema growth should decorate the stable core model, not replace it.

## Minimal Initial Schema Recommendation

The first extraction schema should stay compact.

Suggested initial records:

### `EvidenceSpan`

- `unit_id`
- `quote`
- `start_hint`
- `end_hint`

### `EntityMention`

- `mention_id`
- `surface_form`
- `entity_type_guess`
- `evidence_span_id`

### `LocationMention`

- `mention_id`
- `surface_form`
- `location_type_guess`
- `evidence_span_id`

### `EventMention`

- `event_id`
- `summary`
- `participant_mention_ids`
- `location_mention_ids`
- `time_expression_ids`
- `evidence_span_ids`

### `TimeExpression`

- `time_id`
- `surface_form`
- `interpretation`
- `evidence_span_id`

### `TemporalClaim`

- `claim_id`
- `left_event_id`
- `relation`
- `right_event_id`
- `confidence_band`
- `evidence_span_ids`

### `ThreadCandidate`

- `thread_id`
- `summary`
- `status_guess`
- `related_event_ids`
- `evidence_span_ids`

### `AliasCandidate`

- `candidate_id`
- `left_mention_id`
- `right_mention_id`
- `reason`
- `evidence_span_ids`

These can later feed canonicalization.

## Context And Pass Strategy

Extraction should be local in raw text scope, but aware of prior confirmed state.

That means a pass over `unit-0008` should not receive the whole book. It may receive:

- the text for `unit-0008`
- a compact list of confirmed entities and aliases so far
- active unresolved thread candidates
- recent canonical event summaries where needed
- relevant temporal constraints
- the structured outputs from earlier passes on the same unit

The goal is to improve extraction quality without turning every call into a whole-book prompt.

## Pass Dependency Model

Each pass should consume useful prior structured output instead of rediscovering everything from raw text.

Recommended dependency shape:

1. Local bundle extraction
2. Event grouping
3. Temporal claim extraction
4. Thread candidate extraction
5. Alias candidate generation
6. Parent-unit verification

`Local bundle extraction` should extract tightly coupled local objects together:

- entity mentions
- location mentions
- event mentions
- time expressions
- evidence spans

This pass benefits from seeing the local text once and keeping local coherence.

Focused later passes should consume the local bundle output:

- event grouping consumes entity, location, event, and time mentions
- temporal claim extraction consumes grouped event candidates and time expressions
- thread candidate extraction consumes event summaries, active thread state, and evidence spans
- alias candidate generation consumes entity mentions plus confirmed aliases
- parent-unit verification consumes chunk-level outputs and compact confirmed state

## Prompt Composition Strategy

The extraction system should not grow as one giant prompt.

As passes multiply, prompts should be assembled from reusable parts with explicit versions.

Useful prompt parts:

- task header: the specific job for this call
- shared extraction principles: grounding, uncertainty, local IDs, no final canonicalization
- output schema contract: exact JSON shape for the pass
- evidence policy: quote, relocation, and source-span expectations
- prior context block: confirmed entities, aliases, locations, threads, and recent events
- existing records block: already extracted records being refined or checked
- validation feedback block: deterministic issues and source windows for repair
- segmentation guide: known or proposed boundaries by time, location, event, or role focus
- repair instructions: preserve valid records, fix invalid records, return full corrected JSON
- review/QC instructions: inspect merged output for duplicates, gaps, contradictions, and uneven density

Prompt parts should be composable so that a repair pass can reuse the same schema and evidence policy as the extraction pass, while swapping the task header and adding validation feedback.

Prompt part versions should be part of cache keys.

This makes it possible to update evidence relocation guidance without invalidating unrelated context formatting, or to revise a QC prompt without rerunning first-pass extraction.

Current `run-chain` prompt composition:

- Overview pass system prompt is the static prompt part `overview-segmentation-contract` from `tilusion/prompts/overview_segmentation_v0.1.md`.
- Overview pass user payload contains `task`, `unit_id`, `unit`, and the full parent unit `text`.
- Segment extraction pass system prompt starts with the static `segment-extraction-contract` from `tilusion/prompts/segment_extraction_v0.4.md`.
- Segment extraction then appends one generated prompt part per segment with role `overview_extraction_hints`.
- The generated overview-hints part contains the segment title, summary, per-segment key entities, locations, time hints, event hints, and extraction hints from the overview result.
- Segment extraction user payload contains the synthetic segment unit metadata, compact `prior_context` derived from overview hints, and the sliced segment text only.
- Each pass writes `prompt_composition.json` and `system_prompt.md`; those are the first files to inspect when comparing prompt versions.

The likely prompt families are:

- overview and segmentation prompts
- detailed segment extraction prompts
- deterministic-error repair prompts
- merge and parent-unit QC prompts
- cross-unit alias and timeline review prompts

This also leaves room for later GEPA-like prompt evolution, because evolved parts can be evaluated and versioned independently.

### Leveraging LLM KV Cache Reuse

The prompt composition strategy above is not only about maintainability — it directly enables LLM backend KV cache reuse across passes.

Most LLM backends (DeepSeek, Claude, OpenAI) cache the key-value tensors for prompt prefixes. When a second request shares the same prefix bytes, the backend skips recomputing attention for that prefix. This is request-level acceleration, not file-based caching.

#### Prefix Sharing Opportunities

The extraction loop naturally produces shared prefixes:

**Static system prompts are the cheapest prefix.** The task contract and output schema are identical across many calls. The system prompt for segment extraction (`segment-extraction-contract`) is the same for every segment; only the generated overview-hints part differs. If the backend receives the same system prompt bytes for multiple segments in sequence, the KV cache for that prefix is reused.

**Full source text can be a shared user-payload prefix.** The overview pass already sends the complete unit text. If the repair pass or finalization pass also sends the full unit text as a prefix (followed by generated feedback or compacted results as a suffix), these passes benefit from the overview pass's KV cache. The tradeoff is token volume: the source text is the largest single payload component, but with KV cache hit the marginal compute cost is negligible.

**Segment text is not currently shareable across passes.** Each segment extraction pass sends a different source slice, so there is no natural prefix overlap between segment passes. Within a single segment, multi-round refinement (extract → validate → repair) reuses the same segment text prefix, making repair rounds cheaper than the initial extraction.

#### Multi-Round / Test-Time Scaling Patterns

Several patterns become practical once prompt parts are composable and prefixes are shareable:

**Repair loop (2 rounds).** Round 1 extracts segment data. Deterministic validation identifies actionable issues. Round 2 sends the same source text + system prompt (KV cache hit on both) with a repair task suffix and compact `repair_hints` feedback. The second round costs only the new suffix tokens plus the model's output tokens.

**Majority-vote or best-of-N (N parallel rounds).** Run the same extraction prompt N times with high temperature. Each response is parsed and validated independently. A lightweight merge step picks the most consistent output or flags disagreements. All N calls share the same prompt prefix, so KV cache reuse amortizes the prefix cost. This is useful when extraction ambiguity is high and the cost of a wrong merge exceeds the cost of extra inference.

**Branching at unit scope (1 + M rounds).** After segment extraction completes and the unit text prefix is cached:
- Round A: finalize unit (merge, alias, dedup, QC) — implemented
- Round B: construct timeline from stabilized event records
- Round C: generate cross-unit context summary for the next unit's `prior_context`

Rounds A/B/C share the same unit-text prefix and receive different task suffixes. If run in quick succession, each round hits KV cache on the prefix and only pays for the suffix + output.

**Incremental correction without rerunning.** When a human corrects one entity record in the unit extraction, only the affected segment's extraction cache is invalidated (via text hash change). The overview pass and other segments retain their cache hits. A re-run of `run-chain` only re-extracts the changed segment, and the finalization pass receives the updated segment result while reusing the cached overview and unchanged segments.

#### Current Gaps

| Capability | Status |
|-----------|--------|
| File-based SHA256 cache | Done |
| Cross-segment text-hash cache reuse | Done (`_build_segment_cache_map`) |
| Composible prompt parts with versioned cache keys | Done |
| Shared source text prefix across overview, repair, and finalization | Not wired — finalization sends compacted JSON, repair pass not yet implemented |
| Multi-round repair loop consuming `repair_hints` | `repair_hints` designed for this, loop runner not implemented |
| KV-cache-aware pass ordering in the pipeline | Not implemented — passes run independently, no prefix-sharing scheduler |
| Test-time majority voting or best-of-N | Not designed |

#### Near-Term Priorities

1. **Wire source text into the finalization payload as an optional prefix.** The finalization pass should receive the full unit text (or relevant source windows for unresolved evidence) so it can verify claims against evidence. If the overview pass just ran, the KV cache makes this nearly free.

2. **Implement the repair loop runner.** A `repair-segment` command or pipeline function that sends the source text + `repair_hints` payload to the LLM. The second round reuses the first round's prompt prefix.

3. **Pass ordering in `run-chain`.** After the overview pass caches the full unit text prefix, run segment extraction passes immediately (while the prefix is warm), then run the finalization pass (also reusing the prefix). Currently passes are independent and could be scheduled by a runner that groups same-prefix calls together.

## Segmentation Strategy

Neither extreme is ideal:

- very long input with one extraction type per pass gives continuity, but can be expensive and hard to inspect
- very short input with all extraction types in one pass gives local coherence, but can overload one prompt and make global verification weak

Use a hybrid strategy:

1. Start from reader structural units, usually chapters.
2. If a unit is too long, split it into scene-sized chunks using headings, paragraph boundaries, or conservative overlaps.
3. Run local bundle extraction on each chunk.
4. Run focused passes over structured chunk outputs.
5. Run a lightweight verification pass at the parent unit level.

The parent-unit verification pass should check for:

- duplicate local events across chunks
- missed chapter-level thread updates
- obvious temporal contradictions
- inconsistent entity naming

It should not rewrite the entire extraction result from scratch.

## Token Discipline

Token waste will become a serious problem if extraction is not decomposed.

Rules:

- one pass, one job
- use the smallest structural unit that still contains enough context
- prefer cheaper models for first-pass extraction
- escalate only ambiguous cases to stronger models
- cache outputs by:
  - source unit ID
  - source text hash
  - extraction task name
  - prompt version
  - schema version
  - compact context state hash
  - model/backend identity
- never rerun the whole book when only one frontier segment changed

Cache boundaries should be designed so that changing a temporal prompt does not invalidate entity mention extraction, and correcting one alias does not force every unrelated local pass to rerun.

## Evaluation Discipline

Every phase should have explicit checks.

Recommended measurements:

- schema validation pass rate
- evidence coverage rate
- extraction rerun stability
- human correction rate
- duplicate suggestion precision
- temporal contradiction frequency
- token cost per unit
- latency per unit

The most important early metric is not abstract accuracy.

It is:

- can the result be inspected
- can the result be corrected
- can later computation rely on it safely

### Manual LLM Backend Test Guide

Use the one-pass baseline first:

```bash
python -m tilusion.cli run-pass "./books/Fu Sheng Liu Ji --Zhong Hua Jing Dian Zhi - Miao Huai Ming Ping Zhu  Chen Fu Zhuan.txt" unit-0002 --backend deepseek
```

Then run the chained flow:

```bash
python -m tilusion.cli run-chain "./books/Fu Sheng Liu Ji --Zhong Hua Jing Dian Zhi - Miao Huai Ming Ping Zhu  Chen Fu Zhuan.txt" unit-0002 --backend deepseek
```

Both commands print a `cache_dir`.

To refresh validation artifacts for an existing chain cache without any LLM/backend call:

```bash
python -m tilusion.cli refresh-chain-validation .tilusion_cache/extraction_chains/<chain-cache-key>
```

Use this after validator-only changes.

It reads the cached overview and segment `result.json` plus their original `request_payload.json`, then rewrites validation reports, validated results, repair hints, and manifests.

It does not rerun segment resolution or create extraction results for newly resolvable overview segments.

That distinction matters because improving deterministic segment relocation can make a later `run-chain` resolve more overview segments than an older cache contains; those new segment passes would require fresh LLM extraction unless handled by a separate repair/resume command.

For `run-chain`, inspect these files:

- `chain_manifest.json`: full chain summary, source length, segment lengths, overview result, segment pass summaries, aggregate validation, and repair hints.
- `resolved_segments.json`: relocated segment boundaries, source offsets, segment length, and anchor relocation status.
- `overview/*/result.json`: raw parsed overview segmentation result.
- `overview/*/validation_report.json`: deterministic checks for segment anchors.
- `segments/<segment_id>/*/request_payload.json`: the exact sliced segment text sent to the LLM backend.
- `segments/<segment_id>/*/prompt_composition.json`: static and generated prompt parts used for that segment.
- `segments/<segment_id>/*/system_prompt.md`: the composed system prompt.
- `segments/<segment_id>/*/result.json`: parsed per-segment extraction result.
- `segments/<segment_id>/*/validation_report.json`: deterministic quality report for the per-segment result.
- `segments/<segment_id>/*/validated_result.json`: parsed extraction result enriched with deterministic source locations for evidence spans.
- `repair_hints.json`: compact repair payloads for segments with deterministic issues.

`repair_hints.json` is now intentionally narrower than `validation_report.json`.

Use it as the default input candidate for a later repair pass, but keep `validation_report.json` and `validated_result.json` local for debugging, navigation, and human review.

How to judge whether the chain improved:

- Compare `run-pass` output with `run-chain` segment outputs for extraction density and missed obvious events.
- Check whether overview segments are meaningful scene/topic/time/location ranges rather than arbitrary equal chunks.
- Check `resolved_segments.json` for segment sizes; very large segments mean the overview pass under-segmented, very tiny segments mean it over-segmented.
- Open each `request_payload.json` to confirm the detailed pass received the intended segment text.
- Use each segment `validation_report.json` to see whether evidence is exact, relocated, ambiguous, or missing.
- Use each segment `validated_result.json` to inspect deterministic source offsets without rereading the full local report.
- Use `repair_hints.json` to find deterministic issues ready for a later LLM repair pass.
- Treat surface grounding warnings as review targets, not automatic hallucination.

## Recommended LLM Workflow Pattern

The extraction path should likely use a multi-pass pattern such as:

1. local bundle extraction
2. local event grouping
3. temporal claim extraction
4. thread candidate extraction
5. alias candidate generation
6. parent-unit verification
7. cross-unit linking
8. review generation

Each pass should consume structured prior results where possible, not just raw text.

## Relation To Ontology Induction

Ontology induction is not rejected.

It is postponed.

Relevant later uses:

- discovering finer narrative role categories
- discovering recurring event types
- clustering relation labels
- refining the canonical graph schema

At that stage, tools and ideas like AutoSchemaKG may become useful references.

But that is a later-stage optimization over a functioning extraction substrate.

## Relation To External Systems

Useful references, but not first implementation targets:

- AutoSchemaKG: later-stage schema induction inspiration
- GraphRAG: later local/global graph navigation inspiration
- LangExtract: grounded structured extraction inspiration
- DocETL: decomposition and evaluation discipline inspiration

## Immediate Next Step

The next implementation step should be to define the first extraction milestone concretely:

- exact local extraction schema
- exact prompt/output contract
- exact evaluation checklist
- exact caching boundary
- exact failure handling

That milestone should be small enough to run on a few chapters and inspect manually before any cross-unit graph logic is added.
