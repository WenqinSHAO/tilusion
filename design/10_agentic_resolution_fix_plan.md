# Agentic Registry Resolution Fix Plan

## Status

Commit `347140c` implemented the first agentic cross-unit resolution path:
multi-turn markdown tool calls, registry search tools, group semantic
shortlisting, and v0.2 concept/group resolution prompts. The architecture is
usable as a direction, but the current implementation has correctness gaps at
the boundary between:

- raw LLM proposal JSON,
- applied validation subjects,
- pass artifact data,
- and `compute_registry_delta()`.

This plan defines the fix sequence before running more real book-scope
extraction.

## Release-Blocking Fixes

### 1. Preserve Raw Proposals Separately From Applied Subjects

**Problem:** `run_agentic_resolution_pass(..., return_subject=True)` returns the
validation subject when validation passes. The subject contains applied
`concepts` or `logical_groups`, but not the raw `resolution_proposals` or
`group_resolution_proposals`. The calling passes then read proposals from that
subject and silently save empty proposal lists.

**Impact:** LLM-confirmed cross-unit `link`, `continue`, and `mutate` proposals
are lost before `compute_registry_delta()`. Registry updates can degrade back to
deterministic exact matching.

**Fix:**

- Introduce an explicit return shape for agentic resolution:

  ```python
  @dataclass(slots=True)
  class AgenticResolutionResult:
      raw_data: dict[str, Any]
      applied_subject: dict[str, Any]
      conversation: ConversationContext
      validation_report: ReadingValidationReport
      turns_used: int
      exhausted: bool = False
  ```

- Keep `run_agentic_resolution_pass()` responsible for tool execution and final
  response validation, but stop overloading `return_subject`.
- In `run_cross_unit_concept_resolution_pass()`:
  - use `result.applied_subject["concepts"]` for resolved concepts,
  - use `result.raw_data["resolution_proposals"]` for artifacts, implicit refs,
    and registry delta input.
- In `run_cross_unit_group_resolution_pass()`:
  - use `result.applied_subject["logical_groups"]` for updated groups,
  - use `result.raw_data["group_resolution_proposals"]` for artifacts and
    registry delta input.
- Add regression tests that assert non-cache agentic concept/group runs preserve
  proposal lists and feed them into registry delta.

### 2. Accept Only Complete Agentic Final Responses

**Problem:** If the model keeps emitting `tool_calls` until the turn budget is
exhausted, the loop exits and validates the last parsed object. If that object
only has `tool_calls`, the validation subject builder may apply no proposals and
still pass package validation.

**Impact:** An unfinished protocol can be accepted as a successful empty
resolution.

**Fix:**

- Treat a response as complete only when:
  - `status == "complete"`,
  - no `tool_calls` are present,
  - the required proposal key is present for the task.
- If the loop exits with pending `tool_calls`, set `exhausted=True` and do not
  validate it as a final response.
- Add tests for:
  - max-turn exhaustion with continuing `tool_calls`,
  - malformed final response missing proposal key,
  - final response containing both `status: complete` and `tool_calls`.

### 3. Use A True Single-Pass Fallback

**Problem:** The current fallback reuses the v0.2 prompt with `run_agentic_pass`.
That runner does not execute registry tools, while the v0.2 prompt tells the
model it can call tools.

**Impact:** Fallback can repeat the same tool-calling failure instead of falling
back to the known single-pass baseline.

**Fix:**

- On agentic failure, rebuild the corresponding v0.1 prompt:
  - `build_concept_resolution_composition()`,
  - `build_group_resolution_composition()`.
- Run the existing `run_agentic_pass()` repair loop with that prompt and the
  same payload.
- Record fallback metadata in the pass result:
  - `agentic_status: "complete" | "fallback" | "failed"`,
  - `agentic_turns_used`,
  - `agentic_failure_reason`.

### 4. Repair Prompt/Code Contract Mismatches

**Problem:** The v0.2 prompts say to "emit proposals incrementally" but also say
tool-calling turns must contain only `tool_calls`. The implementation does not
accumulate partial proposals across turns.

**Impact:** The prompt can encourage behavior the code ignores.

**Fix:**

- Remove "emit proposals incrementally" from the v0.2 prompts.
- Replace it with:
  - "Make clear decisions internally as you go."
  - "Emit all proposals only in the final `status: complete` response."
- Update `render_tool_definitions_markdown()` so the generic tool text is
  task-neutral. It currently mentions only `resolution_proposals`, which is
  inaccurate for group resolution.

## Retrieval And Runtime Improvements

### 5. Cache Registry Search Material

**Problem:** `search_concepts()` and `search_groups()` rebuild compact text and
re-encode all registry entries on every tool call.

**Impact:** Multi-round search can spend most runtime in embedding work. Tests
can also stall when they accidentally hit the real embedding path.

**Fix:**

- Add a lightweight `RegistrySearchIndex` object keyed by registry commit hash
  or in-memory registry version:
  - compact concept rows,
  - compact group rows,
  - BM25 indexes,
  - optional embedding matrices.
- Build it once per resolution pass and place it in `tool_context`.
- Make tools reuse the in-context index instead of rebuilding.
- In tests, monkeypatch `_get_embedding_model()` to `None` except for explicit
  embedding tests.

### 6. Make Group Search Return Compact Rows By Default

**Problem:** `search_groups()` currently returns full registry group dicts when
embedding search succeeds, while the tool definition promises semantic search
results. Full groups can be token-heavy and inconsistent with compact
shortlisting.

**Fix:**

- Return `CompactGroup`-shaped dicts from `search_groups()`.
- Let the model call `get_group(group_id)` when it needs full structure.
- Keep `get_group()` as the only full-group retrieval tool.

### 7. Fix Or Remove `get_source_block` For Registry Blocks

**Problem:** Tool context currently passes only current-unit `source_blocks`.
Registry concept `source_block_refs` usually refer to prior units, so
`get_source_block()` cannot fetch the evidence the prompt suggests.

**Fix options:**

- Preferred: add registry-backed source block lookup by storing source block
  snapshots or resolvable artifact refs in `BookRegistry`.
- Short-term: limit the prompt wording to current-unit source blocks and remove
  advice to fetch prior registry source evidence.

## Data Structure Cleanup

### 8. Stop Reading Private Registry Fields In New Code

The new code reads `registry._groups` and `registry._concepts` in several
places. Some private-field usage pre-existed, but new agentic code should move
toward public helpers:

- `BookRegistry.has_groups()`
- `BookRegistry.list_groups()`
- `BookRegistry.list_concepts()`

Add `list_groups()` before refactoring group index/search code.

### 9. Normalize Tool Results

Use one envelope for every tool result:

```json
{
  "tool_call": {"action": "...", "args": {...}},
  "ok": true,
  "result": {},
  "error": null
}
```

This gives the model the original arguments back and makes failures easier to
repair. Keep result payloads compact unless a `get_*` tool explicitly requests a
full record.

## Test Plan

Add or update tests in `tests/test_agentic_resolution.py`:

- Concept pass with final `link` proposal:
  - record keeps `resolution_proposals`,
  - `implicit_refs` are built from raw proposals,
  - `compute_registry_delta()` receives the link and emits `merge_concepts` with
    `match_reason == "llm_link_proposal"`.
- Group pass with final `continue` proposal:
  - record keeps `group_resolution_proposals`,
  - `compute_registry_delta()` emits group continuation/mutation operations.
- Max-turn exhaustion:
  - pending `tool_calls` cannot validate as a successful empty result,
  - fallback uses v0.1 prompt.
- Prompt contract:
  - no "emit proposals incrementally" text remains in v0.2 prompts,
  - concept and group tool-definition text uses task-specific proposal keys.
- Retrieval tests:
  - default tests monkeypatch embeddings off,
  - one dedicated test covers embedding ranking with a fake model.

Run with the project environment:

```bash
~/.virtualenvs/shredder/bin/python -m pytest tests/test_agentic_resolution.py tests/test_cross_unit_resolution.py
```

## Implementation Order

1. Add `AgenticResolutionResult` and preserve raw proposal data.
2. Enforce final-response completeness and max-turn exhaustion handling.
3. Switch fallback to v0.1 single-pass prompts.
4. Update v0.2 prompt wording and tool-definition text.
5. Add regression tests for proposal preservation and exhaustion.
6. Add registry search index caching and compact group search results.
7. Revisit `get_source_block` with either registry-backed lookup or narrower
   prompt guidance.

The first five steps are correctness fixes and should land before any real
book-scope run. Steps six and seven are quality/runtime improvements that can
land immediately after the correctness patch.
