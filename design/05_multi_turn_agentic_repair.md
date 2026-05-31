# Multi-Turn Conversations & Agentic Repair Loop

## Context

The current pipeline treats every LLM call as single-turn: `complete_json(system_prompt, user_payload)` builds fresh `[system, user]` messages, gets a response, validates, and aborts on any error. This is wasteful in three ways:

1. **Validation failures discard all good work.** A single `missing_source_block_refs` at `concepts[64]` aborts the entire 36-second grouping pass.
2. **KV cache is never reused.** Repair would re-send the same system prompt + unit text + concepts/items (10K+ tokens), instead of a 200-token error list.
3. **Per-segment extraction is sequential** despite being embarrassingly parallel.

The fix: multi-turn conversations as the backbone, with an agentic validation-repair loop (deterministic auto-fix → compact LLM repair with KV-cache reuse → full retry as last resort).

## Design

### Context structure

Two distinct categories — only `messages` goes to the LLM API:

```
ConversationContext (new file: tilusion/conversation.py)

  ── LLM context (sent to API, KV-cached by the model server) ──
  messages: [{role, content}]     # OpenAI-format, grows each turn
                                  #   Turn 1: [system, user(payload)]
                                  #   Turn 2: [..., assistant(resp1), user(repair)]
                                  #   Turn N: ...
                                  # This array IS the LLM context. Only content
                                  # hashes matter for KV-cache prefix match.

  ── Local app state (never sent to LLM, for caching/debug/replay) ──
  conversation_id: str            # content hash of initial prompt+payload+model
                                  #   Used as cache key on disk, never in API call
  model_identity: str             # backend.model_identity (for cache key)
  pass_name: str                  # "per-segment-extraction" | "unit-logical-grouping"
  turn_count: int                 # how many user→assistant exchanges so far
  initial_system_prompt: str      # saved for full retry (new conversation)
  initial_payload: dict           # saved for full retry
  turn_metadata: [TurnMetadata]   # per-turn: turn_index, turn_type, auto_fixes_applied,
                                  #   validation_report, elapsed_ms
                                  #   Purely for observability, never in API call
```

**KV-cache reuse semantics:** The DeepSeek API caches the `messages` array prefix server-side. As long as the first N messages are byte-identical between calls, the KV cache hits for those messages. Our repair turns always prepend the same `[system, user(payload), assistant(resp1), ...]` prefix and only append a new `user(repair)` + `assistant(patch)`. So turns 2+ only pay for the new repair message tokens. The app-level fields (`conversation_id`, `turn_count`, `pass_name`) never enter the messages array, so they cannot invalidate the prefix match.

Serializable via `to_dict()`/`from_dict()` — saved as `conversation.json` alongside existing pass artifacts.

### Backend evolution (modify: tilusion/backend.py)

Extend `LLMBackend` Protocol with two new methods:

```python
def start_conversation(self, system_prompt, user_payload, *, pass_name="") -> ConversationContext
def continue_conversation(self, conversation, user_message) -> ConversationContext
```

- `DeepSeekBackend`: Extract shared `_build_request_kwargs(messages)` and `_call_with_retry(kwargs)` from `complete_json()`. `start_conversation` builds `[system, user]` → calls API → appends assistant response. `continue_conversation` appends user message → calls API with full message history → appends assistant response. `response_format: json_object` on all turns.
- `MockReadingBackend`: `start_conversation` delegates to `complete_json()` and wraps result in a ConversationContext. `continue_conversation` echo-repairs.
- `complete_json()` remains untouched for backward compatibility.

### Deterministic auto-fixer (new file: tilusion/repair.py)

`DeterministicAutoFixer` — registry of fix functions keyed by error code. Fixes are free (no LLM) and safe (only mechanical corrections):

| Code | Fix |
|---|---|
| `invalid_ref` / `unknown_ref` | Remove from parent list |
| `empty_string_list_item` | Filter out empty strings |
| `wrong_field_type` | Coerce types where safe |
| `duplicate_object_id` | Append dedup suffix |
| `missing_required_field` | Insert default value |
| `schema_version_mismatch` | Set to current version |
| `stale_core_field` | Remove stale key |
| `missing_source_block_refs` | Inherit from merged source concepts' source_block_refs (for merge/split deltas) |

Not auto-fixable: `invalid_grounding`, `invalid_type_string`, `prior_context_used_as_evidence`, `empty_ref_list` on required fields — these go to LLM repair.

### Agentic repair loop (new file: tilusion/repair.py)

```
run_agentic_pass(backend, prompt, payload, validation_subject_builder, ...)
  │
  ├─ Turn 1: backend.start_conversation(prompt, payload)  ← KV cache populated
  │     └─ parse → initial_data
  │
  └─ _repair_loop(initial_data, conversation):
       │
       ├─ validate → passed? return data
       ├─ auto_fix errors → re-validate → passed? return data
       ├─ max_repair_turns exhausted? → full retry (new conversation)
       │
       └─ build_repair_message(errors)  ← ~200 tokens
           └─ backend.continue_conversation(conversation, repair_msg)  ← KV cache hit
               └─ parse repair patch → apply to data → loop
```

**Repair message format** (compact, KV-cache friendly):
```json
{"task": "repair_extraction", "errors": [{"code": "...", "path": "...", "message": "...", "repair_hint": "..."}], "instruction": "Return repairs array with path/operation/value. Fix only reported errors."}
```

**Repair response format** (patch, not full regeneration):
```json
{"repairs": [{"path": "concepts[64].source_block_refs", "operation": "replace", "value": ["seg-0003-block-0001"]}], "explanation": "..."}
```

Patch is merged into the original data — good inference is never discarded.

### Full pipeline with multi-turn backbone

Each pass type gets its own conversation (own system prompt, own payload shape). The agentic loop pattern is identical across all three. Per-segment extractions are independent branches that can run in parallel. The deterministic merge between per-segment and grouping is pure code (no LLM).

```
run_reading_pipeline(book, unit_id)
│
├─ Step 1: Overview segmentation
│   Conversation A  (system: overview prompt)
│   ├─ Turn 1: [system][user: unit_text, unit_metadata] → [assistant: segments]
│   └─ repair loop (if validation fails)
│       ├─ auto-fix (deterministic, free)
│       └─ Turn 2..N: [user: errors] → [assistant: patch]  ← KV-cache hit
│   → resolved segments
│
├─ Step 2: Per-segment extraction  (parallel branches)
│   Conversation B1  (system: per-segment prompt)
│   ├─ Turn 1: [system][user: segment_1_text, blocks] → [assistant: concepts, items]
│   └─ repair loop (if validation fails)
│   Conversation B2  (runs in parallel with B1, B3...)
│   ├─ Turn 1: [system][user: segment_2_text, blocks] → [assistant: concepts, items]
│   └─ repair loop
│   Conversation B3 ...
│   → all segment results
│
├─ Deterministic merge  (no LLM — pure Python)
│   merge_segment_extraction_results()
│   → merged concepts, stabilized items, unresolved_items
│
├─ Step 3: Unit logical grouping
│   Conversation C  (system: grouping prompt)
│   ├─ Turn 1: [system][user: unit_text, concepts, items] → [assistant: deltas, groups]
│   └─ repair loop (if validation fails)
│       ├─ auto-fix (deterministic, free)
│       ├─ Turn 2..N: [user: errors] → [assistant: patch]  ← KV-cache hit
│       └─ exhausted? → full retry (new Conversation C')
│   → post-processing (delta screening, dedupe, remap)  ← deterministic
│   → validated unit package
│
└─ Final validation → write unit_package.json
```

Key points:
- **Conversations A, B*, C are independent** — different system prompts, different payload shapes. Cross-conversation KV-cache sharing is not supported by the OpenAI/DeepSeek API (prefix match operates within a single `messages` array; different system prompts → different position-0 content → no match). See [Cross-conversation KV-cache analysis](#cross-conversation-kv-cache-analysis) below for trade-offs and future options.
- **Conversations B1..Bn are independent** — each segment has its own text, blocks, and conversation. They share the same system prompt but different user payloads → different KV caches. Safe to run in parallel.
- **Repair loop is identical pattern** in all three pass types, implemented by the same `run_agentic_pass()` function.
- **Deterministic steps** (merge, post-processing) are pure Python, no LLM cost.
- **Only the grouping pass has the merge delta post-processing** (screening, dedupe, remap) that wraps around the agentic loop.

### Cross-conversation KV-cache analysis

**Problem:** Conversation A (overview) and Conversation C (grouping) both include `unit_text` in their first user message (~3-5K tokens for typical units). Can we avoid paying for it twice?

**Technical constraint:** The OpenAI/DeepSeek KV-cache prefix match operates within a single `messages` array. Conversation A has `system: overview prompt`; Conversation C has `system: grouping prompt`. Their message arrays differ at position 0, so no cross-conversation cache sharing is possible with the current API.

**Practical assessment:** For typical units (~15K chars ≈ 3-5K tokens), paying for `unit_text` twice is minor. The real savings come from repair turns *within* each pass, where we avoid re-sending the full 10K+ token payload and instead send ~200-token error lists. A single avoided full-retry on the grouping pass saves more tokens than a year of duplicated `unit_text`.

**Future optimization — merged conversation (not in v1):** If token costs become a concern, overview and grouping could be merged into a single multi-turn conversation:

```
Conversation AC  (system: unified "reading extraction agent" prompt)
├─ Turn 1: [system][user: overview task + unit_text] → [assistant: segments]
│   ← KV cache populated with system + unit_text
├─ (per-segment extraction runs in parallel, separate conversations)
├─ (deterministic merge runs, producing concepts + items)
└─ Turn 2: [user: grouping task + concepts + items] → [assistant: deltas, groups]
    ← KV-cache HIT on system + unit_text prefix
```

Trade-off: the unified system prompt must cover two distinct tasks (segmentation + grouping), likely making it longer and less specialized. This is a prompt engineering problem — worth exploring if token costs dominate, but not required for v1. The current two-conversation design prioritizes prompt clarity and task separation.

### Pipeline integration (modify: tilusion/reading_pipeline.py)

- `run_per_segment_extraction_pass()`: Replace direct `backend.complete_json()` + `_raise_on_validation_errors()` with `run_agentic_pass()`. The `validation_subject_builder` wraps the LLM data with source_blocks for validation context.
- `run_unit_logical_grouping_pass()`: Same pattern. The `validation_subject_builder` includes deterministic post-processing (delta screening, application, dedupe, remap) so the validator sees the final state.
- Per-segment loop: `ThreadPoolExecutor` with `max_workers=min(len(segments), 4)` for I/O-bound parallel extraction.
- `_raise_on_validation_errors()` kept only at the pipeline level (line 1163) as final safety check.

### Cache changes (modify: tilusion/pass_utils.py)

Add `"conversation"` key to `pass_artifact_paths()`. Cache key unchanged (still hashes initial prompt+payload+model). On cache hit, load both `result.json` and `conversation.json`.

## Implementation Sequence (6 commits)

| # | Commit | Files |
|---|---|---|
| 1 | `tilusion/conversation.py` — ConversationContext + TurnMetadata dataclasses with serialization | new file + test |
| 2 | Backend protocol + implementations — extend LLMBackend, refactor DeepSeekBackend, update MockReadingBackend, add conversation.json to artifact paths | backend.py, reading_pipeline.py, pass_utils.py + tests |
| 3 | `tilusion/repair.py` — DeterministicAutoFixer with per-code fix functions and path accessors | new file + test |
| 4 | Agentic repair loop — `run_agentic_pass()`, `_repair_loop()`, repair message builder, patch merger | repair.py + test |
| 5 | Pipeline integration — wire agentic loop into per-segment and grouping passes, remove `_raise_on_validation_errors` from pass functions | reading_pipeline.py + tests |
| 6 | Parallel per-segment extraction — ThreadPoolExecutor for segment loop | reading_pipeline.py |

## Verification

1. **Unit tests**: Each commit has dedicated tests — ConversationContext round-trip, auto-fixer per-code fixes, repair loop with mock backend, pipeline integration with mock data.
2. **No regression**: `complete_json()` is untouched. Existing pipeline tests pass with mock backend (which returns valid data, so repair loop is a no-op — no turns needed).
3. **Integration**: Run `python -m tilusion.cli run-reading book.txt unit-0002 --backend mock` — should complete with 0 validation errors (mock data is always valid).
4. **Real-world**: Run on unit-0003 with DeepSeek backend — the `missing_source_block_refs` error should trigger auto-fix (inherit from merged concepts' refs) or compact repair turn, then pass.
