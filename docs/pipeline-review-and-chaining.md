# Pipeline Review & Run-All Chaining Plan

## Current State

The extraction pipeline has 6 passes orchestrated via 10 CLI subcommands. Each pass is independently cacheable and runnable, but there is no unified runner. Users must manually chain: `run-chain` → `finalize-unit` → `repair-unit` → `timeline-unit` → `repair-timeline`, passing cache directories between commands.

### Pass Inventory

| # | Pass | CLI command | Input | Output |
|---|------|-------------|-------|--------|
| 1 | Overview segmentation | `run-chain` (internal) | Unit text | Segment proposals |
| 2 | Segment extraction | `run-chain` (internal) | Segment text + hints | Per-segment records |
| 3 | Unit finalization | `finalize-unit` | Chain cache dir | `unit_records` (entities, locations, events, threads) |
| 4 | Unit repair | `repair-unit` | Finalization pass dir | Repaired `unit_records` |
| 5 | Timeline construction | `timeline-unit` | Repair pass dir | `timelines` array (partial-order DAG) |
| 6 | Timeline repair | `repair-timeline` | Timeline pass dir | Repaired `timelines` |

### Artifact Layout

Each pass writes to `.tilusion_cache/` with SHA256-based cache keys:
- `extraction_chains/<chain_key>/` — overview + segment passes + chain manifest
- `extraction_chains/<chain_key>/unit_finalization/<pass_key>/` — finalization
- `extraction_chains/<chain_key>/unit_finalization/<pass_key>/unit_repair/<pass_key>/` — repair
- `.../unit_repair/<pass_key>/unit_timeline/<pass_key>/` — timeline
- `.../unit_timeline/<pass_key>/unit_timeline_repair/<pass_key>/` — timeline repair

---

## Issues Found

### A. No Progress Logging (Critical UX Gap)

Every CLI command is silent until it prints the final JSON record to stdout. The user has no visibility into:
- Which pass is currently running
- Whether the pass hit local cache or is making an LLM call
- How long each pass took
- Whether a pass failed and the pipeline stopped

**Fix:** Add structured progress lines to stderr. Each pass emits one line on start (pass name, cache status) and one on completion (elapsed, pass/fail). Example:

```
[1/5] overview+segments (chain)... cache hit (0.2s)
[2/5] unit finalization... LLM call (12.3s)
[3/5] unit repair... skipped (ready_for_llm_repair=false)
[4/5] timeline construction... LLM call (8.1s)
[5/5] timeline repair... skipped (0 errors)
Done. unit_extraction: .tilusion_cache/extraction_chains/<key>/unit_extraction.json
```

### B. `run-all` Command Missing

No single command runs the full pipeline. Users must execute 3-5 CLI commands in sequence, passing cache directory paths between them. This is error-prone and unsuitable for batch processing.

**Fix:** Add `run-all` subcommand that orchestrates all passes, respecting cache and `ready_for_llm_repair` gates.

### C. `ready_for_llm_repair` Computed But Never Checked

`build_chain_repair_hints()` computes `ready_for_llm_repair` and writes it to `repair_hints.json`. The flag is accurate — it reflects whether any segment or overview has LLM-actionable issues. But no CLI command or pipeline function checks it before running a repair pass. Users can waste LLM calls on repair passes that have nothing to repair.

Similarly, `run_unit_timeline_repair_pass` always runs if invoked, even when the timeline validation report has zero errors.

**Fix:** `run-all` checks these flags and skips unnecessary repair passes with a log message. Individual CLI commands (`repair-unit`, `repair-timeline`) also check and print a warning if the repair is likely unnecessary, but still run if explicitly invoked (user may want to retry with different model/settings).

### D. No Retry on Transient LLM Failures

`DeepSeekBackend.complete_json()` has no retry logic. Network errors, rate limits (429), or temporary API outages cause immediate failure. Given that `run-all` may run 3+ LLM calls in sequence, a single transient failure aborts the entire pipeline.

**Fix:** Add exponential backoff retry (3 attempts, 1s/2s/4s delay) for retryable errors (network errors, 429, 5xx). Non-retryable errors (auth failure, budget errors, finish_reason=length) fail immediately.

### E. No Timeout on LLM Calls

`OpenAI().chat.completions.create()` is called without a timeout. A hung API call blocks the pipeline indefinitely.

**Fix:** Pass `timeout=300` (5 minutes) to the OpenAI client constructor. This is generous for DeepSeek V4 but prevents indefinite hangs. Consider making it configurable via `--timeout` CLI flag on `run-all`.

### F. `chain_cache_key` Doesn't Include Overview Output Hash

`chain_cache_key()` in `extraction_pipeline.py:1278` hashes `unit_id + text + model_identity`. Segment passes are keyed by `segment_text + model_identity + prompt_hash`. But the overview output (which determines which segments exist and what hints they receive) is not part of the chain key. If the overview prompt or model changes, the chain cache key stays the same, and segment passes could hit stale caches that correspond to different overview segment boundaries.

**Fix:** Include the overview result hash in the chain cache key. This ensures that changing the overview prompt or model produces a fresh chain directory.

### G. Mock Backend Coverage Gaps

The mock backend dispatches on `user_payload.get("task")`:

| Task | Handler | Status |
|------|---------|--------|
| `overview_segmentation` | `mock_overview_response` | Done |
| `segment_extraction` | (default branch) | Done |
| `unit_finalization` | `mock_unit_finalization_response` | Done |
| `unit_repair` | Falls through to `unit_finalization` | **Gap** — repair should produce different output (resolved issues) |
| `unit_timeline` | `mock_unit_timeline_response` | Done |
| `unit_timeline_repair` | `mock_unit_timeline_repair_response` | Done |

**Fix:** Add a proper `mock_unit_repair_response` that resolves the repair targets in its output, making mock-backed repair pass tests meaningful.

### H. Token Estimation is Approximate

`estimate_deepseek_tokens()` uses `CJK * 0.6 + other * 0.3`. This is a rough heuristic. DeepSeek V4 uses a different tokenizer than the reference, so actual token counts can deviate 10-20%.

**Impact:** Budget checks may pass when actual token usage would exceed limits, or fail when the call would have succeeded.

**Fix:** Not urgent. The heuristic is conservative enough for current unit sizes. Revisit when processing very large units or when DeepSeek exposes a token-counting endpoint.

### I. No Unit-Level Accumulable Result Package

Each pass writes its own artifacts, but there's no single "unit extraction package" file that downstream consumers (UI, future extractions, cross-unit canonicalization) can read. The closest is `unit_extraction.json` in the timeline repair pass directory, but:
- The path is deeply nested under cache keys
- There's no manifest tying together the chain, finalization, repair, and timeline results
- A consumer needs to know which passes ran and where their outputs are

**Fix:** `run-all` writes a `unit_package.json` at a predictable path (e.g., `.tilusion_cache/units/<unit_id>/unit_package.json`) containing:
- Unit metadata (id, source, char count)
- Pass summary for each pass that ran (cache key, cache hit, elapsed, pass/fail)
- Pointers to key artifact files (unit_extraction.json, timeline_view.md, etc.)
- The complete `data` from the final pass (timeline repair, or timeline if repair skipped, or repair if timeline skipped)
- Validation summary

This is the single file the UI/app layer reads.

### J. Alignment with App Design Docs

The design docs (`docs/extraction_roadmap.md`) describe a 6-phase growth plan:

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Grounded local extraction | **Mostly done** — extraction + repair + validation loop working |
| 2 | Intra-unit grouping | **Done** — finalization merges segments, timeline pass orders events |
| 3 | Cross-unit canonicalization | **Not started** — needs canonical entities, locations, event linking |
| 4 | Temporal constraints | **Partially done** — timelines are unit-scoped DAGs, no cross-unit temporal edges |
| 5 | Review and correction infrastructure | **Not started** — no UI, no correction persistence |
| 6 | Schema growth | **Not started** — ontology induction postponed |

The 4 architectural layers from the project vision (Book → Analysis → Graph/Reasoning → Presentation):

| Layer | Status |
|-------|--------|
| Book (Reader) | Done — `BookIndex`, `extract_unit_text` |
| Analysis (Extraction) | Mostly done — 6-pass pipeline with validation |
| Graph/Reasoning | Partial — unit-scoped timeline DAGs, no cross-unit graph |
| Presentation | Not started — no UI, no viewer |

Immediate gaps before the UI layer can consume results:
1. **Unit package manifest** (Issue I above) — the UI needs a stable file to read
2. **Cross-unit context** — `prior_context` is designed but only wired for segment hints within a chain; not yet used for passing confirmed entities/events between units
3. **Correction persistence** — no mechanism to store human corrections and re-baseline extractions

These are phase 3-5 work and out of scope for this plan.

---

## `run-all` Command Design

### CLI Interface

```
tilusion-reader run-all <book> <unit_id> [options]
  --backend            mock|deepseek (default: mock)
  --model              (default: deepseek-v4-flash)
  --thinking           enable thinking mode
  --reasoning-effort   high|max (default: high)
  --max-tokens         (default: 32000)
  --cache-dir          (default: .tilusion_cache)
  --no-cache           disable all local caching
  --skip-repair        skip repair passes even if issues detected
  --timeout            LLM timeout in seconds (default: 300)
  --retries            max LLM retries on transient failure (default: 3)
```

### Execution Flow

```
run-all(book, unit_id, ...)
  │
  ├─[1] run-chain (overview + segments)
  │     Log: "[1/5] overview+segments... {cache_hit|LLM} ({elapsed})"
  │     On failure: abort, print error
  │
  ├─[2] finalize-unit
  │     Log: "[2/5] unit finalization... {cache_hit|LLM} ({elapsed})"
  │     On failure: abort, print error
  │
  ├─[3] repair-unit (conditional)
  │     Check: repair_hints.ready_for_llm_repair
  │     If false: Log "[3/5] unit repair... skipped (nothing actionable)"
  │     If true:  Log "[3/5] unit repair... {cache_hit|LLM} ({elapsed})"
  │     On failure: warn, continue with un-repaired data
  │
  ├─[4] timeline-unit
  │     Log: "[4/5] timeline construction... {cache_hit|LLM} ({elapsed})"
  │     On failure: abort, print error
  │
  ├─[5] repair-timeline (conditional)
  │     Check: timeline validation errors > 0
  │     If false: Log "[5/5] timeline repair... skipped (0 errors)"
  │     If true:  Log "[5/5] timeline repair... {cache_hit|LLM} ({elapsed})"
  │     On failure: warn, continue with un-repaired timeline
  │
  └─ Write unit_package.json
        Log: "Done. package: .tilusion_cache/units/<unit_id>/unit_package.json"
```

### Progress Logging Format

All progress lines go to stderr. The final JSON record still goes to stdout (backwards compatible). Format:

```
[<step>/<total>] <pass description>... <status> (<elapsed>)
```

Status is one of: `cache hit`, `LLM call`, `skipped (<reason>)`, `FAILED`.

### Unit Package Schema

```json
{
  "unit_id": "unit-0002",
  "source": {
    "book_path": "...",
    "char_count": 15720,
    "line_count": 458
  },
  "created": "2026-05-17T...",
  "passes": {
    "chain": {
      "cache_key": "abc123",
      "cache_hit": true,
      "elapsed_ms": 200,
      "segments_resolved": 14,
      "segments_total": 14
    },
    "finalization": {
      "cache_key": "def456",
      "cache_hit": false,
      "elapsed_ms": 12300
    },
    "repair": {
      "cache_key": "ghi789",
      "cache_hit": false,
      "elapsed_ms": 8900,
      "skipped": false,
      "issues_resolved": 3
    },
    "timeline": {
      "cache_key": "jkl012",
      "cache_hit": false,
      "elapsed_ms": 8100
    },
    "timeline_repair": {
      "skipped": true,
      "reason": "no timeline errors"
    }
  },
  "data": { /* complete extraction data from final pass */ },
  "validation": {
    "errors": 0,
    "warnings": 5,
    "passed": true
  },
  "artifact_paths": {
    "unit_extraction": ".tilusion_cache/.../unit_extraction.json",
    "timeline_view": ".tilusion_cache/.../timeline_view.md",
    "unit_package": ".tilusion_cache/units/unit-0002/unit_package.json"
  }
}
```

`data` contains the complete output from the last pass that ran: the full unit extraction with `entity_records`, `location_records`, `event_records`, `thread_records`, `timelines`, and all other top-level fields. This is the single object the UI renders.

---

## Implementation Plan

### Commit 1: Fix chain cache key to include overview hash

**Why first:** This is a correctness fix. Without it, changing the overview prompt or model can produce stale segment caches under the same chain key.

**Changes:**
- `extraction_pipeline.py`: `chain_cache_key()` accepts `overview_result_hash` parameter
- `run_chained_extraction()`: pass overview result hash after overview pass completes
- `refresh_chain_validation_cache()`: read overview hash from existing overview result

### Commit 2: Add retry and timeout to DeepSeekBackend

**Why second:** Prevents transient failures from aborting `run-all` mid-pipeline.

**Changes:**
- `extraction.py`: `DeepSeekBackend.__init__` accepts `timeout` and `max_retries`
- `extraction.py`: `DeepSeekBackend.complete_json` wraps API call with retry loop
- Retryable errors: `APIConnectionError`, `APITimeoutError`, `RateLimitError`, `InternalServerError`
- Non-retryable: `AuthenticationError`, `BadRequestError`, `ExtractionError` (finish_reason=length)
- CLI: add `--timeout` and `--retries` flags to all commands that use DeepSeek backend

### Commit 3: Add `run-all` command with progress logging

**Why third:** The main feature. Depends on commits 1-2 for correctness and reliability.

**Changes:**
- `extraction_pipeline.py`: Add `run_all_passes()` function that orchestrates the 5-step flow
- `extraction_pipeline.py`: Add `write_unit_package()` function
- `extraction_pipeline.py`: Add progress logging helper `_log_progress()` that writes to stderr
- `cli.py`: Add `run-all` subcommand
- `cli.py`: Wire `--skip-repair`, `--timeout`, `--retries` flags

### Commit 4: Check `ready_for_llm_repair` gates in individual CLI commands

**Why fourth:** Makes individual CLI commands smarter about skipping unnecessary work. Also needed for `run-all` to decide whether to run repair.

**Changes:**
- `cli.py`: `repair-unit` reads `repair_hints.json` and warns if `ready_for_llm_repair` is false (but still runs if explicitly invoked)
- `cli.py`: `repair-timeline` checks timeline validation report error count and warns if zero
- `extraction_pipeline.py`: `run_unit_repair_pass` accepts optional `skip_if_unnecessary` flag
- `extraction_pipeline.py`: `run_unit_timeline_repair_pass` accepts optional `skip_if_unnecessary` flag

### Commit 5: Add mock_unit_repair_response for test coverage

**Why last:** Closes the mock coverage gap. Tests can exercise the full repair flow with mock backend.

**Changes:**
- `extraction.py`: Add `mock_unit_repair_response(user_payload)` that resolves `repair_targets`
- `extraction.py`: Add `"unit_repair"` branch in `MockExtractionBackend.complete_json`
- `tests/test_extraction.py`: Update repair pass tests to verify mock repair actually resolves issues

---

## What's NOT in This Plan

- **Cross-unit canonicalization** (Phase 3) — needs its own design pass
- **Correction engine / persistence** (Phase 5) — needs UI layer decisions first
- **Token estimator improvement** — low impact, revisit with large units
- **Parallel segment extraction** — segments run sequentially; KV cache sharing makes parallel less valuable, but could revisit for throughput
- **Incremental re-extraction** (only re-run changed segments) — designed in roadmap, not yet needed
- **Best-of-N or majority voting** — designed in KV cache doc, not yet needed
- **UI / Presentation layer** — separate project phase

---

## Verification

1. Run all existing tests (42 tests, must stay green)
2. Run `run-all --backend mock` on unit-0002 — verify all 5 steps execute with cache hits after first run
3. Run `run-all --backend deepseek` on unit-0002 — verify progress logging, elapsed times
4. Test retry behavior: simulate transient failure with a short timeout
5. Test skip behavior: run on a chain with `ready_for_llm_repair=false`, verify repair is skipped
6. Inspect `unit_package.json` — verify all fields present, paths valid
