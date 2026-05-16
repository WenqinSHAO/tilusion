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
- Next validation task: split full local validation reports from compact LLM repair payloads and enriched locator metadata; current `surface_not_in_cited_evidence` warnings are too broad for direct LLM repair use.
- Reader remains intentionally neutral about main text vs notes/commentary; separating those is an extraction/analysis responsibility.
- Still untested at true 500MB scale.
