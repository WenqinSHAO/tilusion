# Progress

## Done

- Design intent aligned and recorded in the planning docs.
- Repo initialized; planning baseline committed in `975ef8b`.
- Reader slice implemented and refined across:
  - `f4d1d3d` initial TXT/EPUB structural reader
  - `3fa276b` index attribute enrichment
  - `625ec2a` unit ID normalization
  - `c15cf0c` reader schema and extraction usage documentation
- Current reader contract and schema live in `docs/reader_index_schema.md`.
- Extraction planning now lives in `docs/extraction_roadmap.md`.
- Extraction roadmap updated to define thread candidates, context-aware local extraction, pass dependencies, hybrid segmentation, and cache keys.
- First extraction skeleton added: local bundle pass, prompt envelope, mock/DeepSeek backend boundary, cache keying, CLI `run-pass`.
- DeepSeek backend updated to use the official OpenAI SDK pattern, defaulting extraction to `deepseek-v4-flash` with JSON mode and optional thinking controls.
- Segment extraction prompt externalized into a versioned prompt file; model payload now omits pipeline audit metadata and gives clearer guidance on evidence spans, local IDs, locator reconstruction, and alias candidates.
- Extraction calls now use a larger default output cap, preflight estimated context/output budgets, and report truncated or malformed JSON as actionable extraction failures.
- Extraction roadmap now records the next implementation direction: first-pass LLM extraction followed by deterministic validation, targeted repair, and re-validation gates.
- Reader scope is now stable enough for later modules to consume:
  - structure index for `.txt` and `.epub`
  - normalized opaque unit IDs
  - per-unit navigation metadata
  - on-demand text extraction for a structural unit
- Regression coverage exists for TXT heading/chunking/duplicate-TOC behavior and EPUB TOC/range reconciliation.

## Ongoing

- Goal: build extraction/analysis modules on top of the reader contract.
- Gap: no benchmark yet against a true 500MB input.
- Note: if a source edition mixes main text with notes/commentary inside one structural unit, that separation is a later extraction/analysis responsibility rather than a reader responsibility.
