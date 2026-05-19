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

The pack has two layers: **always-included compact context** and **tool-accessible detail**. The compact context is small enough to include in every extraction prompt. The detail is pulled in only when the LLM signals it needs it via tool calls.

### Compact context (always in prompt)

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

### Tool-accessible detail (on-demand)

These records are NOT included in the prompt by default. They are available through deterministic tool calls the LLM can invoke during extraction:

- **Full thread event lists** — when the extractor detects an event that may continue a known thread
- **Full timeline structures** — when the extractor needs to place a new event relative to known timeline anchors
- **Entity/location canonical records with alias lists** — when the extractor encounters a surface that may match a known entity and needs to resolve whether it is the same one
- **Prior event details** — when the extractor needs to check whether a detected event duplicates or references a prior one

### Deterministic surface scanner

Before building the context pack, run a deterministic lexical scan over the current unit text against all known canonical names, aliases, and surface forms from the book registry.

This scan:
- Requires zero LLM calls — it is pure string matching against prior extraction records
- Produces a hit list: "this unit text likely mentions these 15 known entities and 8 known locations"
- Feeds into the compact context as the `entities` and `locations` arrays, each entry carrying just the canonical record needed for disambiguation
- Also produces a `surfaces_not_matched` list for the selection report — surfaces that appear in the unit but don't match any known record (these may be new entities or alias variants)

The scan is intentionally low-recall and precision-oriented. False positives are worse than false negatives because they push the LLM toward incorrect continuity. The LLM can always call `resolve_entity` or `resolve_location` if it suspects a match the scanner missed.

The context pack and selection report should be written as artifacts beside the extraction pass. Their hashes must become part of downstream cache keys.

## Agentic Tool-Use During Extraction

Rather than dumping all prior book-scope records into the prompt, the LLM extractor should have access to deterministic tools it can call when it detects a narrative signal that matches or extends prior structure.

### Tool definitions

```
detail_thread(thread_id) → {
  thread_id, title, summary, status,
  last_N_events: [{event_id, summary, participants, locations, confidence}],
  open_questions: [...],
  expected_continuation_signals: [...]
}
```

```
detail_timeline(timeline_id) → {
  timeline_id, summary, confidence,
  ordered_events: [{event_id, summary, before_edges}],
  time_anchors: [{expression, approximate_date, linked_events}]
}
```

```
resolve_entity(query: canonical_name | alias | surface_form) → {
  entity_id, canonical_name, aliases, entity_kind,
  compact_description, recent_events: [{event_id, summary}],
  unresolved_alias_candidates: [...]
}
```

```
search_prior_events(query: text) → [{
  event_id, summary, thread_id, timeline_id, confidence
}]
```

### How it works during segment extraction

1. The segment extraction prompt includes compact book-scope context (thread summaries, matched entity/location records, recent timeline anchors).
2. The LLM detects something that may involve a known thread, entity, or timeline — for example, an event that looks like a continuation of thread-42.
3. The LLM calls `detail_thread("thread-42")`. The tool returns the full event list and expected continuation signals.
4. The tool response is injected into the extraction context for the remainder of that segment pass. The LLM uses it to confirm or reject the thread continuation, and to set `thread_id` and `before_edges` correctly on new events.
5. Each tool call and response is recorded in the pass manifest for audit and reproducibility.

### Cache implications

Tool calls create a dependency chain for caching:
- A segment extraction pass that calls `detail_thread("thread-3")` must include the snapshotted state of thread-3 in its cache key.
- The pass manifest records: which tool was called, with which arguments, against which book-state snapshot.
- On cache hit, verify the referenced snapshots haven't changed. If they have, the cache is invalid.
- This is a natural extension of the existing KV-cache prefix strategy: the compact context prefix stays stable, tool responses are injected after the prefix, and the full prompt composition hash covers both.

### Why this beats enumeration

- Context stays compact by default — only the 3-5 line thread summaries are always present, not full event histories.
- Detail is pulled in exactly when there's a *reason* to reference prior structure, not speculatively.
- The aggregate set of tool calls across segments is itself a useful signal — it tells you which threads and entities the LLM considered relevant to this unit.
- The LLM learns to use the tools as it reasons about narrative continuity, mirroring how a human annotator would flip to the relevant section of their notes rather than pinning everything on the wall at once.
- New extractors and models can use the same deterministic tools without retraining — the tool interface is stable even if the prompt evolves.

### Non-goals for tool-use

- No LLM-generated tool calls that mutate book state directly. All mutations go through deterministic validation and transaction logging.
- No recursive or chained tool calls within a single extraction step. One round of tool calls per segment, then the LLM produces its output.
- No tools that return raw prior-unit text or prior-pass raw responses. Tools return structured records only.

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
2. Run the deterministic surface scanner over the unit text against the current book registry. Produce a hit list of matched entities, locations, thread cue terms, and time expressions.
3. Build `context_pack.json` with compact summaries (thread summaries, matched canonical records, recent timeline anchors) using a token-budgeted selector. Write `context_selection_report.json` explaining what was included and excluded.
4. Run overview segmentation on the target unit. Initially this can stay mostly unit-local, with optional recent arc summary only if needed.
5. Run per-segment extraction with: `[static prompt] + [overview hints for this segment] + [compact book-scope context] + [current segment text]`. The LLM may call detail/resolve/search tools when it detects narrative continuity signals.
6. Tool responses are injected into the extraction context. Each tool call and response is recorded in the pass manifest.
7. Run deterministic validation and repair-hint generation exactly as now for local evidence and record integrity.
8. Run unit finalization with local segment results plus the context pack. This pass can propose canonical matches, alias candidates, and thread continuations.
9. Run a cross-unit canonicalization pass that outputs a delta: new canonical records, proposed merges, updated aliases, thread updates, timeline anchor updates, and unresolved ambiguity queue.
10. Validate the delta deterministically before applying it to book state.
11. Write a new book-state snapshot and transaction log entry only after validation passes or the user explicitly accepts lower-confidence changes.

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

The next work should remain verifiable and avoid LLM-backed reruns until passive scaffolding is inspectable. The first two steps are already complete.

### Completed

1. **Passive schema and artifact scaffolding** (done: `fb7f969`).
   - `tilusion/book_context.py` with stable `book_id`, book cache paths, empty/default `BookStateSnapshot`, and passive `ContextPack` builders.
   - Writes context-pack artifacts under `.tilusion_cache/books/<book_id>/context_packs/<unit_id>/`.
   - Prompt injection is disabled: `prompt_injection.enabled = false` everywhere.

2. **Pipeline wiring without prompt behavior changes** (done: `ab44208`).
   - `run-all` builds and persists the passive context pack.
   - `unit_package.json` carries `book_context` metadata for inspection.
   - Existing extraction pass cache keys unchanged.

### Planned

3. **Cache-key readiness.**
   - Add context-pack hash fields and helper functions.
   - Document where future prompt-affecting passes must include the context-pack hash and tool-call response hashes.
   - Do not invalidate current LLM caches until the context pack or tool responses are actually injected into request payloads.

4. **Deterministic context selector (surface scanner + compact context builder).**
   - Lexical surface/alias scan over current unit text against book registry records (entities, locations, thread cue terms, time expressions).
   - Produce a matched-hit list and a `surfaces_not_matched` list.
   - Build compact context: thread summaries, matched entity/location canonical records, recent timeline anchors.
   - Write `context_pack.json` and `context_selection_report.json` with include/exclude reasoning.
   - Write the surface scan hit list and tool-accessible record indices as separate artifacts.
   - Zero LLM calls at this step. Build mock registries from `unit-0002` as fixtures.

5. **Tool scaffolding (deterministic, no LLM calls yet).**
   - Define tool schemas: `detail_thread`, `detail_timeline`, `resolve_entity`, `search_prior_events`.
   - Implement each tool as a deterministic function over book-state snapshot JSON.
   - Add a tool-call record type and include tool-call argument/response hashes in pass manifest.
   - Test with mock tool calls against a `unit-0002`-derived registry.
   - Do not wire into LLM prompts yet.

6. **Prompt integration after review.**
   - Add a generated prompt part for compact selected context.
   - Wire tool definitions into the extraction prompt so the LLM can invoke them.
   - Record tool calls in the pass manifest and include their argument/response hashes in cache keys.
   - Then run LLM-backed extraction on the next unit, using `unit-0002` as prior state.

## Implementation Milestones

Milestone 1: Schema and artifacts only. (Done)

- Define `BookStateSnapshot`, `ContextPack`, and `RegistryDelta` JSON shapes.
- Write passive artifacts from `run-all`.
- No LLM behavior changes.

Milestone 2: Deterministic context selector.

- Implement the lexical surface scanner: scan current unit text against book registry surfaces, aliases, and cue terms.
- Build compact context with matched records, thread summaries, and recent timeline anchors.
- Write `context_pack.json` and `context_selection_report.json` with include/exclude reasoning.
- Zero LLM calls at this milestone. Test with mock registries derived from `unit-0002`.

Milestone 3: Tool scaffolding.

- Implement the four tool functions deterministically over book-state snapshot JSON.
- Define tool-call record schema and wire into pass manifest.
- Test tool calls return correct structured records for known entity/thread/timeline IDs.
- Cache-key readiness: include tool-call argument/response hashes in pass cache keys (but don't inject into prompts yet).

Milestone 4: Prompt integration — compact context.

- Add a generated prompt part for compact selected context (thread summaries, matched records, timeline anchors).
- Feed compact context to segment extraction and unit finalization.
- The prompt instructs the LLM that prior context is guidance for matching and continuity, not evidence for new facts.
- Keep current one-unit pipeline available as baseline.

Milestone 5: Prompt integration — tool-use.

- Wire tool definitions into the extraction prompt schema so the LLM can invoke `detail_thread`, `detail_timeline`, `resolve_entity`, `search_prior_events`.
- Record tool calls and responses in the pass manifest.
- Include tool-call argument/response hashes in cache keys.
- Run LLM-backed extraction on a new unit with `unit-0002` as prior state.

Milestone 6: Cross-unit canonicalization delta.

- Add a pass after unit finalization/repair/timeline that proposes registry deltas.
- Do not mutate registry directly from raw LLM output.
- Validate delta and write transaction artifacts.

Milestone 7: Book-state snapshot writer.

- Apply validated deltas to the latest snapshot.
- Preserve previous snapshots and transaction logs.
- Add CLI commands to inspect state, context packs, and unresolved items.

Milestone 8: Periodic consolidation.

- Use the large context window over many unit packages and registry deltas.
- Produce arc summaries, alias merge recommendations, thread status updates, and timeline conflict repairs.
- Keep consolidation separate from routine per-unit extraction.

## Non-Goals For The Next Step

- No LLM-backed tool calls until tool scaffolding and the deterministic context selector are tested with mock registries.
- No vector database until deterministic JSON indices are insufficient.
- No full-book timeline reconstruction on every chapter.
- No automatic destructive merges of entities, locations, threads, or timelines.
- No assumption that prior extraction can serve as evidence for new-unit facts.
- No recursive or chained tool calls within a single extraction step.
