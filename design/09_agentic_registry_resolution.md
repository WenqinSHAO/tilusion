# Agentic Registry Resolution: Multi-Round LLM with Tool Calling

## Context

Phase 3 (`design/08_cross_unit_llm_merge.md`) implemented cross-unit LLM concept
and group resolution as single-pass conversations: the LLM sees a compact registry
index + unit data, emits all proposals in one response, and deterministic code
applies them. This works but has limits:

1. **No iterative refinement.** The LLM can't request more detail for ambiguous
   cases — it either decides from the compact index or flags uncertainty.
2. **Group shortlisting is deterministic-only.** Groups use concept-overlap
   filtering; no embedding-based semantic matching, no compact group index.
   A "new thread that sounds like a prior thread" is missed.
3. **The compact index may not have enough detail.** For complex entities
   (characters with many aliases, evolving summaries across units, groups with
   rich edge structures), the compact one-line-per-concept format may be
   insufficient for confident identity judgment.

This design upgrades the resolution architecture from single-pass to agentic
multi-round with registry API tool calling.

## Proposed Architecture

```
Conversation D/E (revised, agentic)

Turn 1:
  system: concept_resolution prompt (with tool definitions)
  user:   merged unit concepts + compact registry index (shortlisted)
  assistant: initial screening — for each unit concept, either:
    a) "clearly new" → new_concept proposal
    b) "clearly same" → link proposal (confident from compact index). Link
       proposals may carry optional `changes` — e.g., updated summary,
       added aliases, or new observed_surfaces — when the unit contributes
       fresh information about an existing registry concept.
    c) "need more detail" → requests raw concept data via tool call

Turn 2..N:
  assistant: tool_call: get_concept(id) / get_group(id) / search_concepts(query)
  tool:     returns full Concept / LogicalGroup / search results
  assistant: after examining detail, emits merge / link / continue / mutate
             proposal, or tool call for more candidates

Final turn:
  assistant: emits final_response with `status: "complete"` — all unit concepts
             have a decision. The app deterministically verifies every unit
             concept has a corresponding proposal before accepting the result.
```

**Stop discipline.** The LLM must explicitly signal `status: "complete"` when it
has assigned a decision to every unit concept. The app validates this: if any
unit concept lacks a proposal, the loop continues (up to the turn budget). This
prevents the LLM from silently dropping concepts.

**Turn budget.** Maximum 10 turns. The system prompt includes the budget and
the number of remaining turns so the LLM can pace itself (e.g., "6 turns
remaining — enough to investigate 2-3 more candidates").

The key efficiency property: Turns 2..N only carry the tool call/response
payloads — the system prompt and unit data hit KV-cache. Each additional
concept fetched costs only its full serialization (~200-500 tokens), not
the full unit context.

## Registry API Tool Definitions

The LLM is given these tools in the system prompt:

### `get_concept`
```
get_concept(concept_id: str) → Concept
```
The `concept_id` comes from the compact registry index (the `CompactConcept.concept_id`
field), which is always included so the LLM has valid ids to call.
Returns the full Concept record (all fields: canonical_name, summary, aliases,
observed_surfaces, facets, provenance, source_block_refs, merged_from, etc.).
Used when the compact index suggests a potential match but the LLM needs the
full record to confirm.

### `get_group`
```
get_group(group_id: str) → LogicalGroup
```
The `group_id` comes from the compact group index (`CompactGroup.group_id`).
Returns the full LogicalGroup record including all item_refs, concept_refs,
and graph edges. Used when the compact group index suggests potential
continuation but the LLM needs the full structure to decide continue vs.
mutate vs. new_thread.

### `search_concepts`
```
search_concepts(query: str, top_k: int = 10) → list[CompactConcept]
```
Semantic search over the full registry (embedding-based, not just shortlisted
candidates). Returns compact one-line-per-concept results. Used when the LLM
suspects a match exists but the shortlisted candidates don't include it (e.g.,
a known character appearing under a completely new surface — "the old man"
with no surface collision in the shortlist).

The LLM should craft queries using the most discriminative fields it has:
canonical_name, observed_surfaces, or key phrases from the summary. A good
query is specific (e.g., "Opium War British plenipotentiary" not "person").

### `search_groups`
```
search_groups(query: str, top_k: int = 10) → list[CompactGroup]
```
Same as above for groups. Semantic search over registry groups by summary and
key concepts.

### `merge_concepts`
```
merge_concepts(unit_concept_ids: list[str], registry_concept_id: str | None, changes: dict) → MergeResult
```
Apply a merge. If registry_concept_id is provided, cross-unit link. If None,
within-unit merge. Returns the resulting concept_id.

### `add_concept` / `continue_group` / `mutate_group` / `new_thread`
```
add_concept(concept: dict) → str
continue_group(unit_group_id: str, registry_group_id: str, changes: dict) → str
mutate_group(unit_group_id: str, registry_group_id: str, changes: dict) → str
new_thread(group: dict) → str
```

The LLM emits these as structured tool calls. The backend executes them
deterministically against the BookRegistry. Each call is validated (schema
check, ref resolution) before execution. Failed calls return error messages
the LLM can correct in the next turn.

## Unified Concept/Group Shortlisting

Both Conversations D and E need the same pattern: compact index + dual-signal
retrieval → shortlisted candidates → LLM screens and fetches detail as needed.

### Compact Group Index

New function `build_group_index(registry) → list[CompactGroup]` parallel to
`build_registry_index()`:

```
| group_id | group_type | summary | key_concept_ids | item_count |
|---|---|---|---|---|
| book-group-0017 | timeline | Opium War events 1839-1842 | [book-concept-0042, book-concept-0089] | 23 |
| book-group-0031 | theme_set | Economic consequences of unequal treaties | [book-concept-0042, book-concept-0155] | 8 |
```

`CompactGroup`:
```python
@dataclass
class CompactGroup:
    group_id: str
    group_type: str
    summary: str              # truncated to ~120 chars
    key_concept_ids: list[str] # top 5 concepts by reference count
    item_count: int
```

### Dual-Signal for Groups

Extend `select_group_candidates()` with the same BM25 + embedding + RRF
pattern used for concepts. Build searchable text from group summary +
key concept names. This replaces the current concept-overlap-only filter.

### Unified Candidate Selection API

```python
def select_concept_candidates(
    unit_concepts: list[dict],
    registry_index: list[dict],
    *,
    top_k: int = 20,
) -> list[dict]: ...

def select_group_candidates(
    unit_groups: list[dict],
    registry_groups: list[dict],
    resolved_concepts: list[dict],
    *,
    top_k: int = 20,
) -> list[dict]: ...
```

Both use the same dual-signal pipeline: deterministic pre-filter (always) +
BM25 lexical + Qwen3-Embedding-0.6B semantic + RRF fusion. The deterministic
pre-filter for groups adds concept-overlap as an always-include gate
(concepts already resolved by Conversation D).

### Additional Context Tools

The LLM may need source text to verify a candidate match. Two additional tools
provide this without bloating the conversation:

**`get_source_block(block_id: str) → SourceBlock`** — Returns the full text
and metadata for a source block referenced by a concept or edge. Lets the LLM
read the original passage to confirm identity.

**`get_book_summary() → str`** — Returns the book-level overview summary
produced by Conversation A. Provides domain context (e.g., "this is a history
textbook about the Opium Wars") to help the LLM interpret concept surfaces.

These are optional — the pipeline works without them — but can improve
resolution quality for ambiguous cases where the compact index alone is
insufficient.

## Inclusive Recall Property

The shortlisting must be **inclusive**: false positives are acceptable (LLM
can reject them), false negatives are catastrophic (LLM can never discover
the match). The hybrid approach ensures this:

1. **Deterministic pre-filter** catches exact and near-exact matches (surface
   collision, canonical_name match, concept-overlap for groups). These are
   never missed — zero false negative risk for surface-sharing candidates.
2. **BM25 lexical** catches partial surface overlap, shared terminology,
   translated names. High recall on lexical variants.
3. **Qwen3-Embedding-0.6B semantic** catches the "new surface" case — zero
   surface overlap but semantically related. The 0.988 R@1 on ZH→EN
   cross-lingual retrieval means near-certain recall for cross-lingual
   identity (e.g., Chinese surface → English registry concept).
4. **RRF fusion** ensures no single signal dominates. A concept that ranks
   poorly in BM25 but highly in embedding still gets a strong fused score.

**Candidate count**: use a fixed `top_k` cap (default 20) rather than elbow
detection. A fixed cap is simpler, predictable, and the escape hatch
(`search_concepts` / `search_groups`) catches any misses. Elbow detection on
RRF scores is a potential future optimization if we find the cap is too rigid.

### Measuring Recall

For tuning: run on a labeled dataset where cross-unit identity is known.
Measure recall@K (K=20, 50) — what fraction of known matches appear in the
shortlisted candidates. Target: recall@20 ≥ 0.95. If below, adjust:
- Increase `top_k` per signal
- Lower embedding similarity threshold (currently 0.3)
- Expand deterministic type families

### The `search_concepts` / `search_groups` Escape Hatch

Even with high-recall shortlisting, the LLM may suspect a match exists that
wasn't shortlisted. The `search_concepts(query)` tool lets the LLM query the
full registry directly. This is the final safety net — if the LLM thinks
"this sounds like something I've seen before but it's not in the candidates,"
it can search. The query is the unit concept/group summary, encoded as an
embedding, searched against the full registry.

## Prompt Design Principles

### Conversation D (Concept Resolution) — Revised

The system prompt defines the tools above and instructs:

1. **Screen first.** For each unit concept, check the shortlisted candidates.
   If a clear match exists (same entity, different surface), propose `link`.
   If clearly novel, propose `new_concept`.
2. **Request detail when uncertain.** If a candidate looks plausible but the
   compact index doesn't have enough information, call `get_concept(id)`.
   Examine the full record, then decide.
3. **Search when suspicious.** If no candidate matches but the concept
   summary suggests a known entity, call `search_concepts(query)`. This is
   the "new surface" escape hatch.
4. **Emit proposals incrementally.** Don't wait until all concepts are
   resolved. Propose clear cases first, investigate ambiguous ones.
5. **Stop condition.** When all unit concepts have a decision (link,
   new_concept, merge, split, refine, reclassify), emit final response with
   `resolution_proposals`, `implicit_refs`, `unresolved_items`, `warnings`.

### Conversation E (Group Resolution) — Revised

Same pattern:

1. **Screen first.** For each unit group, check shortlisted registry groups.
   If clear continuation (same thread, next events), propose `continue`.
   If clearly novel, propose `new_thread`.
2. **Request detail.** Call `get_group(id)` to examine full structure
   (items, edges) before deciding continue vs. mutate.
3. **Propose cross-group edges.** After groups are placed, examine for
   cross-group relationships (timeline intersects theme at event, etc.).
4. **Search when needed.** `search_groups(query)` for groups that sound
   familiar but weren't shortlisted.

### Tool Call Schema

Tools use a simple `{"action": ..., "args": {...}}` format embedded in the
JSON response, compatible with the existing `complete_json()` backend:

```json
{
  "tool_calls": [
    {"action": "get_concept", "args": {"concept_id": "book-concept-0042"}}
  ]
}
```

The backend executes the tool call and returns the result as a `tool` role
message in the conversation. The LLM sees the result and continues. When
the LLM has resolved all concepts, it omits `tool_calls` and emits the
final `resolution_proposals` response.

## Implementation Plan

### Step 0: Compact Group Index

- `CompactGroup` dataclass in `registry_index.py`
- `build_group_index(registry) → list[CompactGroup]`
- Tests: empty registry, single group, multi-group, summary truncation

### Step 1: Dual-Signal for Groups

- `_build_group_text(group: CompactGroup) → str` — searchable text
- Extend `select_group_candidates()` with BM25 + embedding + RRF
- Parallel to concept dual-signal; same threshold (>50 groups → activate)
- Tests: semantic match without concept overlap, cross-type group match

### Step 2: Registry API Tool Definitions

- `RegistryTool` protocol/enum in `registry_index.py` or new `registry_tools.py`
- Tool schema definitions (JSON Schema for each function)
- Tool execution dispatcher: routes tool name → BookRegistry method call
- Tests: each tool executes correctly, errors returned for invalid args

### Step 3: Agentic Pass Function

- `run_agentic_resolution_pass()` in `reading_pipeline.py` — generalization
  of the current `run_agentic_pass` that supports tool-calling loops
- Multi-turn loop:
  1. Send user payload + tool definitions
  2. Parse assistant response: if `tool_calls`, execute and append to
     conversation; if `resolution_proposals`, break
  3. Max turns limit of 10 to prevent infinite loops; system prompt makes
     the budget and remaining turns visible to the LLM
  4. Validation on final output
- Replaces the single-turn `run_cross_unit_concept_resolution_pass` and
  `run_cross_unit_group_resolution_pass`

### Step 4: Revised Prompts

- `prompt_concept_resolution_v0.2.md` — add tool definitions, multi-round
  instructions, stop condition
- `prompt_group_resolution_v0.2.md` — same
- Both keep the same output schema for `resolution_proposals`; tool calls
  are additional

### Step 5: Mock Backend

- `MockReadingBackend` gains tool-calling support
- Mock tool execution against in-memory registry
- Tests: full agentic loop with mock tools

### Step 6: Pipeline Wiring

- Replace single-pass concept/group resolution with agentic versions
- Conversation D and E become multi-round
- Backward-compatible: same cache keys (pass_name unchanged), same output
  schema shape in final turn

### Step 7: Shortlisting Quality Metrics

- Measure recall@K on a labeled dataset
- Tune `top_k` and embedding threshold
- Document recall numbers in this design doc

## Open Questions

1. **Tool call format — RESOLVED: simple `{"action": ..., "args": {...}}`**.
   The LLM response JSON includes an optional `tool_calls` key:

   ```json
   {
     "tool_calls": [
       {"action": "get_concept", "args": {"concept_id": "book-concept-0042"}}
     ]
   }
   ```

   This is simpler than OpenAI-style, fits directly in `complete_json()`'s
   expected JSON response schema, and requires no backend interface changes.
   The backend parses `tool_calls`, executes the requested function, and
   appends a `tool_result` role message to the conversation. OpenAI-style
   function calling offers no advantage here since we control the full stack.

2. **Max turns — RESOLVED: 10 turns with budget awareness**. The system prompt
   includes the turn budget and remaining turns. Most cases resolve in 2-4
   turns; 10 is a generous safety cap. If the budget is exhausted before all
   concepts are resolved, remaining concepts fall back to the deterministic
   `new_concept` path.

3. **Within-unit merges in agentic mode — RESOLVED: tool execution updates
   in-flight state**. When the LLM calls `merge_concepts(c1, c2)`, subsequent
   turns see the merged concept list. This is why concept resolution precedes
   group forming — group references must target the post-merge concept ids.

4. **Cache invalidation — RESOLVED: same strategy as other passes**. The
   cache key is computed from the first-turn message signature (system prompt
   + user payload), same as segmentation and per-segment extraction. The
   non-deterministic nature of the LLM call doesn't affect caching — the
   key is on the input, not the output. An `ignore_cache` flag (already
   supported by the pass infrastructure) allows forcing a fresh run when
   needed.

5. **Fallback — RESOLVED: fall back to single-pass**. If the agentic loop
   fails (timeout, max turns exhausted, tool execution errors), fall back to
   the current single-pass implementation. The agentic pass is a quality
   upgrade, not a correctness dependency. Single-pass is always available as
   a safe baseline.

## Files

| File | Action |
|------|--------|
| `tilusion/registry_index.py` | Add `CompactGroup`, `build_group_index`, `select_group_candidates` dual-signal |
| `tilusion/registry_tools.py` | NEW — tool definitions + execution dispatcher |
| `tilusion/reading_pipeline.py` | `run_agentic_resolution_pass()`, replace concept/group pass functions |
| `tilusion/reading_prompts.py` | v0.2 composition builders with tool definitions |
| `tilusion/prompts/prompt_concept_resolution_v0.2.md` | NEW — tool-calling version |
| `tilusion/prompts/prompt_group_resolution_v0.2.md` | NEW — tool-calling version |
| `tilusion/backend.py` | Extend `LLMBackend` for tool-calling support (if needed) |
| `tests/test_agentic_resolution.py` | NEW — tool execution, agentic loop, mock integration |
