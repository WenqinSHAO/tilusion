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

- `entity_mention`
- `location_mention`
- `event_mention`
- `time_expression`
- `temporal_claim`
- `alias_candidate`
- `thread_candidate`
- `evidence_span`

This is the extraction substrate.

It is intentionally smaller than the eventual canonical graph.

### 2. Local Extraction Before Global Reasoning

Extraction should begin at the smallest useful structural unit, usually a chapter or later a scene-like segment.

First extract:

- what entities are mentioned
- what locations are mentioned
- what events are described
- what temporal cues are present
- what unresolved threads are introduced or updated

Do not attempt cross-book resolution in the first pass.

### 3. Evidence Is Mandatory

Every extracted object should point to source evidence.

No object should exist only as a model claim without:

- a supporting span
- a source unit ID
- enough local context for a human to inspect it later

### 4. Corrections Must Beat Raw Model Confidence

The system should be designed so that:

- human correction overrides model output
- corrected objects are durable
- downstream views recompute from corrected state

This matters more than chasing raw extraction recall in early milestones.

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

Success criteria:

- output schema validates
- every object has evidence
- rerunning on the same input is reasonably stable
- token cost per unit is measurable and acceptable

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
  - prompt version
  - schema version
- never rerun the whole book when only one frontier segment changed

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

## Recommended LLM Workflow Pattern

The extraction path should likely use a multi-pass pattern such as:

1. mention extraction
2. local event grouping
3. temporal cue extraction
4. thread extraction
5. cross-unit linking
6. review generation

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
