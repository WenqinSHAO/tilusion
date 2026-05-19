# Progress

## Done

**Planning and reader foundation**
- Planning baseline and initial design intent (`975ef8b`); design docs under `design/`.
- Reader foundation: TXT/EPUB reader (`f4d1d3d`), index attribute enrichment (`3fa276b`), unit ID normalization (`625ec2a`), reader schema docs (`c15cf0c`).
- Reader contract: `docs/reader_index_schema.md` — structure index, normalized opaque unit IDs, navigation metadata, on-demand unit text extraction.

**Extraction pipeline — first skeleton**
- Extraction roadmap (`b82f5ee`), context-aware pass strategy (`2165fbf`), validation-and-repair loop design (`4b5632a`).
- Pipeline skeleton (`4c6690d`), DeepSeek SDK backend (`ef1f265`), versioned segment prompt and extraction failure handling (`5934ceb`).
- First deterministic validation slice: evidence relocation, response-local ID/reference integrity, evidence span length, object-surface grounding, compact LLM repair payloads (`f957a5e` onward).

**Multi-pass extraction and chain flow**
- Multi-pass scaffolding: `run-pass` caches prompt, payload, raw response, parsed result, validation report, and manifest under `.tilusion_cache/extraction_passes/` (`f5adf58`).
- First chained extraction flow (`run-chain`): overview segmentation, deterministic segment-anchor relocation, per-segment extraction with overview hints, aggregate validation, repair-hint artifacts (`09ec7a7`).
- Validation outputs separated into full local reports, compact LLM-actionable repair hints, and enriched validated results with evidence source locations (`5b7a8a2`).
- Chain cache revalidation without backend calls via `refresh-chain-validation` (`d83b15d`).

**Validation refinement and chain trials**
- Validation output audit gaps (A: segment quality overview, B: unified relocation, C: non-actionable warning summary) all resolved (`69aab40`, `ac66822`, `9db21bb`).
- First LLM-backed chain trial (unit-0002, ~15.7K chars): 6/6 segments resolved, 0 errors. Trial surfaced five improvement areas: canonical_name, evidence-granularity guidance, CJK surface validation, cross-segment entity aliasing, CJK sentence-boundary context windows (`651e95f`).
- Second chain trial (v0.5 prompt): 14/14 segments restored, non-blocking QC issues documented (`b0557ec`).

**Unit finalization, repair, and timeline construction**
- Prompt composition strategy for LLM KV cache reuse: static system prompts + shared source text prefixes for cheap multi-round refinement (`694ad30`).
- Unit-finalization pass (`finalize-unit`): cross-segment merge, alias resolution, duplicate detection, source-navigable unit artifacts (`7d209e2`, `67ed913`).
- Unit repair pass (`repair-unit`): KV cache prefix sharing with finalization pass, repair-specific instructions and `repair_targets`. Trial on unit-0002 repaired all 3 blocking concerns, reduced unresolved items 8→5 (`ae3eea2`).
- Timeline construction pass (`timeline-unit`): partially-ordered timelines with DAG-structured `before_events` edges. Trial on unit-0002 produced 3 timelines covering 40/43 events (`14b96c0`).
- Timeline repair pass with KV cache prefix sharing (`3fe179e`).
- `run-all` command: end-to-end orchestration with progress logging and unit package output (`4129aba`).

**Reader view generator**
- Self-contained HTML demo consuming real extraction data (`7ccf03c`).
- Usage: `python tools/generate_reader_view.py <unit_package.json> <resolved_segments.json> <book.txt> -o reader_view.html`
- Template: `tools/reader_view_template.html` (CSS + JS skeleton). Known UI notes: `docs/reader_view_notes.md`.

**Regression coverage**
- Tests for reader behavior and extraction pipeline (`pytest tests/test_extraction.py -x -q`).

**Cross-unit readiness scaffold**
- Cross-unit/context planning doc (`743827f`, `566d166`): `docs/cross_unit_extraction_plan.md`.
- No-behavior extraction refactor split prompt composition, payload builders, and unit validation from the pipeline orchestrator (`8f25370`, `5aa6c4f`, `2bb59ca`, `e494dfe`).
- Passive book-context scaffold and `run-all` artifact wiring: empty book-state snapshot, `context_pack.json`, `context_selection_report.json`, and unit-package metadata with prompt injection disabled (`fb7f969`, `ab44208`).

## Ongoing

- Extraction pipeline is roughly stitched end-to-end but needs polishing: prompt quality, edge cases, and tighter validation.
- Prompt design toward composable versioned parts (extraction guides, context, validation feedback, repair, segmentation, QC).
- Current goal: deterministic cross-unit context selector over passive book-state artifacts; planning doc: `docs/cross_unit_extraction_plan.md`.
- Extraction adjustment based on user input prompts.
- Reader remains intentionally neutral about main text vs notes/commentary; separating those is an extraction responsibility.
- Still untested at true 500MB scale.
