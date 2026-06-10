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

**Foundation phases (1, 1.5, 2a, 2b) — DONE**
- Phase 1: embedding cache — `sha256(text)` keys, two-layer memory+disk.
- Phase 1.5: per-concept candidate maps with caps (5 embedding + 3 BM25-only).
- Phase 2a: quality metrics scaffolding (`tilusion/reading_quality.py`) — non-fatal field-language, type vocabulary, canonical-name, facet, group granularity checks.
- Phase 2b: v0.3 prompt refresh — field-language policy, stripped overhead, type vocabulary consolidation (narrowed to novels/essays: 7 preferred concept types, 9 item types, 8 group types), facet + canonical_name instructions, `reader_language` parameter.
- Phase 2c contract backbone: `tilusion/prompt_contracts.py` owns FieldRole, FieldMeta, PassContract, TypeVocabulary API and prompt-rendered language/type sections.
- Repair/merge hardening after Phase 2c: warning-level deterministic type normalization, fixed repair propagation, known-registry hint contract, generic alias-safe registry dedup, and skipped-merge logging.

**v0.3 extraction test run (units 2–4, 浮生六记)**
- Zero English output, 100% facet coverage, 76% canonical names, 3 non-standard types (was 25).
- Quality analysis documented in `design/20_v0.3_extraction_analysis.md`.
- Issues cataloged: prompt-fixable (5), pipeline/merge-fixable (3), LLM limitations (3), frontend (1).

**Plan restructured**
- `design/15_cross_unit_refactor_plan.md` reorganized into three parts: Foundation (done), Infrastructure (Phase 2c done), Iterative Quality Improvement (ongoing, replaces old Phases 3–6).
- `design/22_registry_merge_contract.md` documents the Part 3 merge/facet/soft-type contract and the next merge-observability roadmap.

**Regression coverage**
- 462 tests passing after Phase 2c and repair/merge hardening.

## Ongoing

- **Current goal:** Part 3 reorganized into three work groups: A (type accuracy), B (grouping quality), C (timeline structure). Groups A+B are independent and ship first. Timeline work (C) un-gated — merge infrastructure is mature enough.
- **Branch:** `prompt_refresh`.
- **Immediate next step:** Implement Work Group A (concept_type ← facets binding, ~2 lines) + Work Group B (ungrouped items, summary leak, theme_set guidance, ~20 lines). Then Work Group C (timeline continuation, ~40 lines).

## Plan Structure (from `design/15_cross_unit_refactor_plan.md`)

| Part | What | Status |
|------|------|--------|
| 1. Foundation | Phases 1, 1.5, 2a, 2b | Done |
| 2. Infrastructure | Phase 2c: prompt/data-model contracts | Implemented and hardened |
| 3. Iterative quality | Ongoing improvement from issue catalogs | Starting with merge observability; see `design/22_registry_merge_contract.md` |
