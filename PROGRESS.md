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
- Phase 2c API design: `design/19_phase_2c_contract_api.md` — FieldRole, FieldMeta, PassContract, TypeVocabulary API.

**v0.3 extraction test run (units 2–4, 浮生六记)**
- Zero English output, 100% facet coverage, 76% canonical names, 3 non-standard types (was 25).
- Quality analysis documented in `design/20_v0.3_extraction_analysis.md`.
- Issues cataloged: prompt-fixable (5), pipeline/merge-fixable (3), LLM limitations (3), frontend (1).

**Plan restructured**
- `design/15_cross_unit_refactor_plan.md` reorganized into three parts: Foundation (done), Infrastructure (Phase 2c), Iterative Quality Improvement (ongoing, replaces old Phases 3–6).

**Regression coverage**
- 423 tests passing.

## Ongoing

- **Current goal:** Implement Phase 2c (contract refactor) as backbone infrastructure, then begin iterative quality improvement from the issue catalog.
- **Branch:** `prompt_refresh`.
- **Immediate next step:** Implement Phase 2c — `tilusion/prompt_contracts.py`, update `reading_prompts.py` builders, shrink prompt files to pass-specific content only.

## Plan Structure (from `design/15_cross_unit_refactor_plan.md`)

| Part | What | Status |
|------|------|--------|
| 1. Foundation | Phases 1, 1.5, 2a, 2b | Done |
| 2. Infrastructure | Phase 2c: prompt/data-model contracts | Designed, not implemented |
| 3. Iterative quality | Ongoing improvement from issue catalogs | Catalog #1 at `design/20_v0.3_extraction_analysis.md` |
