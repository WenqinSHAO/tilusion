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
- Chain cache revalidation without backend calls via `refresh-chain-validation`.

**Validation refinement and chain trials (old model)**
- First LLM-backed chain trial (unit-0002, ~15.7K chars): 6/6 segments resolved, 0 errors, surfaced five improvement areas.
- Second chain trial (v0.5 prompt): 14/14 segments restored.

**Unit finalization, repair, and timeline construction (old model)**
- KV-cache-aware prompt composition for multi-round refinement.
- Unit-finalization, repair, timeline construction, and timeline repair passes.
- `run-all` command: end-to-end orchestration with progress logging and unit package output.

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
- **Immediate next step:** Commit 12 — Update CLI.
- **Branch:** `cross-unit-refactor`. Old extraction pipeline (`extraction*.py`) stays untouched as regression baseline. Reading modules (`reading_*.py`) rewritten in-place.
- Commits 1–10 reviewed 2026-05-28. Three fixes applied: per-segment `warnings` now propagated through `merge_segment_extraction_results`, dead `elif` branch removed from `_validate_temporal_attributes`, `_list` return type corrected, and unused `alias_candidates` field removed from `Concept` schema.
- Commit 11 done 2026-05-28: factual stage metrics wired through pass artifacts and final `metrics.validation` / `metrics.counts`, without heuristic quality warnings.
- Reader remains intentionally neutral about main text vs notes/commentary; separating those is an extraction responsibility.
- Still untested at true 500MB scale.

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
| 12 | Update CLI | pending |
