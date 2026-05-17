# Progress

## Done

- Planning baseline and initial design intent committed in `975ef8b`; design docs live under `design/`.
- Reader foundation is usable for later modules. Key commits: `f4d1d3d` initial TXT/EPUB reader, `3fa276b` index attribute enrichment, `625ec2a` unit ID normalization, `c15cf0c` reader schema docs.
- Current reader contract lives in `docs/reader_index_schema.md`: structure index, normalized opaque unit IDs, navigation metadata, and on-demand unit text extraction.
- Extraction direction is documented in `docs/extraction_roadmap.md`. Key commits: `b82f5ee` extraction roadmap, `2165fbf` context-aware pass strategy, `4b5632a` validation-and-repair loop.
- First extraction skeleton is in place. Key commits: `4c6690d` local extraction pipeline skeleton, `ef1f265` DeepSeek SDK backend, `5934ceb` versioned segment prompt and extraction failure handling.
- Regression coverage exists for reader behavior and the first extraction skeleton.

## Ongoing

- First-pass segment extraction prompt is done enough for live trials, but its output is draft quality.
- Segment extraction prompt is being tightened using validator feedback, starting with explicit exact-quote and surface-in-evidence rules.
- Current next step: grow deterministic validation into a validation/repair loop. Detailed plan: `docs/extraction_roadmap.md`.
- Planned extraction loop now uses multiple pass sizes: whole-unit overview, chunk-level detailed extraction, chunk-level repair/validation, and whole-unit QC.
- Prompt design should move toward composable versioned parts for extraction guides, context, existing records, validation feedback, repair, segmentation, and QC.
- First deterministic validation slice now has a dedicated quality module with evidence relocation, response-local ID/reference integrity, evidence span length, object-surface grounding, and compact LLM repair payloads.
- Multi-pass scaffolding has started: `run-pass` now emits a pass record and caches prompt composition, request payload, raw response, parsed result, validation report, and manifest under `.tilusion_cache/extraction_passes/`.
- First chained extraction flow is available via `run-chain`: overview segmentation, deterministic segment-anchor relocation, per-segment extraction with overview hints, aggregate validation, and repair-hint artifacts. LLM repair/review/QC passes are still intentionally separate follow-up work.
- Validation outputs now separate full local reports, compact LLM-actionable repair hints, and enriched validated results with evidence source locations.
- Existing chain caches can be revalidated without backend calls via `refresh-chain-validation <chain_cache_dir>`.
- Next extraction task: add the LLM-powered repair pass that consumes compact repair hints plus original text/result context.
- Validation output audit (2026-05-17) identified three gaps before the LLM repair pass. All three (A: segment quality overview, B: unified relocation, C: non-actionable warning summary) are now implemented in commits `69aab40`, `ac66822`, `9db21bb`.
- First LLM-backed chain trial (2026-05-17) over unit-0002 (浮生六记, ~15.7K chars) completed: 6/6 segments resolved, 0 errors, 16 non-actionable surface_not_in_evidence_context warnings. Trial surfaced five improvement areas documented in `docs/extraction_roadmap.md#first-llm-backed-chain-trial-findings-2026-05-17`: (1) separate canonical_name from surface in schema, (2) evidence-granularity guidance in prompts, (3) CJK-aware surface validation, (4) cross-segment entity aliasing in chain QC, (5) CJK sentence-boundary context windows.
- Second LLM-backed chain trial with v0.5 prompt restored 14/14 segments and exposed non-blocking finish/QC issues: one segment overlap, four missing/ellipsized evidence quotes, two empty evidence refs, one ambiguous short quote, one malformed evidence ref, and three non-actionable surface warnings. Details: `docs/extraction_roadmap.md#second-llm-backed-chain-trial-follow-up-issues-2026-05-17`.
- Reader remains intentionally neutral about main text vs notes/commentary; separating those is an extraction/analysis responsibility.
- Still untested at true 500MB scale.
