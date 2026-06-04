# Progress

## Done

**Planning and reader foundation**
- Planning baseline and initial design intent (`975ef8b`); design docs under `design/`.
- Reader foundation: TXT/EPUB reader (`f4d1d3d`), index attribute enrichment (`3fa276b`), unit ID normalization (`625ec2a`), reader schema docs (`c15cf0c`).
- Reader contract: `docs/reader_index_schema.md` — structure index, normalized opaque unit IDs, navigation metadata, on-demand unit text extraction.

**Extraction pipeline — first skeleton (event/timeline-centered)**
- Extraction roadmap (`b82f5ee`), context-aware pass strategy (`2165fbf`), validation-and-repair loop design (`4b5632a`).
- Pipeline skeleton (`4c6690d`), DeepSeek SDK backend (`ef1f265`), versioned segment prompt and extraction failure handling (`5934ceb`).
- First deterministic validation slice: evidence relocation, response-local ID/reference integrity, evidence span length, object-surface grounding, compact LLM repair payloads (`f957a5e` onward).

**Multi-pass extraction and chain flow (old model)**
- Multi-pass scaffolding with `.tilusion_cache/extraction_passes/` artifact caching.
- Chained extraction flow: overview segmentation, deterministic segment-anchor relocation, per-segment extraction with overview hints, aggregate validation, repair-hint artifacts.
- Validation outputs: full local reports, compact LLM-actionable repair hints, enriched validated results with evidence source locations.
- Historical old-chain validation/revalidation work was removed when the reading pipeline became the active extraction path.

**Validation refinement and chain trials (old model)**
- First LLM-backed chain trial (unit-0002, ~15.7K chars): 6/6 segments resolved, 0 errors, surfaced five improvement areas.
- Second chain trial (v0.5 prompt): 14/14 segments restored.

**Unit finalization, repair, and timeline construction (old model)**
- KV-cache-aware prompt composition for multi-round refinement.
- Unit-finalization, repair, timeline construction, and timeline repair passes.
- Historical `run-all` command was removed with the old event/timeline-centered extraction pipeline.

**Reader view generator**
- Self-contained HTML demo consuming real extraction data.
- Template: `tools/reader_view_template.html` (CSS + JS skeleton).

**Regression coverage**
- Tests for reader behavior and extraction pipeline (`pytest tests/test_extraction.py -x -q`).

**Cross-unit readiness scaffold (old model)**
- No-behavior extraction refactor split prompt composition, payload builders, and unit validation from the pipeline orchestrator.
- Passive book-context scaffold and cache-aware context injection.

**First reading-pipeline trial and pivot**
- Generalized reading-pipeline direction documented; reading schema, validation, prompts, payloads, and pipeline scaffold added.
- Unit-0002 reading trial run. Extraction quality regressed vs. the older timeline-centered pipeline. Root causes identified and documented in `docs/source_grounded_reading_pipeline.md`.
- Decision: rebuild the reading pipeline from scratch around deterministic source blocks, concepts, atomic items, and graph-shaped logical groups.

**Updated plan**
- `docs/source_grounded_reading_pipeline.md` revised with v0.3 model, clarified naming, splitter spec, and 12-commit implementation sequence.
- Key design decisions: no backward compat with v0.1, source_spans/ConceptMention/GroupLink/DerivedStructure removed, links moved into logical group graphs, cross-group item membership allowed, factual metrics built into each module from the start.

## Ongoing

- **Current goal:** Rebuild the reading pipeline from scratch following the 12-commit sequence in `docs/source_grounded_reading_pipeline.md`.
- **Immediate next step:** Run the new reading CLI on real unit-0002 output and inspect package quality before cross-unit registry work.
- **Branch:** `cross-unit-refactor`. Old event/timeline-centered `extraction*.py` pipeline has been removed; the active extraction path is the source-grounded reading pipeline plus shared `backend.py`, `overview.py`, and `pass_utils.py` utilities.
- Commits 1–10 reviewed 2026-05-28. Three fixes applied: per-segment `warnings` now propagated through `merge_segment_extraction_results`, dead `elif` branch removed from `_validate_temporal_attributes`, `_list` return type corrected, and unused `alias_candidates` field removed from `Concept` schema.
- Commit 11 done 2026-05-28: factual stage metrics wired through pass artifacts and final `metrics.validation` / `metrics.counts`, without heuristic quality warnings.
- Commit 12 done 2026-05-28: CLI now exposes `run-reading` for the v0.3 reading pipeline and `split-blocks` for deterministic source-block inspection.
- Commit 13 done 2026-05-28: `unit_package.json` output path is now content-addressed from pass cache keys, with a `latest` pointer file for convenience.
- Before the next LLM-backed run: execute the quality cleanup sequence now documented in `docs/source_grounded_reading_pipeline.md` — align coarse concept types, add deterministic type normalization for merges, and dedupe concepts after unit-level deltas.
- Quality cleanup step 1 done: per-segment and unit-grouping prompts now share the coarse schema concept vocabulary, with tests guarding against the previous fine-grained prompt shape.
- Quality cleanup step 2 done: segment merge now normalizes known noisy concept-type aliases before deterministic concept comparison.
- Quality cleanup step 3 done: unit grouping now dedupes concepts that become equivalent after LLM concept deltas, and remaps item/group concept refs through the composed remap.
- Reader remains intentionally neutral about main text vs notes/commentary; separating those is an extraction responsibility.
- Still untested at true 500MB scale.
  - Commits 14–18 done 2026-05-28: concept quality fixes after comparison review of two extraction runs. The LLM unit grouping pass was emitting unsafe merge deltas that collapsed distinct entities into synthetic collections (dates into "biography timeline", places into "place series", terms into "terminology group"). Five commits address this: (14) tightened merge identity rules, (15) narrowed concept type definitions, (16) separated temporal mentions from merging, (17) added deterministic merge safety validation, (18) added tests.
  - Post-18 fixes: uncertainty list normalization to prevent validation crashes on LLM-inconsistent types; HTML reading view offset fix for unit-relative vs book-level positions; source block rendering simplified to uniform `.src-block` class.
  - Pre-feature cleanup 2026-05-29: rebalanced graph guidance in logical grouping prompt (`f800153`); compacted all 3 extraction prompts by ~24% to save context space (`ab71552`); added book-hash scoping to cache directories to prevent cross-book unit ID collisions (`6b951e6`).
  - Multi-turn agentic repair loop 2026-05-29: 6 commits implementing the conversation backbone, backend protocol extension, deterministic auto-fixer, agentic repair loop, pipeline integration, and parallel per-segment extraction (`09219a9` → `fa8039c`). Designed in `design/05_multi_turn_agentic_repair.md`. `complete_json()` is untouched for backward compatibility.
  - Post-repair-loop fixes 2026-05-29: three bug fixes from unit-0007 trial —
    (1) `resolve_overview_segments` no longer drops segments when only one anchor fails; fills missing boundary from neighbours,
    (2) `source_window_needles` tries clause suffixes to catch LLM-fabricated quote prefixes,
    (3) reading view template `.info-item` clicks now highlight item cards and graph nodes.
  - Agentic registry resolution fix slice 2026-06-01: raw LLM proposal JSON is now preserved separately from applied validation subjects; incomplete tool-call loops are rejected before validation; agentic fallback now uses the v0.1 single-pass prompt; v0.2 prompt/tool wording no longer implies incremental proposal emission. Remaining plan items: registry search caching, compact group search results, and registry-backed source-block lookup.
  - Cache layout refactor 2026-06-02: design doc `design/13_cache_layout_redesign.md` added; implementation now writes source index, registry, run manifests, unit packages, cross-unit pass caches, and book-scope `runs.json` under a clean `.tilusion_cache/book-{hash}/` root. Registry metadata now binds to `source_index_id` before book-scope deltas.
  - Source-index Phase 4/5 hardening 2026-06-02: package validation rejects legacy segment-derived block IDs when `source_index_id` is present; registry deltas carry/enforce `source_index_id`; per-segment extraction prompt now presents book-scoped `block-*` IDs as the normal evidence shape.
  - Cross-unit registry troubleshooting 2026-06-04: unit-0003/0004 book registry groups exposed a systematic item-ref remapping bug: group `item_refs` and graph node `item_ref`s retained unit-local IDs that collided with prior registry item IDs. The fix records `unit_item_id -> registry_item_id` during delta application and remaps group item refs before registry insertion; unit `run.json` now logs cross-unit pass artifact summaries directly.
  - Prompt/quality refactor plan 2026-06-04: `design/15_cross_unit_refactor_plan.md` and `design/18_prompt_simplification.md` now prioritize non-fatal quality metrics before prompt refresh, use one prompt template with field-language policy, and keep `timeline` distinct from local `temporal_sequence` groups.
  - Phase 2a quality visibility 2026-06-04: non-fatal quality metrics now report source-surface grounding issues, reader-language issues, non-standard type counts, canonical-name coverage, facet coverage, and timeline/temporal-sequence counts; CLI logs now preview repair targets/values plus concept/group merge proposals with content snippets.
  - Phase 2b prompt refresh 2026-06-04: overview, per-segment extraction, unit grouping, and cross-unit concept/group resolution now use v0.3 prompt resources with one field-language policy; `run-reading` exposes source/reader/normalized language policy options and includes the policy in cache identity.
  - **Next step:** Rebuild a clean `.tilusion_cache/book-{hash}/` root through units 0002-0004 and use the v0.3 prompt policy plus quality/logging signals to target remaining extraction fixes.

## Implementation Status

| # | Commit | Status |
|---|---|---|
| 1 | Update plan and PROGRESS.md | done |
| 2 | Deterministic source block splitter | done |
| 3 | Rewrite reading schema (v0.3) | done |
| 4 | Rewrite reading validation | done |
| 5 | Rewrite per-segment extraction prompt | done |
| 6 | Rewrite per-segment pass (prompts, payloads, pipeline) | done |
| 7 | Segment-scoped ID reindexing | done |
| 8 | Unit-level concept unification and item stabilization | done |
| 9 | Unit-level logical grouping prompt | done |
| 10 | Unit-level logical grouping pass | done |
| 11 | Metrics wiring | done |
| 12 | Update CLI | done |
| 13 | Content-addressable unit package caching | done |
| 14 | Tighten concept merge identity rules | done |
| 15 | Narrow concept type guidance | done |
| 16 | Separate temporal mentions from concept merging | done |
| 17 | Deterministic merge safety validation | done |
| 18 | Merge safety validation tests | done |
| M1 | ConversationContext + TurnMetadata dataclasses | done |
| M2 | Backend protocol + implementations for multi-turn | done |
| M3 | DeterministicAutoFixer with per-code fix functions | done |
| M4 | Agentic repair loop (run_agentic_pass) | done |
| M5 | Pipeline integration — wire agentic loop into passes | done |
| M6 | Parallel per-segment extraction | done |
