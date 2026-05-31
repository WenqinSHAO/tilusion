# Pipeline Refactor: Cross-Unit Extraction (Phase 2)

## Context

Phase 1 wired BookRegistry into the extraction pipeline with `scope="book"`,
adding: `load_or_init`, `has_concepts`, `_merge_summaries` on BookRegistry;
`registry_delta.py` (deterministic delta compute/apply); `book_digest.py` v0.1
(entity-table-only digest from registry snapshot, pre-extraction); and
`--scope book` on the CLI.

Phase 1 limitations:
- Digest is a stateless snapshot of registry entities, generated pre-extraction
- Overview pass receives no book context
- Each LLM call is single-turn `complete_json()`, no KV-cache reuse

**Phase 2 addresses these with two changes:**

1. **Post-extraction digest as Conversation C additional turn.** Instead of a
   separate LLM pass, the digest is generated as a follow-up turn in the unit
   logical grouping conversation, which already holds `unit_text`, concepts,
   items, and groups. Only the N-1 digest is added as new input — everything
   else hits KV-cache. The digest and registry are committed together.

2. **Book digest ingestion at overview pass.** The overview segmentation pass
   receives the cached N-1 digest and produces per-segment hints tailored to
   known entities and narrative threads. Per-segment extraction receives only
   these customized segment hints (not the raw digest) — the digest cascades
   through overview, which distills it into segment-specific guidance.

**Priority and risk posture.** The book digest is a quality enhancement, not
a correctness dependency. Concept/item/group merging into the BookRegistry
is a must-have (it ensures book-level structure). The digest improves
extraction quality by providing narrative context, but a missing or
uninformative digest must never harm extraction. Per-segment hints derived
from the digest may be empty — extraction still works correctly. This batch
paves the way for full native integration with BookRegistry and multi-turn
agentic backbone.

## Deferred (not in this batch)

- LLM-driven concept/item/group merging (cross-unit merge proposals,
  ambiguity resolution, group continuation)
- Agentic repair loop (`repair.py`, `DeterministicAutoFixer`) — design/05
  steps 3-5; Phase 2 only needs the multi-turn conversation infrastructure
  (design/05 steps 1-2) to enable the Conversation C digest turn
- `refine_concept` / `refine_item` (LLM summary rewriting)
- `link_concept` as standalone operation
- User feedback / correction engine

## Architecture (Phase 2 target)

```
run_reading_pipeline(book_path, unit_id, scope="book")
│
├─ [Pre-extraction]
│   ├── Load BookRegistry for book_path
│   └── Load cached book digest from unit N-1
│       (None for first unit)
│
├─ Step 1: Overview segmentation (+ book digest)
│   Conversation A (system: overview prompt)
│   ├── Turn 1: [system][user: unit_text, unit_metadata, book_digest]
│   │           → [assistant: segments with digest-informed hints]
│   └── repair loop (if validation fails)
│   → resolved segments, each with entity-aware key_entities / extraction_hints
│
├─ Step 2: Per-segment extraction (parallel)
│   Conversation B1..Bn (system: per-segment prompt)
│   ├── Turn 1: [system][user: segment_text, blocks, segment_hints]
│   │           → [assistant: concepts, items]
│   │   segment_hints are digest-informed from overview (key_entities,
│   │   extraction_hints); the raw book_digest is NOT repeated here
│   └── repair loop (if validation fails)
│   → all segment results
│
├─ Deterministic merge (no LLM)
│   merge_segment_extraction_results()
│   → merged concepts, stabilized items, unresolved_items
│
├─ Step 3: Unit logical grouping + digest update
│   Conversation C (system: grouping prompt)
│   ├── Turn 1: [system][user: unit_text, concepts, items, book_digest]
│   │           → [assistant: concept_deltas, groups]
│   ├── repair loop (if validation fails)
│   │       ← KV-cache hit on repair turns
│   │
│   └── Turn N+1 (digest update): [user: digest_update_task, digest_N-1]
│       → [assistant: updated_digest_for_N+1]
│       ← KV-cache HIT on system + unit_text + concepts + items
│       Only digest_N-1 (~500 tokens) and the new response are new cost.
│   → post-processing (delta application, dedupe, remap)
│
├─ [Post-extraction]
│   ├── Compute registry delta (deterministic)
│   ├── Apply delta to BookRegistry
│   ├── Save updated digest alongside registry  ← part of registry state
│   └── Save BookRegistry (git commit)           ← digest + registry in same commit
│
└── Write unit package + artifacts
```

Key points:
- **Digest is a Conversation C turn, not a separate pass.** The grouping
  conversation already has `unit_text`, all concepts, all items in its
  message history. Adding a digest-update turn reuses the KV-cache for
  everything except the new user message (digest task + N-1 digest) and
  the assistant response.
- **Digest and registry are committed together.** The digest reflects the
  registry state after unit N's delta is applied. Both go into the same
  git commit — the digest is part of the book's accumulated knowledge.

## Design 1: Digest as Conversation C Additional Turn

### Why not a separate LLM pass

A separate `build_book_digest()` call would start a fresh conversation and
re-send `unit_text` (~3-5K tokens) + concepts + items + groups as inputs.
Conversation C Turn 1 already has all of this in its message history. The
only new information needed for digest update is the N-1 digest.

### Turn structure

Conversation C starts as a normal grouping pass:

```
Turn 1:
  system: grouping_prompt (includes digest consumption instructions)
  user:   {task: "unit_logical_grouping", unit_text, concepts, items, book_digest, ...}
  assistant: {concept_deltas: [...], logical_groups: [...], ...}
```

After validation passes and concept deltas are applied (deterministic
post-processing), a follow-up turn updates the digest:

```
Turn 2 (digest update):
  user:   {
    task: "update_book_digest",
    previous_digest: "<digest from unit N-1>",
    instruction: "Generate an updated book context digest for unit N+1
                  based on the extraction results above and the previous
                  digest. Follow the digest format specification."
  }
  assistant: {digest: "# Book Context Digest\n...", entity_count: N, warnings: [...]}
```

The assistant already sees the full extraction results from Turn 1 — it
doesn't need them repeated. The `previous_digest` is the only new payload.

### Digest format

A compact markdown string with whatever structure the LLM finds clearest.
Minimal prescription — the format should be easy for the extraction LLM
to consume. Typical sections:

```
# Book Context Digest

## Known Entities
| Name | Type | Notes |
|---|---|---|
| ... | | |

## Attention Guidance
[2-4 sentences: narrative attention cues — what narrative threads are
unresolved, what to watch for. Not extraction methodology (the extraction
prompt handles that). Examples: "Event X is unresolved — watch for
revealing details." "Figure Y's true identity remains unclear."
"Geographical impact on Z was discussed; the economic analysis promised
by the author is likely in this unit."]
```

The digest should NOT give extraction methodology instructions — the
extraction prompt is already sophisticated for that. The digest's job is
to guide the LLM's **attention**: what narrative threads, entities, and
questions to be alert for. It's a spotlight, not a manual.

Target ≤800 tokens total. The format may evolve as we observe what helps
extraction most.

### Implementation

In `run_reading_pipeline`, after the grouping pass completes and concept
deltas are applied:

```python
# After grouping pass validation + delta post-processing:
if scope == "book" and digest_N_minus_1 is not None:
    digest_update_msg = {
        "task": "update_book_digest",
        "previous_digest": digest_N_minus_1,
        "instruction": "Generate an updated book context digest..."
    }
    conversation = grouping_record.conversation  # from pass record
    updated = backend.continue_conversation(conversation, digest_update_msg)
    digest_for_next_unit = json.loads(
        _last_assistant_content(updated)
    ).get("digest")
```

This requires `start_conversation` / `continue_conversation` on the
backend (design/05 steps 1-2) — the only part of design/05 needed for
this batch.

## Design 2: Overview Pass Digest Ingestion

### Rationale

Currently the overview pass segments text by structural features only.
With the N-1 digest, overview can produce **attention-informed**
per-segment hints that guide what the extraction LLM should watch for.
These hints are the only cross-unit context per-segment extraction
receives — the raw book digest is not repeated in per-segment payloads.
The cascade is:

```
digest (N-1) → overview → segment_hints → per-segment extraction
```

Example contrast:

- Without digest: `extraction_hints: ["Pay attention to speakers"]`
- With digest: `extraction_hints: ["Confucius appears in dialogue here — his view on ritual is contested"]`

The overview is the first LLM pass to see the unit text. Giving it book
context lets it recognize when a segment covers known entities, unresolved
narrative threads, or anticipated developments. Per-segment extraction
then gets segment-specific hints — more targeted and cheaper than
repeating the full digest for every segment.

### Constraint

Overview is a **segmentation** pass, not an extraction pass. The digest is
guidance for segmentation and hint generation only. Segment boundaries must
still be grounded in text quotes (`start_quote`/`end_quote`). Every
`key_entity` hint must reference text the segment actually contains.

### Changes

- `run_overview_segmentation_pass()` gains `context: dict[str, Any] | None`
- Overview prompt consumes `context.digest` (same pattern as per-segment
  and grouping prompts)
- When digest is present, the prompt instructs: use digest to recognize
  known entities, include references in hints, do not re-extract or
  redefine entities already in the digest
- Cache key already includes the full user payload, so scope="book" and
  scope="unit" overview cache entries never collide

## Implementation Sequence (Phase 2)

### Step 1: Multi-turn conversation infrastructure (design/05 commits 1-2 only)

`ConversationContext` dataclass + `start_conversation` / `continue_conversation`
on `LLMBackend`. This is the minimum needed to enable the Conversation C
digest turn. MockReadingBackend updated.

Files: `tilusion/conversation.py` (new), `tilusion/backend.py`,
`tilusion/pass_utils.py`, `tilusion/reading_pipeline.py`

### Step 2: Post-extraction digest as Conversation C turn

Extend the grouping pass flow in `run_reading_pipeline`:
- After grouping validation + delta post-processing, add digest update turn
- `continue_conversation(grouping_conversation, digest_update_msg)`
- Parse digest from assistant response
- Save digest alongside registry (same cache directory, version-controlled
  together)
- Update `prompt_book_digest_v0.1.md` → v0.2 for the turn-2 task shape

Files: `tilusion/reading_pipeline.py`, `tilusion/book_digest.py`,
`tilusion/prompts/prompt_book_digest_v0.2.md` (new)

### Step 3: Overview pass digest ingestion

Add `context` parameter to `run_overview_segmentation_pass()`, thread
cached digest from pre-extraction, update overview prompt to consume
`context.digest` and produce digest-informed per-segment hints.

Files: `tilusion/overview.py`, overview prompt template

## Verification

1. **Conversation C digest turn with mock backend**: Run scope="book" on
   unit 2 with pre-populated registry + cached N-1 digest. Verify:
   - Conversation C has ≥2 turns
   - Turn 2 user message contains `task: "update_book_digest"` and
     `previous_digest`
   - Assistant response contains updated digest referencing unit 2 entities

2. **KV-cache semantics**: Verify that Turn 2's `messages` array shares
   the Turn 1 prefix (system + unit_text + concepts + items messages are
   byte-identical). The only new messages are the digest task user message
   and the assistant response.

3. **Overview with digest**: Mock backend with pre-cached digest → verify
   overview payload includes `context.digest` → verify output segments
   have digest-informed `key_entities` / `extraction_hints`.

4. **First unit**: Empty registry + no cached digest → overview receives
   `context={}` → extraction proceeds normally → digest generated as
   Conversation C turn 2 → cached for unit 2.

5. **3-unit integration**: Mock backend, verify:
   - Unit 1: no digest → extraction OK → digest generated post-extraction
   - Unit 2: receives unit 1 digest → overview hints entity-aware →
     extraction OK → digest updated
   - Unit 3: receives unit 2 digest → extraction OK
   - Registry has cumulative concepts; digest evolves across units

6. **Scope="unit" regression**: All existing tests pass, behavior unchanged.

---

## Phase 1 Recap (2026-05-30)

Already implemented and tested (297 tests passing):

| What | Where |
|---|---|
| `BookRegistry.load_or_init`, `has_concepts`, `_merge_summaries` | `book_registry.py` |
| `compute_registry_delta`, `apply_registry_delta`, `RegistryDeltaResult` | `registry_delta.py` |
| `build_book_digest` v0.1 (entity-table-only, pre-extraction) | `book_digest.py` |
| `--scope book` on CLI; pipeline pre/post extraction hooks | `reading_pipeline.py`, `cli.py` |
| `context.digest` field in per-segment + grouping prompts | `prompts/*.md` |
| 14 delta tests, 7 registry tests | `tests/` |

Phase 1 resolutions for design issues B1-B7 and N3 are documented at the
end of this file.

---

## Open Design Questions

### Q1: Digest format — prose, semi-structured, or JSON?

**A) Prose paragraph** — compact, natural, but harder for LLM to parse precisely.
**B) Bullet list with entity summaries** — semi-structured, scannable.
**C) Markdown table of known entities + prose for threads/guidance** — hybrid.

> Resolution: **(C)**. But the format is not rigid — whatever is easiest for
> the extraction LLM to consume. The digest prompt specifies the goal
> (entity recognition + extraction guidance), not a strict template.

### Q2: Digest generation — when to regenerate?

**A) Every unit**: Generate fresh digest before each unit extraction.
**B) On registry change**: Only when the registry was modified.
**C) Cached with content hash**: Digest keyed by registry_head_commit.

> Resolution: **(B)**. Post-extraction, after registry delta is applied.
> The digest is a Conversation C follow-up turn, reusing KV-cache for
> unit_text + concepts + items. Cached and consumed by the next unit.

### Q3: Digest prompt — what registry information to include?

**A) Full dump**: All concepts, items, groups serialized.
**B) Compact summary**: canonical_name, type, one-line summary, observed_surfaces.
**C) Structured abstract**: Deterministic pre-digest with counts, groupings.

> Resolution: **(B)**. But since the digest is now a Conversation C turn,
> the LLM already sees the full extraction results from Turn 1. The
> additional input is only the N-1 digest. No need to re-serialize
> registry state — it's already in the conversation.

### Q4: Merge summary concatenation — format?

**A) Separator join** — `"summary1 | summary2"`
**B) Newline join** — `"summary1\nsummary2"`
**C) Source-prefixed** — `"[unit-0001]: summary1\n[unit-0003]: summary2"`

> Resolution: **(C)**. Source-prefixed summaries allow LLM-backed summary
> evolution to later describe how and when a concept evolved across units.
> The unit prefix is derived from concept provenance at merge time.

---

## Phase 1 Design Resolutions

Resolved during Phase 1 implementation:

- **B1**: Context type contract — `{"digest": str}` dict, wrapped via
  `make_context_dict()`.
- **B2**: Source unit provenance — injected via `_dict_to_concept(d,
  source_unit=unit_id)` in `compute_registry_delta`.
- **B3**: Cache root naming — `registry_cache_root` computed from parent
  of `reading_passes` cache_dir.
- **B4**: Digest specification — prompt at
  `prompts/prompt_book_digest_v0.1.md`, `start_conversation()` JSON-mode,
  concepts only (max 50), graceful degradation on failure.
- **B5**: Operation vocabulary — `merge_concepts`, `add_concept`,
  `add_item`, `add_group`, `ambiguity_item`.
- **B6**: Unresolved items — forwarded as `ambiguity_items` with
  `source: "unit_unresolved"`.
- **B7**: Ambiguity accumulation — deferred, sidecar JSON per unit.
- **N3**: Digest placement — per-segment user payloads, not system prompts.
