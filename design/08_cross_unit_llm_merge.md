# Cross-Unit LLM Merge: Concept, Item, Group Resolution

## Context

Phase 2 wired BookRegistry into the pipeline and added the book digest as a
Conversation C follow-up turn. The current cross-unit concept linking is purely
deterministic (`compute_registry_delta` in `registry_delta.py`) — it matches
concepts by exact `canonical_name` + type collision only. LLM judgment for
identity resolution is confined to within-unit concept deltas inside the
grouping pass, with no visibility into registry state.

This is the must-have feature: **LLM-backed cross-unit concept identity,
item reference, and group continuation resolution**, built on the multi-turn
conversation backbone and BookRegistry.

## Current Flow (Phase 2)

```
run_reading_pipeline(book_path, unit_id, scope="book")
│
├─ [Pre]  Load BookRegistry + cached digest from unit N-1
│
├─ Step 1: Overview segmentation (Conversation A)
│           → resolved segments + extraction_hints (digest-informed)
│
├─ Step 2: Per-segment extraction (Conversations B1..Bn, parallel)
│           → concepts + items per segment
│
├─ Step 3: Deterministic merge (no LLM)
│           → merged concepts, stabilized items, unresolved_items
│           Merge is within-unit only: canonical_name + type collision.
│
├─ Step 4: Unit logical grouping (Conversation C)
│           → concept_deltas (merge, split, refine, reclassify)  ← LLM judges
│             identity within unit, but has NO registry visibility
│           → logical_groups (timelines, theme sets, discourse graphs, ...)
│           → digest update as Turn N+1
│
├─ [Post] Compute deterministic registry delta (exact match only)
│         Apply delta → save registry + digest
│
└─ Write unit package
```

**Key gap:** The LLM that does concept deltas (step 4) never sees registry
concepts from prior units. Identity resolution is within-unit only.
Cross-unit linking is purely deterministic — exact name match — missing
implicit references, surface variations, and harder identity judgments.

## Proposed Flow (Phase 3)

Two new LLM passes and one substantially revised pass:

```
run_reading_pipeline(book_path, unit_id, scope="book")
│
├─ [Pre]  Load BookRegistry + cached digest from unit N-1
│
├─ Step 1: Overview segmentation (Conversation A)          ← unchanged
│
├─ Step 2: Per-segment extraction (Conversations B1..Bn)   ← unchanged
│
├─ Step 3: Deterministic merge (no LLM)                    ← unchanged
│
├─ Step 4: [NEW] Cross-unit concept resolution (Conversation D)
│   │       system: concept_resolution prompt
│   │       user:   merged unit concepts (with summaries),
│   │               registry concept index (compact: canonical_name, type,
│   │               one-line summary, observed_surfaces — for all registry
│   │               concepts, or semantically filtered subset),
│   │               prior unresolved items, N-1 digest
│   │       assistant: concept_resolution_proposals
│   │         - link: unit concept C_i ↔ registry concept R_j (cross-unit
│   │           identity — same real-world entity, different surfaces)
│   │         - merge: unit concepts C_i, C_j → single concept (within-unit
│   │           correction the deterministic merge missed)
│   │         - split, refine, reclassify (same as current concept_deltas)
│   │         - implicit_ref: "item I_k refers to registry concept R_j
│   │           without naming it"
│   │         - new_concept: C_i is genuinely novel, register as new
│   │       → applied deterministically → remapped concepts + implicit ref map
│   │
│   │       Conversation D replaces the concept_deltas portion of current
│   │       Conversation C. The grouping pass no longer sees concept deltas.
│   │
│   │       Registry concept index construction is the essential design
│   │       challenge — see D2 below.
│   │
├─ Step 5: Unit logical grouping (Conversation C, revised)
│   │       system: grouping prompt (stripped of concept delta guidance)
│   │       user:   unit_text, resolved concepts (now linked to registry),
│   │               atomic items, implicit_ref map from step 4
│   │       assistant: logical_groups only
│   │         - Groups can reference registry-linked concept IDs
│   │         - The implicit_ref map lets the LLM understand that "the
│   │           treaty" in item I_k actually refers to registry concept R_j
│   │       → pure grouping, no concept edits
│   │
├─ Step 6: [NEW] Cross-unit group resolution (Conversation E)
│   │       system: group_resolution prompt
│   │       user:   unit logical groups, registry groups (filtered by
│   │               concept/item overlap candidates), resolved concepts
│   │       assistant: group_resolution_proposals
│   │         - continue: unit group G_i extends registry group H_j
│   │           (timeline continuation, theme development, etc.)
│   │         - mutate: unit group G_i modifies registry group H_j
│   │           (new items/edges grafted onto existing group)
│   │         - new_thread: unit group G_i is a novel angle/thread/POV
│   │         - cross_group_edge: relationship between groups
│   │           (timeline A intersects theme B at event C, etc.)
│   │           Reuses the same edge types as within-group graph edges
│   │           (precedes, causes, supports, contradicts, refers_to, etc.)
│   │         - merge_groups: two registry groups should merge
│   │           (discovered only now that more units are available)
│   │       → applied → book-level groups updated
│   │
├─ [Post] Compute registry delta (deterministic + step 4/6 proposals)
│         Apply → save registry + digest
│
└─ Write unit package
```

## Design Decisions

### D1: Concept resolution is a dedicated pass, not part of grouping

**Rationale:** The current grouping pass asks the LLM to simultaneously judge
concept identity (merge/split/reclassify) and build logical groups. These are
different cognitive tasks with different evidence needs:

- **Concept identity** needs: surface forms, canonical names, concept types,
  concept summaries (semantic context), registry collision candidates,
  cross-unit continuity signals.
- **Group building** needs: full unit text, narrative function of items,
  temporal ordering, discourse structure, thematic coherence.

Separating them lets each pass be simpler, with focused prompts and smaller
output schemas. Concept resolution happens first because groups depend on
resolved concept identity — you can't build a timeline correctly if two
references to the same character haven't been merged.

Unit text is NOT needed for concept resolution. Each concept carries its own
semantic context via its `summary`, `canonical_name`, `observed_surfaces`,
and `concept_type`. The resolution task is comparing concept A (unit) with
concept B (registry) — the summaries tell the LLM who/what each concept is
about. The full unit text is only needed when the concept summary is too
sparse to disambiguate, which should be rare; in that case, the concept's
`source_block_refs` can be used to fetch the relevant text snippet, not the
whole unit.

### D2: Registry concept index — the essential design challenge

The book registry will grow beyond what fits in an LLM context window.
Concept resolution needs the LLM to match unit concepts against registry
concepts, but sending the full registry (every concept with full provenance,
source refs, etc.) is not viable.

The registry must be presented as a **compact index** — one line per concept:

```
| concept_id | canonical_name | type | summary | observed_surfaces |
|---|---|---|---|---|
| concept-0042 | Treaty of Nanjing | source | 1842 treaty ending the First Opium War | Treaty of Nanjing, Nanjing Treaty, the treaty |
| concept-0017 | Shen Fu | person | Author and autobiographical narrator | 沈复, 三白, Shen Fu |
```

This is essentially the Known Entities table from the book digest, evolved to
serve as the registry index for concept resolution.

However, even this compact form will eventually exceed context. The question
is how to scope it. Two complementary strategies:

**Strategy A — Deterministic pre-filtering (always applied):**
- Surface collision: registry concepts whose `observed_surfaces` overlap with
  unit concept surfaces/aliases
- Type family match: same or compatible `concept_type`
- Temporal proximity: time_anchor concepts within adjacent periods
- Concepts from the most recent N-1 unit (most likely to be referenced)

These produce a narrow but potentially incomplete candidate set. They miss
the case where a known entity appears under a completely new surface (e.g.,
"Shen Fu" appears as "the old man" in a later unit — no surface collision,
different type family if extracted as "unnamed elder").

**Strategy B — Dual-signal candidate retrieval (applied when registry is large):**
Lexical + semantic signal fusion for candidate detection, a proven pattern
in 2025-2026 entity resolution systems (EntityCleaner, LLM4MEM):

1. **Lexical signal (BM25 / text search):** Search registry concepts by
   `canonical_name`, `observed_surfaces`, and summary text against unit
   concept surfaces and summaries. Catches partial surface overlaps.
2. **Semantic signal (embedding similarity):** Compute embeddings for unit
   concept summaries and registry concept summaries. Cosine similarity
   retrieves top-K candidates with no surface overlap — this catches the
   "new surface" case (e.g., "the old man" ↔ "Shen Fu").
3. **Fusion (Reciprocal Rank Fusion or similar):** Combine lexical and
   semantic rankings into a single candidate list per unit concept.
4. **LLM judges only the fused candidate set** — cheaper and more focused
   than scanning the full registry or relying on an LLM retrieval pre-pass.

Cross-type relaxation (LLM4MEM, AuraHQ) is also relevant: don't restrict
candidates to the exact same `concept_type`. A known person might be
extracted under `object` or `other` in a later unit. Expand the type filter
to broader families (person/group/organization, place/scene_element, etc.).

**Hybrid approach:**
1. Always apply deterministic pre-filter (surface, type family, temporal)
2. When the registry grows beyond what fits in context, add dual-signal
   retrieval to narrow further — each unit concept gets its top-K fused
   candidates
3. The LLM doing identity judgment sees unit concepts + compact registry
   index of the fused candidate set

Start simple: deterministic pre-filter only. Add dual-signal retrieval when
registry size proves it necessary.

For the embedding model, **Qwen3-Embedding-0.6B** (Apache 2.0, June 2025) is the
best choice for Chinese-English cross-lingual retrieval:
- 0.6B params, ~1.2 GB FP16, ~639 MB quantized — runs on CPU
- 32K token context — handles long concept summaries
- MRL support: dynamic dimension reduction (1024 → 256 dims with <5% quality loss)
- CCKM benchmark: 0.988 R@1 on ZH→EN cross-lingual retrieval, 0.969 on hard
  idiom-level matching (e.g., "画蛇添足" → "gilding the lily")
- CMTEB-R (Chinese retrieval): 71.02 — #1 among models under 1B params
- HuggingFace: `Qwen/Qwen3-Embedding-0.6B`

Models under 300M that are English-focused **completely fail** on Chinese
(nomic-embed-text: 0% on ZH, mxbai-embed-large: 12% R@1 on ZH↔EN). A
purpose-built multilingual model is essential.

Fallback: **BGE-M3** (568M, MIT license, 100+ languages) if Qwen3 cannot be
used. Slightly weaker on hard cross-lingual idioms (0.844 vs 0.969) but
well-proven and battle-tested.

### D3: The "new surface" problem

A concept known to the registry may appear in a later unit under a completely
new surface form. The deterministic merge won't catch it (no surface collision,
possibly different type). The LLM can catch it only if the registry concept
is in the candidate set.

With a compact registry index and semantic retrieval (D2), this is covered:
the LLM sees "the old man" (unit concept, type: person, summary: "elderly male
narrator reflecting on the past") and "Shen Fu" (registry concept, type: person,
summary: "Author and autobiographical narrator") in the same candidate set,
and can propose a `link` even though surfaces don't overlap at all.

This is why the registry index must be semantically scoped, not just surface-
collision scoped. Surface collision catches the easy cases; semantic retrieval
catches the hard ones.

### D4: `link` vs `merge` — different scopes

- **`link`**: Cross-unit identity. Unit concept C_i refers to the same
  real-world entity as registry concept R_j. The application links them —
  C_i's surfaces and summary are merged into R_j, C_i's items are remapped
  to R_j. This is the primary operation of step 4.
- **`merge`**: Within-unit correction. Two unit concepts C_i and C_j should
  have been merged by the deterministic merge but weren't (e.g., their
  canonical_names didn't match but the LLM judges them to be the same entity).
  Same semantics as the current concept_delta merge.

The distinction matters because `link` affects both the unit and the registry
(cross-unit identity), while `merge` affects only the unit (within-unit
correction). The application handles them differently.

### D5: Concept resolution cascades to items and groups

When a unit concept is linked to a registry concept, the implications ripple:

- Items that reference the unit concept now implicitly reference the registry
  concept. This is captured in the `implicit_ref` map.
- The grouping pass (step 5) receives the implicit_ref map and uses it to
  place items correctly — knowing that "the treaty" = Treaty of Nanjing
  changes which theme/timeline group an item belongs to.
- The group resolution pass (step 6) receives registry-linked concepts and
  can correctly identify group continuation.

The cascade is deterministic: step 4 produces the link + implicit_ref map,
the application remaps concept_refs in items, and steps 5-6 receive the
resolved state. The LLM in steps 5-6 doesn't re-judge identity.

### D6: Item merging is rare — implicit refs cover the common case

Items (events, actions, claims) rarely merge across units. A prior event
may be referenced, alluded to, or summarized — but that reference is a new
item in the current unit, not the same item. The rare exceptions (same
event described from two angles in different units) can be flagged as
`same_as_candidate` edges in group resolution, not as item merges.

Deferred to group resolution (step 6). If cross-unit item linking proves
common enough, extract into its own pass later.

### D7: Group resolution heuristics are rich but schema-light

Group types and their resolution patterns:

| Group type | Continuation pattern | Mutation pattern | Cross-group edges |
|---|---|---|---|
| `timeline` / `temporal_sequence` | Append new events, extend time range | Insert events mid-timeline, split into sub-periods | Intersects with theme/character groups at specific events |
| `theme_set` | Add new examples/evidence | Split into sub-themes | Themes intersect at shared events |
| `discourse_graph` / `claim_evidence_map` | New claims/evidence extend graph | Existing claims qualified/contradicted by new evidence | Cross-reference between discourse and timeline |
| `motif_development` | New occurrences of motif | Motif meaning evolves, new facets | Motif appears in character arc, event pattern |
| `concept_map` | New terms/concepts added | Existing term definitions refined | Terms used in claims, methods |
| `viewpoint_evolution` | New POV added to thread | Existing POV qualified by new speaker | Viewpoint contests claim in discourse graph |
| `character_relationship_graph` | New relationship edges | Existing edges qualified (e.g., "friendship → rivalry") | Character relationships drive timeline events |

The group resolution prompt encodes these as guidance, not as rigid rules.
The LLM judges based on narrative coherence.

### D8: Conversation architecture — three dedicated system prompts

- **`prompt_concept_resolution_v0.1.md`** — Step 4
- **`prompt_unit_grouping_v0.2.md`** — Step 5 (revision of v0.1, minus concept deltas)
- **`prompt_group_resolution_v0.1.md`** — Step 6

All use `start_conversation()` / `continue_conversation()` with repair loops
via `run_agentic_pass`. The digest update turn stays on Conversation C
(grouping) since that conversation still holds unit_text + concepts + items.

### D9: First unit behavior

First unit has no registry — steps 4 and 6 are skipped. Step 5 (grouping)
runs normally and produces the seed registry state. The concept deltas that
the current grouping pass handles (within-unit merge/split/refine/reclassify)
still need to happen for the first unit. The concept resolution pass for unit 1
can run with an empty registry index — it would only produce within-unit
`merge`/`split`/`refine`/`reclassify` proposals (the same work the current
grouping pass does for concept deltas).

Decision: run step 4 for unit 1 with empty registry index. It handles
within-unit concept corrections, replacing the concept_deltas role in the
current grouping pass. Skip step 6 for unit 1 (no registry groups).

## Conversation D: Cross-Unit Concept Resolution

### Input

```
task: "cross_unit_concept_resolution"
schema_version: "reading-unit-v0.3"
unit_id: ...
concepts: [...]         (merged unit concepts from step 3, each with
                         concept_id, surface, concept_type, canonical_name,
                         summary, observed_surfaces, source_block_refs)
registry_index: [...]   (compact registry concept table — one line per
                         concept: concept_id, canonical_name, type, one-line
                         summary, observed_surfaces. May be the full registry
                         when small, or a semantically filtered subset.)
unresolved_items: [...] (from deterministic merge)
context: {digest: "..."} (N-1 book digest)
```

Unit text is NOT included. Concept summaries provide the semantic context
for identity judgment. If a concept's summary is too sparse to disambiguate,
the LLM should note this in `uncertainty` rather than guess.

### Output

```json
{
  "unit_id": "unit-0002",
  "resolution_proposals": [
    {
      "proposal_id": "res-0001",
      "proposal_type": "link|merge|split|refine|reclassify|new_concept",
      "unit_concept_refs": ["concept-0001"],
      "registry_concept_ref": "concept-0042",
      "changes": {
        "canonical_name": "optional refined name",
        "summary": "optional updated summary incorporating new information",
        "new_surfaces": ["surface form found in this unit but not in registry"],
        "new_aliases": []
      },
      "rationale": "brief reason for this proposal",
      "implicit_refs": [
        {
          "item_ref": "item-0003",
          "registry_concept_ref": "concept-0042",
          "surface_in_text": "the treaty",
          "explanation": "This item discusses the treaty's provisions; in context this is the Treaty of Nanjing"
        }
      ],
      "uncertainty": [],
      "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}
    }
  ],
  "unresolved_items": [],
  "warnings": []
}
```

### Proposal types

- **`link`**: Cross-unit identity. Unit concept C_i is the same real-world
  entity as registry concept R_j. The application adds C_i's surfaces to R_j,
  merges summaries, and remaps C_i's item refs to R_j. This is the primary
  operation — it's how the registry grows its knowledge of each entity.
- **`merge`**: Within-unit correction. Two or more unit concepts are actually
  the same entity (missed by deterministic merge). Same semantics as current
  concept_delta merge. Only affects unit concepts, not registry.
- **`split`**: A unit concept conflates distinct entities. Same as current
  concept_delta split.
- **`refine`**: Update canonical_name, summary, aliases, facets without
  changing identity. Same as current.
- **`reclassify`**: Change concept_type. Same as current.
- **`new_concept`**: Unit concept is genuinely novel — no matching registry
  concept. The application registers it as a new book-level concept.
- **`implicit_refs`**: Attached to a `link` proposal. Flags items in the
  current unit that reference the linked registry concept without naming it
  directly. The application uses these to enrich the grouping pass context.

### Rules

- Registry concepts in the index are read-only. Modify unit concepts only;
  the application applies proposals and updates the registry deterministically.
- Do not merge distinct entities (same rules as current concept delta
  guidance — no merging different people, places, dates, sources).
- If the registry index doesn't contain a matching concept but the LLM
  suspects one exists from the digest, note it in `warnings`.
- Preserve uncertainty. If ambiguous, escalate to `unresolved_items`.

## Conversation C (Revised): Unit Logical Grouping

### Changes from v0.1

- **Remove:** All `concept_deltas` output. The concept_deltas key is gone.
  Concept identity is resolved upstream in step 4.
- **Add:** `implicit_refs` input from step 4. A map of item_id → registry
  concept, so the grouping LLM knows "the treaty" = concept-0042.
- **Output:** Only `logical_groups`, `unresolved_items`, `warnings`.
- **Prompt:** Remove all concept delta guidance (~40 lines). Add brief note:
  "Concepts have been resolved against the book registry by a prior pass.
  Use concept_refs as given; do not propose concept merges or splits."

The grouping pass becomes simpler and more focused: read items, understand
their narrative function, build groups.

## Conversation E: Cross-Unit Group Resolution

### Input

```
task: "cross_unit_group_resolution"
schema_version: "reading-unit-v0.3"
unit_id: ...
concepts: [...]         (resolved concepts from step 4)
groups: [...]           (unit logical groups from step 5)
registry_groups: [...]  (book-level groups from prior units, filtered by
                         concept/item overlap with this unit's groups)
context: {digest: "..."}
```

### Output

```json
{
  "unit_id": "unit-0002",
  "group_resolution_proposals": [
    {
      "proposal_id": "grp-res-0001",
      "proposal_type": "continue|mutate|new_thread|cross_group_edge|merge_groups",
      "unit_group_ref": "group-0001",
      "registry_group_ref": "group-0017",
      "changes": {
        "continuation_items": ["item-0003", "item-0007"],
        "new_edges": [],
        "group_summary_update": "optional refined summary for the continued group",
        "cross_group_edges": [
          {
            "source_group": "group-0001",
            "target_group": "group-0017",
            "edge_type": "precedes|causes|enables|supports|contradicts|refers_to|...",
            "at_item": "item-0003",
            "summary": "..."
          }
        ]
      },
      "rationale": "...",
      "uncertainty": [],
      "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}
    }
  ],
  "warnings": []
}
```

### Proposal types

- **`continue`**: unit group extends a registry group. The most common case —
  a timeline continues into a new period, a theme set gains new examples,
  a character relationship graph adds new interactions. The application
  grafts the unit group's items and edges onto the registry group.
- **`mutate`**: unit group modifies a registry group in a way that changes its
  structure. E.g., a timeline needs items inserted between existing events,
  a theme splits into sub-themes, a discourse graph is qualified by new
  counter-claims. Applied automatically (no user review gate for now).
- **`new_thread`**: unit group is a novel angle, thread, or POV not present in
  the registry. The application adds it as a new book-level group with the
  unit as source provenance. Future units may continue it.
- **`cross_group_edge`**: relationship between two groups (unit↔unit,
  unit↔registry, or registry↔registry). E.g., a timeline event is also
  a turning point in a theme group; a character relationship drives a
  discourse claim. These edges enrich the book-level graph without
  modifying existing groups. Edge types reuse the same vocabulary as
  within-group edges (`precedes`, `causes`, `enables`, `supports`,
  `contradicts`, `refers_to`, etc.) — the relationship between two
  items is the same regardless of whether they belong to the same
  unit or different units.
- **`merge_groups`**: two registry groups should merge (discovered only now
  that more units reveal they represent the same thread). Rare — prefer
  cross_group_edge unless the groups are clearly the same thing.

### Registry group candidate selection

Same principle as concept resolution (D2): for each unit group, find registry
groups with concept overlap, item type similarity, or temporal adjacency.
Present compactly — group_id, group_type, summary, key concepts. Only
candidates go into the prompt. The same registry-scoping challenge applies;
start with deterministic overlap filtering, add semantic retrieval when needed.

## Implicit Reference Handling

When a later unit references a known entity without naming it:

- Unit 3 describes the signing of the "Treaty of Nanjing" (concept-0042).
- Unit 5 says: "The treaty's economic provisions were never enforced."
  The extraction pass creates a concept "the treaty" (surface: "the treaty")
  and an item about enforcement failure.
- The deterministic merge can't link "the treaty" to "Treaty of Nanjing"
  (different surfaces).
- Step 4 concept resolution: the registry index includes "Treaty of Nanjing"
  (matched via semantic retrieval — both are about a treaty, temporal
  proximity). The LLM sees "the treaty" (unit concept, summary: "treaty whose
  economic provisions were not enforced") against "Treaty of Nanjing"
  (registry concept, summary: "1842 treaty ending the First Opium War") and
  proposes `link` + `implicit_ref` on the item.

After step 4 application, the unit concept "the treaty" is linked to registry
concept "Treaty of Nanjing." Items that referenced "the treaty" now implicitly
reference "Treaty of Nanjing." The implicit_ref map flows into step 5.

### Cascade

```
Step 4 concept resolution:
  unit concept C_5 ("the treaty") → link → registry concept R_42 ("Treaty of Nanjing")
  implicit_ref: item I_12 → R_42 (because I_12.concept_refs includes C_5)

  Application: remap I_12.concept_refs: C_5 → R_42
  implicit_ref map: {I_12: {registry_concept: R_42, note: "refers to Treaty of Nanjing"}}

Step 5 grouping:
  LLM sees item I_12 with implicit_ref annotation
  → places I_12 in "Opium War Aftermath" group (correct), not generic "economic policy" group

Step 6 group resolution:
  "Opium War Aftermath" group → continue registry "Opium War" timeline (H_3)
```

## Implementation Status

Steps 1-10 are implemented (2026-05-31). The dual-signal candidate retrieval
(Q1 below) was added immediately rather than deferred — it is gated on registry
size (>50 concepts), falling back to deterministic-only for small registries.

### Files

| File | Status |
|------|--------|
| `tilusion/prompts/prompt_concept_resolution_v0.1.md` | Done |
| `tilusion/prompts/prompt_unit_grouping_v0.2.md` | Done |
| `tilusion/prompts/prompt_group_resolution_v0.1.md` | Done |
| `tilusion/reading_prompts.py` | Done — 3 new composition builders |
| `tilusion/reading_payloads.py` | Done — 3 new payload builders |
| `tilusion/registry_index.py` | Done — index builder, candidate selection, BM25, Qwen3-Embedding-0.6B dual-signal, RRF fusion |
| `tilusion/registry_delta.py` | Done — accepts LLM proposals |
| `tilusion/reading_pipeline.py` | Done — 2 new pass functions, revised grouping pass, pipeline wiring (TOTAL_STEPS=5) |
| `tests/test_cross_unit_resolution.py` | Done — 58 tests |

### Pipeline: 5 steps

```
Step 1: Overview segmentation (Conversation A)
Step 2: Per-segment extraction (Conversations B1..Bn)
Step 3: Cross-unit concept resolution (Conversation D) [NEW]
Step 4: Unit logical grouping v0.2 (Conversation C, revised)  [was step 3]
Step 5: Cross-unit group resolution (Conversation E) [NEW]
```

### Dual-signal candidate detection (D2)

The hybrid approach from D2 is fully implemented in `tilusion/registry_index.py`:

- **Deterministic pre-filter**: surface collision + type family + canonical_name
  exact match (always applied).
- **BM25 lexical**: zero-dependency BM25 over registry concept text
  (canonical_name + summary + observed_surfaces).
- **Qwen3-Embedding-0.6B**: lazy-loaded via sentence-transformers, 1024-dim
  embeddings, cross-lingual (ZH↔EN cosine similarity ≥0.80 on test pairs).
  Gracefully degrades to BM25-only if model can't be loaded.
- **Reciprocal Rank Fusion** (k=60): merges BM25 and embedding rankings into
  a single fused candidate list per unit concept.
- **Threshold**: dual-signal activates when registry >50 concepts. For ≤50,
  the full registry index is returned unchanged.

`select_concept_candidates()` unions deterministic and dual-signal candidate
sets — the deterministic pathway never loses surface-collision matches, and
the dual-signal pathway catches the "new surface" case (e.g., "the old man"
↔ "Shen Fu" with zero surface overlap).

## Resolved Questions

1. **Registry index format:** The compact one-line-per-concept format includes
   `concept_id`, `canonical_name`, `type`, one-line `summary`, and
   `observed_surfaces`. These are the fields needed for identity judgment.
   Temporal range and unit provenance can be omitted from the index (they're
   in the full registry record if needed).

2. **Concept resolution for unit 1:** Step 3 runs for unit 1 with an empty
   registry index. It handles within-unit concept corrections (`merge`, `split`,
   `refine`, `reclassify`) — replacing the concept_deltas role that the current
   grouping pass performs. The prompt and output schema work for both within-unit
   and cross-unit cases.

3. **Cross-unit edge types for groups:** Cross-group edges reuse the same
   edge vocabulary as within-group graph edges (`precedes`, `causes`, `enables`,
   `supports`, `contradicts`, `refers_to`, etc.). The relationship between two
   items is the same regardless of whether they belong to the same unit or
   different units. No special cross-unit edge types.

4. **Dual-signal candidate retrieval (was Open Q1):** Implemented. Activates
   when registry >50 concepts. Uses Qwen3-Embedding-0.6B (Apache 2.0, 0.6B
   params, 32K context, 0.988 R@1 on ZH→EN) via sentence-transformers with
   graceful degradation to BM25-only if the model is unavailable.

## Open Questions

1. **Re-extraction when concept resolution reveals major missed links:**
   Deferred. If a character appears under a completely different name and was
   extracted as a separate person, the concept link proposal fixes the
   identity — items get remapped. Re-extraction with updated context would be
   ideal but is not necessary for correctness. Flag severe cases in warnings.
