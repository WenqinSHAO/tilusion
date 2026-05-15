# 03 — Component Interactions

This document describes how components interact in the Novel Timeline Workbench.

It avoids exact API contracts and data schemas. The goal is to guide system behavior and implementation planning.

## MVP Operating Assumptions

For the first implementation phase:

- the tool runs locally;
- the primary interface is a CLI;
- only analyzed text up to the user-selected position is in play;
- hypothetical future exploration is out of scope.

## Interaction Overview

The system has four major layers:

```text
Book Layer
  Book Ingestion
  Structure Indexer
  Text Segment Store

Analysis Layer
  Frontier Manager
  Extraction Orchestrator
  Entity / Location / Event / Temporal / Thread Extractors
  Canonicalization Engine

Graph and Reasoning Layer
  Correction Engine
  Temporal Graph / Partial Order Engine
  Evidence Manager
  Review Queue Builder
  Change Impact / Diff Engine

Presentation Layer
  Derived View Builder
  UI Shell
  Structure Navigator
  Source Text Viewer
  Entity Registry
  Event Board
  Timeline UI
  Open Thread UI
  Review Queue UI
  Agent / Assistant Layer
```

## Flow 1 — Upload and Structure Recognition

### Trigger

User uploads a `.txt` or `.epub` file.

### Steps

1. UI Shell sends file to Book Ingestion.
2. Book Ingestion validates format.
3. Book Ingestion extracts raw text and metadata.
4. Text Segment Store persists normalized source text.
5. Structure Indexer builds the book outline.
6. Project State Manager creates initial project state.
7. Structure Navigator displays recognized structure.
8. User can inspect and correct structure if necessary.

### Result

The system has:

- raw source text;
- normalized source segments;
- a book structure index;
- chapter/section status set to `not analyzed`;
- no deep narrative analysis yet.

### Failure Cases

- EPUB has no useful TOC.
- TXT has ambiguous chapter headings.
- Encoding issues.
- Extremely long sections.

### MVP Handling

If structure detection fails, create fallback chunks and allow the user to proceed.

## Flow 2 — User Sets Analysis Frontier

### Trigger

User selects "Analyze through Chapter N" or gives equivalent natural-language command.

### Steps

1. Structure Navigator or Agent Layer receives frontier request.
2. Frontier Manager validates the selected unit.
3. Frontier Manager updates the current frontier.
4. Frontier Manager computes:
   - already analyzed units;
   - uncovered units up to frontier;
   - future units beyond frontier.
5. If uncovered units exist, Extraction Orchestrator is invoked.
6. If no uncovered units exist, Derived View Builder refreshes views from existing state.

### Result

The system either begins incremental analysis or directly visualizes the current graph.

### Important Rule

In reading-companion mode, future units beyond the frontier must not be used for extraction, summarization, answering, or inference.

For MVP, the frontier rule should be implemented as a simple hard boundary on analyzed text rather than a broader speculative timeline framework.

## Flow 3 — Incremental Analysis of Uncovered Units

### Trigger

Frontier Manager identifies uncovered units.

### Steps

1. Extraction Orchestrator requests text for uncovered units from Text Segment Store.
2. Extraction Orchestrator batches text by chapter/section/scene-size chunk.
3. Entity Extraction runs.
4. Location Extraction runs.
5. Event Extraction runs.
6. Temporal Claim Extraction runs.
7. Open Thread Extraction runs.
8. Evidence Manager attaches source spans and quotes.
9. Canonicalization Engine links mentions to existing canonical objects.
10. Canonicalization Engine creates new canonical objects as needed.
11. Temporal Graph Engine adds new temporal claims.
12. Review Queue Builder creates review items.
13. Derived View Builder regenerates impacted views.
14. Change Impact Engine creates an analysis change report.
15. UI displays updated timeline, registry, board, queues, and report.

### Result

The project state now includes newly extracted and linked structure up to the requested frontier.

### Key Design Constraint

Extraction should produce inspectable claims with evidence.

The system should avoid directly producing a final timeline with no supporting intermediate objects.

Structured extraction outputs should be validated and stored as raw extraction results before canonicalization updates project state.

## Flow 4 — Entity Merge Correction

### Trigger

User says or clicks:

- merge Entity A and Entity B;
- "The masked rider and Lady Ash are the same";
- confirm alias candidate.

### Steps

1. Entity Registry UI or Agent Layer sends merge request to Correction Engine.
2. Correction Engine records the operation.
3. Canonicalization Engine updates the entity cluster.
4. Mentions previously assigned to A or B are reassigned to merged entity.
5. Events involving A or B are marked affected.
6. Event duplicate candidates are recomputed for affected events.
7. Temporal claims touching affected events are re-evaluated.
8. Temporal Graph Engine recomputes impacted partial orders.
9. Review Queue Builder adds or resolves review items.
10. Derived View Builder regenerates affected timelines.
11. Change Impact Engine reports the effect of the merge.

### Result

The user sees a timeline diff and any newly discovered conflicts or duplicate events.

### Example Change Report

```text
Entity merge: Masked Rider + Lady Ash

Impact:
- 21 mentions reassigned.
- 8 events now involve the merged entity.
- 3 possible duplicate events found.
- 1 event auto-merged.
- 1 temporal conflict detected.
- Lady Ash's character timeline updated.
```

### Important Rule

Do not simply concatenate two character timelines.

Entity merge should combine affected events and solve the resulting temporal constraints.

## Flow 5 — Entity Split Correction

### Trigger

User identifies that one canonical entity incorrectly contains mentions from multiple characters.

### Steps

1. User selects mentions or source spans to split.
2. Correction Engine records split operation.
3. Canonicalization Engine creates or restores separate entity clusters.
4. Affected events are updated.
5. Temporal Graph Engine recomputes affected local timelines.
6. Review Queue Builder updates alias/identity review items.
7. Derived View Builder regenerates affected views.
8. Change Impact Engine reports the split impact.

### Result

Incorrectly merged entities are separated, and affected timelines are recalculated.

## Flow 6 — Event Merge Correction

### Trigger

User confirms that two event cards refer to the same story-world event.

### Steps

1. Event Board UI or Agent Layer sends merge request.
2. Correction Engine records operation.
3. Canonicalization Engine merges canonical events.
4. Evidence Manager preserves evidence from both event mentions.
5. Participants, locations, and temporal claims are reconciled.
6. Temporal Graph Engine recomputes impacted ordering.
7. Review Queue Builder resolves duplicate-event review item.
8. Derived View Builder refreshes event board and timeline.
9. Change Impact Engine reports changes.

### Important Principle

Merged events should preserve all source mentions, not discard evidence.

## Flow 7 — Event Split Correction

### Trigger

User determines an event card is too broad and should be split into multiple events.

### Steps

1. User selects event and requests split.
2. UI or Agent Layer collects proposed split boundaries or descriptions.
3. Correction Engine records split operation.
4. Canonicalization Engine creates separate canonical events.
5. Evidence Manager assigns relevant source spans to each event.
6. Temporal Graph Engine adds or asks for ordering constraints between split events.
7. Review Queue Builder may create temporal review items.
8. Derived View Builder updates timeline and event board.
9. Change Impact Engine reports result.

### MVP Simplification

If automatic split is difficult, allow manual event creation from selected source spans.

## Flow 8 — Event Order Correction

### Trigger

User sets:

- Event A before Event B;
- Event A after Event B;
- Event A same time as Event B;
- order between A and B is unknown.

### Steps

1. Timeline UI or Agent Layer sends temporal correction.
2. Correction Engine records operation.
3. Temporal Graph Engine adds, updates, or removes temporal claims.
4. Temporal Graph Engine checks for cycles or conflicts.
5. If conflict appears, Review Queue Builder creates a temporal conflict item.
6. Derived View Builder regenerates affected timelines.
7. Change Impact Engine reports affected ordering.

### Important Rule

The system should support unknown order as a valid correction.

The user should not be forced to choose a sequence when the text does not support one.

## Flow 9 — Advancing the Frontier

### Trigger

User selects a later chapter or section.

### Steps

1. Frontier Manager updates frontier.
2. Frontier Manager computes newly uncovered units.
3. Extraction Orchestrator analyzes only newly uncovered units.
4. Canonicalization Engine links new mentions/events to existing graph.
5. Temporal Graph Engine incorporates new temporal claims.
6. Open Thread Extraction updates thread states.
7. Review Queue Builder creates new review items.
8. Derived View Builder refreshes all affected views.
9. Change Impact Engine reports new and changed structure.

### Result

The user gets a compact update such as:

```text
New since Chapter 5:
- 4 new entities
- 2 alias candidates
- 1 new location
- 9 new events
- 5 temporal claims
- 3 open thread updates
- 1 possible contradiction
```

## Flow 10 — No Uncovered Parts

### Trigger

User asks to visualize or analyze up to a frontier that is already fully analyzed.

### Steps

1. Frontier Manager verifies no uncovered units.
2. Project State Manager checks whether views are stale.
3. If views are current, Derived View Builder returns existing views.
4. If views are stale, Derived View Builder rebuilds from canonical state.
5. UI shows timeline and review queues.

### Result

No unnecessary extraction runs occur.

## Flow 11 — Review Queue Resolution

### Trigger

User accepts, rejects, or defers a review item.

### Examples

- possible alias;
- duplicate event;
- uncertain temporal relation;
- possible flashback;
- unresolved thread update.

### Steps

1. Review Queue UI sends user decision.
2. Correction Engine records operation if the decision changes canonical state.
3. Relevant component applies the decision:
   - Canonicalization Engine for aliases and duplicates;
   - Temporal Graph Engine for ordering;
   - Open Thread component for thread updates.
4. Derived View Builder updates views.
5. Change Impact Engine reports effects.

### Result

The review queue shrinks, and canonical state becomes more stable.

## Flow 12 — Source Evidence Inspection

### Trigger

User clicks an event, entity, temporal claim, alias candidate, or thread update.

### Steps

1. UI requests source evidence from Evidence Manager.
2. Evidence Manager resolves source spans through Text Segment Store.
3. Source Text Viewer scrolls to the relevant passage.
4. Highlights are shown for entities, locations, events, and temporal cues.

### Result

User can verify why the system believes a structure exists.

## Flow 13 — Agent Command Handling

### Trigger

User issues natural-language command.

Examples:

- "Analyze up to Chapter 8."
- "Show Daren's timeline so far."
- "Merge the masked rider and Lady Ash."
- "Why is the ambush before the council meeting?"
- "Show unresolved identity candidates."
- "Mark these two events as unordered."

### Steps

1. Agent Layer parses user intent.
2. Agent Layer maps intent to structured operation or query.
3. If operation:
   - send to Correction Engine, Frontier Manager, or relevant component.
4. If query:
   - read from canonical graph and derived views.
5. Agent Layer responds with explanation, evidence, or action summary.
6. UI updates if state changed.

### Important Rule

The agent should not mutate project state directly.

It should call structured operations.

This same rule should apply to the CLI command layer so that CLI, assistant, and future web UI all share the same core operations.

## Flow 14 — Staleness and Rebuild

### Trigger

A correction changes canonical state in a way that invalidates derived views.

### Steps

1. Correction Engine records affected objects.
2. Project State Manager marks impacted views stale.
3. Derived View Builder rebuilds stale views on demand.
4. Change Impact Engine reports what changed.

### Example

Entity merge may mark these stale:

- two character timelines;
- event board filters;
- alias review queue;
- unresolved thread board;
- temporal graph component containing affected events.

## Flow 15 — Temporal Conflict Detection

### Trigger

New temporal claim or correction creates inconsistent ordering.

### Example

Existing claims:

- Event A before Event B.

New claim:

- Event B before Event A.

### Steps

1. Temporal Graph Engine detects a cycle or contradiction.
2. It creates a conflict object.
3. Review Queue Builder creates a temporal conflict review item.
4. Timeline UI displays conflict marker.
5. User can inspect evidence for competing claims.
6. User can resolve, reject, or mark ambiguity.

### Result

The system preserves conflict instead of silently choosing one claim.

## Recommended Execution Order for Coding Agent

A coding agent should build the MVP in this order:

### Phase 1 — Book and Structure Foundation

- file upload;
- TXT ingestion;
- basic EPUB ingestion;
- structure index;
- source segment store;
- structure navigator.

### Phase 2 — Frontier and Analysis Skeleton

- analysis frontier;
- uncovered unit detection;
- mocked extraction outputs;
- project state manager;
- status tracking.

The first milestone should prove this phase through a CLI flow before web work begins.

### Phase 3 — Extraction Pipeline

- entity extraction;
- location extraction;
- event extraction;
- temporal cue extraction;
- evidence attachment;
- basic canonicalization.

### Phase 4 — Core Views

- entity registry;
- event board;
- timeline-so-far;
- source text viewer;
- review queue.

### Phase 5 — Corrections

- merge entities;
- split entities;
- merge events;
- split events;
- set event order;
- mark unknown order;
- recompute derived views.

### Phase 6 — Incremental Frontier Advancement

- analyze newly uncovered units only;
- link to existing graph;
- change impact report;
- stale view handling.

### Phase 7 — Assistant Layer

- natural-language commands;
- query current structure;

## Development Bookkeeping Note

`PROGRESS.md` should be maintained as the concise human-and-agent implementation memory, with commit history carrying the more detailed reasoning when needed.
- trigger structured operations;
- explain evidence and timeline placement.

## Implementation Principle

Build the system so every important user-visible claim can answer:

1. What source text supports this?
2. Is it confirmed, inferred, suspected, or unresolved?
3. Which frontier made it visible?
4. Which user corrections affected it?
5. Which derived views depend on it?

This principle should guide all component interactions.
