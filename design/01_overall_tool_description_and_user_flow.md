# 01 — Overall Tool Description and User Flow

## Working Name

**Novel Timeline Workbench**

Alternative short description:

> A frontier-controlled, interactive narrative structuring tool for novels.

## Product Goal

Build an interactive tool that helps a user progressively inspect and structure a novel as they read. The tool should extract and visualize entities, locations, events, temporal claims, and open narrative threads up to a user-selected reading frontier.

The tool is not primarily a one-shot summarizer. It should behave more like a narrative-structure workbench: AI proposes structure, the user reviews and corrects it, and the system recomputes derived views such as timelines, character lanes, location lanes, unresolved event buckets, and review queues.

## Agreed MVP Delivery Shape

The first usable version should be:

- local-first;
- single-user;
- CLI-first.

A web UI is still a likely later direction, but it is not the first implementation target. The CLI should prove the core model and workflows before UI work begins.

## Core Product Premise

A novel should not be analyzed only after full completion. The reader may want assistance while reading.

The tool should therefore support:

- uploading a book;
- recognizing the book's structure;
- showing the recognized structure;
- letting the user choose how far to analyze;
- analyzing only uncovered text up to that frontier;
- showing timeline and structure so far;
- letting the user correct entities, events, aliases, orders, and uncertainties;
- recomputing affected views after corrections;
- letting the user advance the frontier and continue.

## MVP Input Formats

Support only:

- `.txt`
- `.epub`

Avoid in v1:

- PDF
- scanned books
- OCR
- heavily formatted academic documents
- multi-volume corpora
- arbitrary web scraping

## Key Concepts

### Book Structure Index

After upload, the first job is not deep analysis. The first job is to recognize and index the book structure.

For EPUB:

- table of contents;
- spine order;
- chapters;
- sections;
- internal anchors;
- document hrefs.

For TXT:

- chapter heading detection;
- part/section heading detection;
- paragraph numbering;
- character offsets;
- fallback chunking if headings are ambiguous.

The recognized structure becomes the control surface for the user.

### Analysis Frontier

The analysis frontier is the user's selected boundary.

Examples:

- analyze through Chapter 3;
- continue to Chapter 8;
- analyze only Chapter 12;
- stop before Part II.

The tool should treat everything beyond the frontier as unavailable in default reading-companion mode.

For MVP, keep this simple:

- analyze only up to the user-given position;
- do not model hypothetical or alternative future timelines;
- do not add speculative "what-if" exploration features.

### Incremental Analysis

When the frontier advances, analyze only the newly uncovered range. Do not recompute the whole book unless necessary.

Example:

- old frontier: Chapter 4;
- new frontier: Chapter 7;
- uncovered range: Chapter 5 through Chapter 7.

The tool extracts new structure from the uncovered range, links it to existing structure, updates derived views, and shows a change report.

### Derived Views

The rendered timeline is not the source of truth.

The source of truth should be a structured model containing:

- source spans;
- entity mentions;
- canonical entities;
- location mentions;
- canonical locations;
- event mentions;
- canonical events;
- temporal claims;
- identity claims;
- open narrative threads;
- user corrections;
- version history or operation history.

Timelines and review queues are derived views over this model.

The implementation should preserve a strict separation between:

- raw extraction outputs;
- canonical project state;
- user correction operations.

These layers should not be silently collapsed together.

## Primary User Flow

### 1. Upload Book

User uploads a `.txt` or `.epub` file.

The system creates a book record and stores the raw source text or extracted EPUB content.

### 2. Build Structure Index

The system detects the book's structure.

For EPUB, prefer the EPUB table of contents and spine.

For TXT, detect headings such as:

- Part I
- Book One
- Chapter 1
- Chapter One
- I.
- section separators
- paragraph boundaries

If structure detection is uncertain, the tool should still produce a fallback structure based on chunks and paragraphs.

### 3. Show Recognized Structure

The tool displays a structure tree or outline.

Each unit should have a status:

- not analyzed;
- partially analyzed;
- analyzed;
- stale;
- needs review;
- failed.

The user can select a target frontier from this outline.

### 4. User Selects Analysis Frontier

User chooses a frontier such as Chapter 5.

The tool computes:

- already analyzed range;
- newly uncovered range;


The future range is ignored.

### 5. Analyze Uncovered Parts

If there are uncovered parts before or at the frontier, the tool analyzes them.

The tool extracts:

- entities;
- alias candidates;
- locations;
- event mentions;
- canonical event candidates;
- temporal claims;
- open threads;
- unresolved questions;
- possible contradictions.

### 6. Visualize Current Structure

Once the requested frontier is fully analyzed, the tool shows:

- timeline so far;
- character-local timelines;
- location timelines;
- entity registry;
- event board;
- open thread board;
- review queues;
- change report since previous frontier.

### 7. User Corrects Structure

User may correct:

- entity merges;
- entity splits;
- alias confirmations/rejections;
- event merges;
- event splits;
- event order;
- unknown event order;
- simultaneous events;
- event location;
- event participants;
- open thread resolution;
- false extraction rejection.

Corrections should be stored as operations, not silent mutations.

### 8. Recompute Affected Views

The system should recompute only affected graph regions when possible.

Example:

If the user merges `Masked Rider` and `Lady Ash`, the system should update:

- entity registry;
- alias clusters;
- affected event participants;
- character timelines;
- possible duplicate event candidates;
- temporal constraints touching affected events;
- open thread status;
- review queues;
- timeline diffs.

### 9. User Advances Frontier

User chooses a later frontier.

The tool performs delta analysis and shows structural changes.

## Important UX Principle

The tool should not claim to know more than the text supports.

It should explicitly represent:

- unknown order;
- weak alias candidates;
- unresolved threads;
- contradictory claims;
- possible duplicate events;
- low-confidence extractions;
- alternative interpretations.

The user should be able to inspect evidence for every proposed structure.

## Agentic Development Notes

To keep implementation auditable and robust:

- prefer deterministic schemas and validation around claims, evidence, and operations before deep LLM integration;
- let the LLM produce structured extraction candidates rather than directly mutating application state;
- record corrections as durable operations early, even if storage is simple at first;
- prove the end-to-end flow with a CLI path before building richer UI layers.

## Suggested Main Screens

### Upload / Library Screen

Purpose:

- upload books;
- choose existing project;
- show analysis progress.

### Structure Navigator

Purpose:

- show detected book structure;
- show analysis status per chapter/section;
- allow user to choose frontier.

### Reading / Source View

Purpose:

- show source text;
- highlight entity mentions, events, locations, time cues;
- let user inspect source evidence.

### Timeline View

Purpose:

- show partial-order event structure up to the frontier;
- support character lanes, location lanes, unresolved buckets, and sync points.

### Entity Registry

Purpose:

- show canonical entities;
- show aliases and nickname candidates;
- review merge/split suggestions.

### Event Board

Purpose:

- show extracted event cards;
- support accept/reject/merge/split/edit operations.

### Open Thread Board

Purpose:

- track unresolved narrative questions and plot threads.

### Review Queue

Purpose:

- focus user attention on uncertain or consequential model decisions.

### Change Impact Panel

Purpose:

- explain what changed after a frontier advance or correction.

## MVP Scope

### Include in v1

- TXT and EPUB upload.
- Book structure detection.
- User-selected frontier.
- Incremental chapter-level analysis.
- Entity extraction.
- Location extraction.
- Scene-level event extraction.
- Temporal cue and temporal claim extraction.
- Open thread extraction.
- Timeline-so-far.
- Entity registry.
- Event board.
- Review queues.
- Manual corrections for merge/split/order/unknown order.
- Change report after analysis and corrections.

### Defer until later

- PDF support.
- OCR.
- Full causal graph.
- Complete knowledge graph ontology.
- Map visualization.
- Fine-grained sentence/action-level extraction.
- Full automatic spoiler-aware whole-book preanalysis.
- Multi-book or series-level analysis.
- Collaborative editing.
- Advanced literary theory annotations.

## Non-Goals for v1

- Perfect extraction quality.
- Fully automatic canonical truth.
- One-shot full-book analysis.
- Total ordering of all events.
- Automatic resolution of every contradiction.
- General-purpose chatbot over arbitrary files.

## Success Criteria

A successful MVP should let a user:

1. Upload a TXT or EPUB novel.
2. See a recognized chapter/section structure.
3. Choose a frontier.
4. Analyze up to that frontier.
5. View entities, locations, events, temporal claims, and open threads.
6. Inspect evidence for extracted events.
7. Correct entity and event mistakes.
8. Advance the frontier.
9. See a clear report of what changed.
10. Maintain uncertainty instead of forcing unsupported precision.
