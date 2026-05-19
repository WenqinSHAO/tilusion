# Cross-Unit Extraction and Context Plan

This document is a planning note only. It reviews the current extraction code and proposes how extracted threads, timelines, entities, locations, and time anchors from one unit should help later units without letting context grow unbounded.

## Current Code Review

The current pipeline is unit-local and is now usable as a complete first pass:

- `run-chain` performs overview segmentation, deterministic segment restoration, per-segment extraction, validation, and repair-hint generation.
- `finalize-unit` merges segment-local records into unit-level entities, locations, events, threads, unresolved items, and quality notes.
- `repair-unit` improves unit-level extraction using finalization output and repair targets.
- `timeline-unit` builds unit-local partially ordered timelines from repaired unit records.
- `repair-timeline` fills timeline coverage gaps and validates ordering structure.
- `run-all` orchestrates the full flow and writes `.tilusion_cache/units/<unit_id>/unit_package.json`.

Strong parts to preserve:

- Prompt composition is explicit and cacheable through `PromptPart` and `PromptComposition`.
- Intermediate artifacts are inspectable: prompt composition, request payload, raw response, parsed result, validation report, and reader/QC views.
- Segment evidence relocation is deterministic and increasingly robust to LLM quote fuzziness.
- Unit timeline construction already avoids asking the LLM to rewrite entity/location/event records; it only adds timelines and merges them back deterministically.

Issues and improvement room:

- `tilusion/extraction_pipeline.py` is doing too much: orchestration, prompt composition, payload compaction, validation, artifact IO, timeline repair, packaging, and Markdown formatting. Cross-unit state will make this harder unless split before implementation grows.
- There is no book-level state model. `unit_package.json` is a final unit artifact, but it is not yet consumed as durable context for later units.
- There is no context-pack selector. Segment extraction receives only current-segment overview hints, not relevant prior confirmed knowledge.
- Cache keys do not include a prior-context snapshot or context-pack hash. Once prior context is injected, reproducibility requires the exact selected context to be part of the cache identity.
- Unit finalization validation is shallow. It checks top-level fields but does not yet validate unit-level reference integrity, duplicate IDs, evidence reference existence, alias ambiguity, event-thread links, or entity/location references inside events.
- Timeline validation is useful but still unit-local. It checks coverage, phantom refs, cycles, and shared events, but not cross-unit continuity.
- Timeline repair currently extracts missing event IDs by regex from validation messages. That is fragile; later validation should provide structured repair targets.
- The pipeline has no state transaction log. A failed or low-quality unit should not silently mutate book-level canonical records.

## Design Principles

1. The current unit text remains the source of truth for current-unit extraction.
2. Previous extraction is guidance, not authority. It should help resolve aliases, continue threads, and place events, but it must not force unsupported facts into the new unit.
3. Context must be selected, explainable, and cache-keyed. Every prior record included in an LLM call should have a reason and a source snapshot.
4. LLM calls should output deltas where possible. Do not ask each call to rewrite the whole book registry.
5. Use the 1M-token context as a strategic batch-review resource, not as a default dumping ground for all accumulated state.
6. Keep deterministic validation between passes. LLM repair should consume concise structured issues, not full local logs.

## What Carries Forward

The output of a completed unit should feed a book-level registry. The registry should not be a single growing prompt blob; it should be structured, indexed, and summarized.

Entities:

- Canonical entity ID, display name, aliases, observed surfaces, entity kind, compact description, first/last seen unit, salience/activity score, relationship hints, unresolved alias candidates, and a small set of evidence refs.
- Use for alias resolution, duplicate prevention, role continuity, and relationship consistency.

Locations:

- Canonical location ID, names/aliases, hierarchy or containment when known, first/last seen unit, linked events, and evidence refs.
- Use for scene continuity, route/path events, and location disambiguation.

Threads:

- Thread ID, title, compact summary, status, last update, related entities/locations, open questions, expected continuation signals, and evidence/event refs.
- Use to decide whether a new event continues a prior narrative thread or starts a new one.

Events and timelines:

- Do not carry every event into every future extraction prompt.
- Carry recent events, active-thread heads, landmark events, unresolved temporal constraints, and arc-level checkpoints.
- Use full event history only for periodic consolidation or focused retrieval.

Time anchors:

- Normalized time expression records, unit-local anchors, relative ordering constraints, and links to events.
- Use to place new events relative to known anchors without rebuilding the whole chronology each time.

## Context Pack

Before extracting a new unit, build a context pack from the current book-state snapshot and the new unit text.

Suggested shape:

```json
{
  "book_state_snapshot_id": "snapshot-...",
  "target_unit_id": "unit-0003",
  "context_budget": {
    "max_input_tokens": 1000000,
    "target_context_tokens": 60000,
    "selection_policy": "cross-unit-context-v0.1"
  },
  "selection_summary": {
    "known_surface_hits": 18,
    "active_threads_included": 5,
    "recent_units_included": 2,
    "excluded_counts": {
      "entities": 1432,
      "events": 3800
    }
  },
  "entities": [],
  "locations": [],
  "active_threads": [],
  "recent_events": [],
  "landmark_events": [],
  "time_anchors": [],
  "arc_summaries": [],
  "selection_reasons": []
}
```

The context pack should be written as an artifact beside the extraction pass. Its hash must become part of downstream cache keys.

## Budget Tiers

Use tiers rather than a single all-or-nothing context decision.

Tier 0: Always included.

- Static prompt contract.
- Current unit text.
- Current unit reader metadata and source locator information.

Tier 1: Recent local context.

- Previous one to three unit summaries.
- Recently active entities, locations, threads, event heads, and time anchors.

Tier 2: Retrieved context.

- Canonical entities and locations matched by deterministic surface scan.
- Threads whose aliases, locations, or cue terms appear in the current unit.
- Timeline anchors near matched threads or entities.

Tier 3: Compressed arc/book context.

- Rolling summaries for arcs or chapter ranges.
- Stable world/background facts.
- High-salience character/location summaries.

Tier 4: Large-window consolidation context.

- Many units or full arc packages.
- Used for periodic reconciliation, not routine per-unit extraction.
- This is where the 1M context window should be exploited most directly.

## Proposed Cross-Unit Flow

For the next chapter after `unit-0002`:

1. Read and index the target unit.
2. Deterministically pre-scan the unit text for known aliases, location surfaces, time expressions, and thread cue terms from the current book registry.
3. Build `context_pack.json` using a token-budgeted selector.
4. Run overview segmentation on the target unit. Initially this can stay mostly unit-local, with optional recent arc summary only if needed.
5. Run per-segment extraction with current segment text, overview hints, and the selected context pack. The prompt should say prior context is guidance for matching and continuity, not evidence for new facts.
6. Run deterministic validation and repair-hint generation exactly as now for local evidence and record integrity.
7. Run unit finalization with local segment results plus the context pack. This pass can propose canonical matches, alias candidates, and thread continuations.
8. Run a cross-unit canonicalization pass that outputs a delta: new canonical records, proposed merges, updated aliases, thread updates, timeline anchor updates, and unresolved ambiguity queue.
9. Validate the delta deterministically before applying it to book state.
10. Write a new book-state snapshot and transaction log entry only after validation passes or the user explicitly accepts lower-confidence changes.

## Long-Novel Strategy

For a novel with 2000+ chapters, thousands of characters, and many interleaved timelines, the system should avoid sending the full accumulated extraction state to every LLM call.

Default per-unit extraction should use compact selected context:

- Recent unit summaries and active threads.
- Deterministic surface matches from the current unit.
- High-salience canonical records.
- Relevant timeline heads and unresolved temporal constraints.

Periodic consolidation should use the large context window:

- Every N units, arc boundary, volume boundary, or when validation detects growing conflict.
- Input can include many unit packages, registry deltas, unresolved alias candidates, and timeline conflicts.
- Output should be registry deltas and checkpoint summaries, not a replacement for all raw artifacts.

This reduces stitching effort while keeping routine calls stable and inspectable. The 1M context window is most valuable for checkpoint review, alias merge audits, timeline reconciliation, and rebuilding compact arc summaries.

## Book-State Artifacts

Likely future artifact layout:

```text
.tilusion_cache/
  books/
    <book_id>/
      registry/
        latest.json
        snapshots/
          snapshot-000001.json
        transactions/
          unit-0002.delta.json
      context_packs/
        unit-0003/
          context_pack.json
          context_selection_report.json
      indices/
        surfaces.json
        aliases.json
        active_threads.json
```

The first implementation can use JSON files. SQLite or a vector index should be deferred until JSON lookup becomes painful or slow.

## Validation Additions

Before cross-unit mutation, add deterministic checks for:

- Context pack references exist in the selected registry snapshot.
- Context pack hash is recorded in pass cache keys.
- Canonical IDs are not reused across entity/location/thread/event types.
- Proposed alias merges have source evidence or are marked as unresolved candidates.
- Proposed thread continuations reference known or newly created thread IDs.
- Timeline deltas do not create obvious cycles or phantom event refs.
- Registry updates are append-only unless a repair pass explicitly records the replacement reason.

Quality metrics to track over time:

- Per-unit issue count and repair count.
- Entity/location duplicate rate.
- Alias merge churn.
- Unresolved ambiguity count.
- Timeline conflict count.
- Context-pack size and selected-record count.
- Cache hit rate by pass and by context-pack hash.

## Immediate Multi-Commit Sequence

The next work should remain verifiable and avoid LLM-backed reruns until passive scaffolding is inspectable. Planned commits:

1. Passive schema and artifact scaffolding.

- Add a small book-context module with stable `book_id`, book cache paths, empty/default `BookStateSnapshot`, and `ContextPack` builders.
- Write context-pack artifacts beside unit packages or under `.tilusion_cache/books/<book_id>/context_packs/<unit_id>/`.
- Do not pass the context pack into prompts yet.

2. Pipeline wiring without prompt behavior changes.

- Let `run-all` build and persist the passive context pack.
- Add context-pack metadata to `unit_package.json` for inspection.
- Keep existing extraction pass cache keys unchanged until context affects prompts.

3. Cache-key readiness.

- Add context-pack hash fields and helper functions.
- Document where future prompt-affecting passes must include the context-pack hash.
- Do not invalidate current LLM caches until the context pack is actually injected into request payloads.

4. Deterministic context selector.

- Start with simple JSON state and lexical surface matching.
- Produce `context_selection_report.json` explaining included and excluded context.
- Still avoid LLM calls unless a mock/cached test needs to verify artifact shape.

5. Prompt integration after review.

- Add a generated prompt part for selected context.
- Include context-pack hash in affected cache keys at the same time.
- Then run LLM-backed extraction on the next unit, using `unit-0002` as prior state, instead of spending tokens only rerunning `unit-0002`.

## Implementation Milestones

Milestone 1: Schema and artifacts only.

- Define `BookStateSnapshot`, `ContextPack`, and `RegistryDelta` JSON shapes.
- Add docs and sample fixtures from `unit-0002`.
- No LLM behavior changes.

Milestone 2: Deterministic context selector.

- Build a simple surface/alias scanner over current unit text.
- Select recent context, matched canonical records, active threads, and nearby timeline anchors.
- Write `context_pack.json` and `context_selection_report.json`.
- Include context-pack hash in cache keys, but keep prompts unchanged at first.

Milestone 3: Prompt integration.

- Add a generated context prompt part.
- Feed compact context to segment extraction and unit finalization.
- Keep current one-unit pipeline available as baseline.

Milestone 4: Cross-unit canonicalization delta.

- Add a pass after unit finalization/repair/timeline that proposes registry deltas.
- Do not mutate registry directly from raw LLM output.
- Validate delta and write transaction artifacts.

Milestone 5: Book-state snapshot writer.

- Apply validated deltas to the latest snapshot.
- Preserve previous snapshots and transaction logs.
- Add CLI commands to inspect state, context packs, and unresolved items.

Milestone 6: Periodic consolidation.

- Use the large context window over many unit packages and registry deltas.
- Produce arc summaries, alias merge recommendations, thread status updates, and timeline conflict repairs.
- Keep consolidation separate from routine per-unit extraction.

## Non-Goals For The Next Step

- No dynamic ontology induction as the primary task.
- No vector database until deterministic JSON indices are insufficient.
- No full-book timeline reconstruction on every chapter.
- No automatic destructive merges of entities, locations, threads, or timelines.
- No assumption that prior extraction can serve as evidence for new-unit facts.
