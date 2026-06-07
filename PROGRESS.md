# Progress

## Done

**Planning and reader foundation**
- Planning baseline and initial design intent (`975ef8b`); design docs under `design/`.
- Reader foundation: TXT/EPUB reader (`f4d1d3d`), index attribute enrichment (`3fa276b`), unit ID normalization (`625ec2a`), reader schema docs (`c15cf0c`).
- Reader contract: `docs/reader_index_schema.md` — structure index, normalized opaque unit IDs, navigation metadata, on-demand unit text extraction.

**Extraction pipeline (source-grounded reading pipeline)**
- First skeleton, backend, validation, repair loop, then full rebuild around deterministic source blocks, concepts, atomic items, and graph-shaped logical groups.
- 18-commit reading pipeline sequence (`docs/source_grounded_reading_pipeline.md`) completed.
- Multi-turn agentic repair loop for extraction passes.
- Agentic cross-unit concept/group resolution with registry API tool calling.
- Cache layout: `.tilusion_cache/book-{hash}/` with content-addressed unit packages.

**Quality cleanup and cross-unit hardening**
- Coarse concept type alignment, deterministic type normalization, concept dedup after unit deltas.
- Unsafe merge delta prevention (commits 14–18).
- Item-ref remapping fix for cross-unit group continuity.
- Source-index hardening: package validation rejects legacy block IDs, registry deltas carry `source_index_id`.

**Cross-unit refactoring — Phases 1, 1.5, 2a, 2b**
- Phase 1: embedding cache (`registry_index.py`) — `sha256(text)` keys, two-layer memory+disk.
- Phase 1.5: per-concept candidate maps with caps (5 embedding + 3 BM25-only), stderr warnings.
- Phase 2a: quality metrics scaffolding (`tilusion/reading_quality.py`, 356 lines) — non-fatal field-language checks, type vocabulary checks, canonical-name coverage, facet coverage, group granularity metrics. Human-readable merge/proposal logging in pipeline stderr.
- Phase 2b: v0.3 prompt refresh — all 5 prompts use field-language policy (source-grounded identity / reader-facing prose / pipeline-normalized internals). `timeline` vs `temporal_sequence` are now distinct granularities. Facet and canonical_name instructions added. `reader_language` parameter (default `zh-Hans`) wired through CLI, payloads, and cache identity.
- Phase 2c planned: prompt/data-model contract refactor with code-owned schemas, composable prompt sections, type registry, and pluggable semantic guidance. Design commits: `4094893`, `defd611`.

**Regression coverage**
- 423 tests passing across all test files.

## Ongoing

- **Current goal:** Narrow v0.3 prompt type vocabularies to target novels and essays, then run an LLM-backed extraction test to collect quality metrics before implementing Phase 2c (contract refactor).
- **Branch:** `prompt_refresh`.
- **Immediate next step:** Design the Phase 2c Python API (contract metadata dataclass, prompt section renderer, type registry shape), then reduce core concept/item/group types in v0.3 prompts to a smaller set suitable for narrative/essay texts.

## Implementation Status

| # | Commit | Status |
|---|---|---|
| 1–18 | Reading pipeline rebuild (12 commits + 6 hardening) | done |
| M1–M6 | Multi-turn agentic repair loop | done |
| R1–R6 | Cross-unit registry resolution | done |
| Phase 1 | Embedding cache | done |
| Phase 1.5 | Per-concept candidate maps | done |
| Phase 2a | Quality metrics scaffolding | done |
| Phase 2b | v0.3 prompt refresh + field-language policy | done |
| Phase 2c | Prompt/data-model contract refactor | planned |
| Phase 3 | Repair/retry policy from quality metrics | pending |
| Phase 4 | Soft typing (identity-gated facets) | pending |
| Phase 5 | Richer hints & known/new flagging | pending |
| Phase 6 | Higher-order reference detection | pending |
