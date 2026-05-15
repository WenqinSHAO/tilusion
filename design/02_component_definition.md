# 02 — Component Definition

This document defines major components for the Novel Timeline Workbench.

It intentionally avoids detailed data interfaces and database schemas. The goal is to give a coding agent enough architectural structure to plan implementation.

## MVP Delivery Constraints

Implementation should assume:

- local-first execution;
- single-user workflow;
- CLI-first interface for the first usable milestone.

The web UI should be treated as a later presentation layer once core ingestion, frontier control, extraction, correction, and derived-view logic are stable enough.

## Component Map

Major components:

1. Book Ingestion
2. Structure Indexer
3. Project State Manager
4. Frontier Manager
5. Text Segment Store
6. Extraction Orchestrator
7. Entity Extraction Component
8. Location Extraction Component
9. Event Extraction Component
10. Temporal Claim Extraction Component
11. Open Thread Extraction Component
12. Canonicalization Engine
13. Correction Engine
14. Temporal Graph / Partial Order Engine
15. Derived View Builder
16. Review Queue Builder
17. Change Impact / Diff Engine
18. Evidence Manager
19. UI Shell
20. Source Text Viewer
21. Structure Navigator
22. Entity Registry UI
23. Event Board UI
24. Timeline UI
25. Open Thread UI
26. Review Queue UI
27. Agent / Assistant Layer
28. Persistence Layer

## 1. Book Ingestion

### Responsibility

Accept uploaded `.txt` and `.epub` files and convert them into normalized internal text resources.

### Handles

- file upload;
- format detection;
- EPUB unpacking;
- TXT decoding;
- metadata extraction where possible;
- initial raw text storage;
- error reporting for unsupported or malformed files.

### MVP Notes

TXT and EPUB only.

For EPUB, preserve as much source structure as possible:

- table of contents;
- spine order;
- chapter hrefs;
- HTML anchors;
- headings.

For TXT, preserve:

- line numbers;
- paragraph boundaries;
- character offsets.

## 2. Structure Indexer

### Responsibility

Build a navigable structure index from the uploaded book.

### Handles

- EPUB TOC parsing;
- EPUB spine fallback;
- TXT chapter heading detection;
- paragraph numbering;
- source range mapping;
- fallback chunking;
- status assignment per unit.

### Output Concept

A hierarchical outline of the book:

- book;
- parts;
- chapters;
- sections;
- paragraphs or chunks.

Each unit should know whether it is analyzed, partially analyzed, stale, or not analyzed.

## 3. Project State Manager

### Responsibility

Maintain the state of a book analysis project.

### Handles

- current book;
- current frontier;
- analyzed units;
- extraction outputs;
- canonical graph;
- user corrections;
- derived views;
- operation history;
- version snapshots where needed.

### Important Principle

Project state should distinguish between raw extraction results and user-confirmed or canonical structures.

It should also preserve user correction operations as a separate durable layer rather than folding them into silent state mutation.

## 4. Frontier Manager

### Responsibility

Manage the user-selected analysis boundary.

### Handles

- setting the frontier;
- advancing the frontier;
- computing uncovered units;
- preventing accidental analysis beyond the frontier in reading-companion mode;
- reporting current progress.

### Example Behaviors

If current frontier is Chapter 3 and user selects Chapter 6:

- detect Chapter 4 through Chapter 6 as uncovered;
- queue these units for analysis;
- keep future chapters hidden.

## 5. Text Segment Store

### Responsibility

Store and retrieve normalized text segments for analysis and evidence display.

### Handles

- chapter text;
- section text;
- paragraph text;
- source span references;
- character offsets;
- mapping between UI selections and source spans.

### Importance

Every extracted object should be traceable back to source text.

## 6. Extraction Orchestrator

### Responsibility

Coordinate extraction passes over newly uncovered text.

### Handles

- batching units;
- calling specialized extractors;
- retrying failed extractions;
- validating output shape;
- routing extraction outputs into canonicalization;
- creating review items for uncertain results.

### MVP Strategy

Use a multi-pass pipeline rather than one monolithic LLM call.

Suggested passes:

1. entity and location mention extraction;
2. scene/event mention extraction;
3. temporal cue extraction;
4. open thread extraction;
5. linking and canonicalization;
6. review queue generation.

### LLM Integration Constraint

The LLM backend should be used to produce structured extraction candidates that are validated before entering canonical state.

For current planning, assume DeepSeek `v4 pro` and `flash` models are available through environment variable `DS_API_KEY`.

## 7. Entity Extraction Component

### Responsibility

Extract entity mentions from text.

### Handles

- character names;
- titles;
- nicknames;
- descriptions;
- pronouns where locally resolvable;
- group entities such as families, armies, orders, kingdoms;
- possible aliases.

### MVP Notes

Do not require perfect entity resolution.

Focus on:

- mention detection;
- local evidence;
- candidate alias generation;
- uncertainty flags.

## 8. Location Extraction Component

### Responsibility

Extract location mentions and candidate canonical locations.

### Handles

- named places;
- rooms/buildings/cities/regions;
- relative places such as "the palace" or "the northern road";
- location aliases;
- event-location associations.

### MVP Notes

Location hierarchy can be shallow in v1.

Example:

- palace;
- throne room;
- capital;
- northern road.

## 9. Event Extraction Component

### Responsibility

Extract event mentions from text.

### Handles

- scene-level events;
- participant attachment;
- location attachment;
- source evidence;
- event summaries;
- event granularity hints;
- possible duplicate events.

### MVP Event Granularity

Use scene-level events by default.

Avoid extracting every micro-action unless the user asks for high detail.

## 10. Temporal Claim Extraction Component

### Responsibility

Extract temporal information and event-ordering claims.

### Handles

- explicit dates if present;
- relative time phrases;
- before/after relations;
- same-time or meanwhile relations;
- flashback indicators;
- uncertain order;
- backstory cues.

### Important Principle

Temporal output should be a set of claims or constraints, not a forced total order.

## 11. Open Thread Extraction Component

### Responsibility

Extract unresolved narrative threads and update them over time.

### Handles

- mysteries;
- unresolved goals;
- unanswered identity questions;
- pending promises/threats;
- unresolved conflicts;
- later resolutions.

### Example Threads

- identity of the masked rider;
- reason for the prince's exile;
- meaning of the silver seal;
- who betrayed the city.

## 12. Canonicalization Engine

### Responsibility

Convert raw mentions into canonical entities, locations, and events.

### Handles

- linking mentions to existing canonical objects;
- creating new canonical objects;
- proposing alias candidates;
- detecting possible duplicate events;
- preserving uncertainty;
- escalating ambiguous decisions to review queues.

### Important Distinction

Event mention and canonical event are not the same.

Entity mention and canonical entity are not the same.

The tool must preserve this distinction.

## 13. Correction Engine

### Responsibility

Apply user corrections as durable structured operations.

### Handles

- merging entities;
- splitting entities;
- confirming aliases;
- rejecting aliases;
- merging events;
- splitting events;
- setting event order;
- marking order unknown;
- setting same-time relation;
- correcting event participants;
- correcting locations;
- rejecting extractions.

### Important Principle

Corrections should be operation records, not silent destructive edits.

This enables undo, diffing, and explainability.

## 14. Temporal Graph / Partial Order Engine

### Responsibility

Maintain and solve temporal constraints among events.

### Handles

- partial order graph;
- event ordering constraints;
- unknown relative order;
- simultaneous events;
- sync points across character/location tracks;
- temporal conflict detection;
- cycle detection;
- unresolved temporal buckets.

### Important Principle

The engine should not force all events into a single linear sequence.

It should support:

- parallel threads;
- disconnected components;
- local timelines;
- unknown order;
- contradictions.

For MVP, this engine only needs to represent extracted and corrected ordering within the analyzed frontier. Hypothetical future or "what-if" timeline branches are out of scope.

## 15. Derived View Builder

### Responsibility

Build UI-ready views from the canonical graph.

### Handles

- timeline-so-far;
- character timelines;
- location timelines;
- open thread summaries;
- entity registry view;
- event board view;
- unresolved temporal buckets;
- conflict markers.

### Important Principle

Derived views should be regenerated from canonical state and correction history.

## 16. Review Queue Builder

### Responsibility

Identify items needing user attention.

### Handles

- possible entity aliases;
- possible entity duplicates;
- possible event duplicates;
- low-confidence events;
- unsupported claims;
- unresolved temporal order;
- temporal conflicts;
- possible flashbacks;
- open thread updates;
- possible thread resolutions.

### MVP Value

Review queues make imperfect AI extraction usable.

## 17. Change Impact / Diff Engine

### Responsibility

Explain what changed after analysis or correction.

### Handles

- new entities;
- new events;
- new locations;
- new temporal claims;
- merged entities;
- merged events;
- timeline changes;
- newly detected conflicts;
- stale views;
- review items created or resolved.

### Example Output

After merging "Masked Rider" and "Lady Ash":

- 18 mentions reassigned;
- 7 events moved into the merged character timeline;
- 3 possible duplicate events found;
- 1 temporal conflict detected;
- 4 event pairs remain unordered.

## 18. Evidence Manager

### Responsibility

Track source evidence for all extracted and canonical objects.

### Handles

- quotes;
- source spans;
- source unit references;
- evidence display;
- evidence comparison for duplicates;
- evidence validation.

### Important Principle

Every timeline event should have inspectable source evidence.

## 19. UI Shell

### Responsibility

Provide the overall application frame.

### Handles

- navigation;
- project loading;
- panels;
- mode switching;
- global state indicators;
- analysis progress indicators.

### MVP Note

This shell can initially be a CLI command surface instead of a graphical UI shell. A web UI may later reuse the same underlying application services.

## 20. Source Text Viewer

### Responsibility

Display the source text and extraction highlights.

### Handles

- reading text;
- highlighting entity mentions;
- highlighting locations;
- highlighting event mentions;
- highlighting temporal cues;
- jumping from event cards to source evidence;
- selecting text for manual correction.

## 21. Structure Navigator

### Responsibility

Show the recognized book structure and analysis status.

### Handles

- chapter/section tree;
- analyzed/not analyzed/stale statuses;
- frontier selection;
- continue-to action;
- analyze-range action;
- ambiguous structure warnings.

## 22. Entity Registry UI

### Responsibility

Show and edit canonical entities and aliases.

### Handles

- entity list;
- aliases;
- mention counts;
- first seen location;
- last seen location;
- related events;
- merge/split controls;
- candidate aliases;
- rejected aliases.

## 23. Event Board UI

### Responsibility

Show extracted and canonical event cards.

### Handles

- event title;
- summary;
- participants;
- location;
- source evidence;
- temporal claims;
- status;
- confidence;
- accept/reject;
- merge/split;
- edit participants/location/order.

## 24. Timeline UI

### Responsibility

Visualize partial-order timelines.

### Handles

- main plot lane;
- character lanes;
- location lanes;
- unresolved buckets;
- parallel threads;
- sync points;
- conflict markers;
- event detail popovers;
- filtering by entity/location/thread.

### Important Principle

The timeline UI should be able to show uncertainty.

Do not require a single global line.

## 25. Open Thread UI

### Responsibility

Display unresolved and resolved narrative threads.

### Handles

- thread title;
- introduced at;
- related entities/events/locations;
- status;
- updates over time;
- possible resolution;
- confirmation/rejection.

## 26. Review Queue UI

### Responsibility

Provide focused review workflows.

### Handles

- review item list;
- item type filters;
- evidence comparison;
- accept/reject/defer;
- bulk operations where safe.

## 27. Agent / Assistant Layer

### Responsibility

Translate natural-language requests into structured reads and operations without directly mutating canonical state.

### Important Principle

The assistant should call the same structured application operations as the CLI or future UI, not special hidden pathways.

### Responsibility

Provide natural-language assistance over structured project state.

### Handles

- user commands;
- question answering within frontier;
- structured operation generation;
- explanation of timeline placement;
- review recommendations;
- change summaries.

### Example Commands

- "Analyze up to Chapter 5."
- "Show Mira's timeline so far."
- "Why is this event before the ambush?"
- "Merge the masked rider and Lady Ash."
- "Show unresolved identity candidates."
- "Mark these two events as same time."
- "Do not use anything after Chapter 8."

### Important Principle

The agent should operate through structured tools and operations, not uncontrolled direct mutation.

## 28. Persistence Layer

### Responsibility

Persist project state, source segments, extraction outputs, correction operations, and concise development bookkeeping where relevant.

### MVP Note

`PROGRESS.md` is the human-and-agent project memory for implementation status. It is not application runtime state, but it should be kept current as development proceeds.

### Responsibility

Persist project data.

### Handles

- uploaded book;
- structure index;
- extracted objects;
- canonical objects;
- corrections;
- review queues;
- derived view cache;
- operation history.

### MVP Storage Options

For a prototype:

- local filesystem plus SQLite;
- or a simple Postgres database;
- or a document store for graph-like state.

The choice can be deferred until implementation planning.
