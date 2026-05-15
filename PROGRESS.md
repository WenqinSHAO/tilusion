# Progress

## Done

- Reviewed and aligned the initial design pack.
- Confirmed product shape: local-first, single-user, CLI-first MVP.
- Confirmed scope boundary: analyze only up to the user-selected position; no hypothetical future or what-if timeline features in MVP.
- Confirmed architecture constraint: keep raw extraction outputs, canonical state, and user correction operations as distinct layers.
- Recorded current LLM backend assumption from `AGENT.md`: DeepSeek `v4 pro` and `flash` via `DS_API_KEY`.
- Updated design docs to capture these decisions.
- Initialized the git repository and committed the planning/design baseline in `975ef8b`.
- Added a first reader slice:
  - streamed TXT indexing with encoding detection and byte-range locators;
  - EPUB indexing via `container.xml` -> OPF -> spine/TOC with reading-order locators;
  - CLI commands for structure indexing and unit extraction;
  - JSON output for programmatic use and text outline output for human use.
- Added `.gitignore` for local book fixtures and common Python cache files.
- Added focused tests for TXT heading detection, TXT fallback chunking, and EPUB TOC-based indexing.
- Validated the CLI against sample books in `books/`, including a large EPUB with a substantial TOC.
- Fixed TXT indexing to filter dense duplicate TOC-style heading blocks that were being mistaken for primary structure.
- Fixed EPUB indexing to reconcile suspicious TOC targets against actual spine document titles and to normalize `*-back` anchors to the enclosing heading start.
- Hardened EPUB XHTML decoding for files without explicit charset metadata and added regression tests for the indexing/extraction issues found during sample-book validation.

## Ongoing

- Goal: harden the reader contract around structural-range extraction and large-input behavior.
- Gap: no benchmark yet against a true 500MB input, and chapter extraction currently returns the edition's full chapter text as present in source, including commentary/notes when the source edition embeds them inside the same structural unit.
