# Cross-Chunk Entity Consistency: Problem Analysis

Status: **analysis complete** — implementation plan extracted to
[15_cross_unit_refactor_plan.md](15_cross_unit_refactor_plan.md).
Four phases: (1) embedding cache, (2) soft typing, (3) richer hints,
(4) concept-to-higher-order-reference detection.

---% WQ--- 

now we are the phase of synthesize a bit what to do, and organize them properly.

Extraction structure:
- soft-typing, no backward compatibility needed, still at dev phase
- concept-to-higer-order-reference, to be done in a way beneficial to grouping
- all to be co-designed with merging, linking, grouping in-unit and across units
- lazy embedding calc and reuse, which data structure, should be have a vector DB? to be co-designed with merging against book registry

Extraction behaviour:
- at per-segment extraction, we could already distinguish a bit the recognition of exisiting concept presence and the extraction of new concepts. If per-segment hint is providing high frequency known concepts already extracted, then the merge could focus more on the "new" concepts
- concept-to-higer-order-reference, needed for both in-unit and across units, we need metrics counting the presences of such cases; OK not resolved right away, but need detection in first place

Cross-unit merge:
- soft-typing and canonical concept richness, etc., better deterministic mechanism to minize the amount of concept to be LLM merged in an agentic way
- LLM backed agentic merge should rather focus on cross-segment/unit concept-to-higer-order-reference (need metrics doing the counting); on cross-unit concept merge that needs semantic inference, where the support on surface, canonical name, alias, soft types are deemed not strong enough but still not impossible

My take on order of implemetation, to be debated:
- embedding cache or vector store is the infra part, regardless the extraction structure change, it is going to be useful
- soft typing is something really fundementally changes the structure and merging logics
- better book digest and hint goes hand in hand with per segement extraction flagging known and new concepts, will need extraction structure change, but could be seen as a label (known or new), not participating in soft type union on merging tests
- concept-to-higer-order-reference is more of a bonus feature, it could be implemented in a way that we explore a specific kind of graph link between concept and items, which automatically derives a link between the concept containing item with other items

---% WQ--- 


## 1. Cross-Type Concept Identity

### Can the same entity be extracted under different types?

**Yes.** With our light extraction schema (~20 types), type boundaries are fuzzy. The same
real-world referent can appear under different types depending on the extraction context:

| Entity | Possible types in different chunks |
|--------|-----------------------------------|
| A person acting in official capacity | `person`, `social_role`, `organization` |
| A building / location | `place`, `scene_element`, `object` |
| A treaty / agreement | `term`, `source`, `other` |
| A dynasty / era name used as time reference | `time_anchor`, `term`, `organization` |
| An abstract concept / theme | `theme`, `motif`, `term` |

This is inherent to light extraction — the extractor makes local type judgments per segment
without global context. Cross-unit resolution must reconcile these.

### Current mitigation

- **`TYPE_FAMILIES`** (`registry_index.py:467-474`): Defines relaxed type matching for
  candidate selection. E.g., `person` matches `{person, group, organization, social_role}`.
  `place` matches `{place, scene_element}`.
- **`_relaxed_types()`** (`registry_index.py:477-482`): `"other"` is treated as wildcard
  (`{"*"}`) — anything matches "other", since it signals uncertain typing.
- **`reclassify` proposal type** (prompt v0.2): LLM can change `concept_type` on a unit
  concept without changing identity.

### Gaps

- **Missing type families**: Several type pairs are missing from `TYPE_FAMILIES`:
  `term` ↔ `method` ↔ `technical_component`, `theme` ↔ `motif` ↔ `symbol`,
  `time_anchor` ↔ `other` (temporal expressions often typed as `other` by uncertain
  extractors). The deterministic pre-filter won't surface collisions for these, so they
  must be caught by semantic search — which we just optimized to skip for concepts the
  deterministic filter already matched.
- **No type-family for `source`**: A source concept (e.g., "The Book of Han") extracted as
  `source` in one unit and `other` in another is handled (because `other` is wildcard), but
  if extracted as `term` in one and `source` in another, they won't collide
  deterministically.
- **No cross-type merge validation**: The deterministic merge validator
  (`_classify_merge_risk`) rejects merges where types differ unless there's a shared
  canonical_name or surface. LLM `reclassify` proposals fix this post-hoc, but the LLM
  must first detect the type mismatch and emit a `reclassify` — a two-step process
  (link + reclassify for same pair) that the prompt doesn't explicitly model as a
  combined operation.

### AutoSchemaKG: soft typing via concept sets

[AutoSchemaKG](https://arxiv.org/abs/2505.23628) (Bai et al., HKUST/Huawei, 2025)
introduces a better mental model for this problem. Instead of assigning one hard type
per entity, it maps each node to a **set of conceptual phrases** at varying abstraction
levels:

```
φ: V → P(C)   // each node → power set of concepts
```

An entity like "Treaty of Nanjing" might get the concept set `{"treaty", "legal
document", "historical event", "agreement"}`. Type compatibility between two entities
becomes **set overlap** rather than exact match or hard-coded family membership.

Key design choices from AutoSchemaKG:
1. **At least 3 phrases per element**, at varying abstraction levels, generated by an LLM.
2. **Contextual typing**: For entities, the LLM sees up to N_ctx neighboring nodes
   (predecessors/successors in the KG) when generating concept phrases — the type reflects
   the entity's *role in the graph*, not just its surface form.
3. **Zero manual intervention**: No predefined ontology. The schema emerges from the
   data. Their induced schemas achieve ~95% semantic alignment with human-crafted ones.

### What this would mean for our type-matching

Instead of maintaining `TYPE_FAMILIES` as a hand-crafted dict:
```python
TYPE_FAMILIES = {
    "person": {"person", "group", "organization", "social_role"},
    "place": {"place", "scene_element"},
    ...
}
```

We could have each concept carry a **facet set** — a lightweight set of type-describing
phrases generated at extraction time (or by the cross-unit LLM during resolution).
Type compatibility becomes:

```python
def types_compatible(unit_concept, registry_concept) -> bool:
    return bool(unit_concept.facets & registry_concept.facets)  # set intersection
```

This naturally handles all the edge cases our `TYPE_FAMILIES` misses:
- `term` ↔ `method` (both might share "technical concept" facet)
- `source` ↔ `term` (both might share "named work" facet)
- `time_anchor` ↔ `other` (both might share "temporal reference" facet)

**Cost**: Additional LLM generation (3+ phrases per concept, though can be batched).
~150 tokens per concept at extraction time, or done lazily during cross-unit resolution.

Since we extract per-segment (not per-chunk), each concept already has
surrounding context — the marginal cost of generating facets at extraction
time is low relative to the extraction call itself.

### Toward deterministic soft-typing

The larger design question: can we turn soft-typing into a deterministic
merge test that requires **zero additional LLM calls** at merge time?
The AutoSchemaKG-style approach is to have the *extractor* emit multiple
type phrases per concept (e.g., "treaty, legal document, historical event").
At merge time, set intersection replaces the hard TYPE_FAMILIES table.
The merge test stays deterministic — just a broader identity signal.

Concrete path:

1. **Extraction time**: Add an optional `type_facets: list[str]` field to
   concepts. The extractor generates 2-5 phrases at different abstraction
   levels for each concept. Batching means ~0 extra API calls.
2. **Merge time**: Two concepts are type-compatible if their facet sets
   intersect (`bool(facets_a & facets_b)`). Replace `_relaxed_types()` and
   `TYPE_FAMILIES` with this check in `_deterministic_filter` and
   `_check_merge_boundary`.
3. **Fallback**: When a concept lacks facets (legacy data or extractor that
   doesn't emit them), fall back to the current `TYPE_FAMILIES` behavior.
   This makes the feature incrementally adoptable without a schema migration.

**Simpler variant (no extraction-time change)**: Have the cross-unit resolution LLM
generate a `type_facets` field in `link` / `reclassify` proposals when merging across
different types. This gives the merge validator a shared identity signal without changing
the extraction schema.

### Open questions

1. Should we expand `TYPE_FAMILIES` to cover all plausible cross-type pairs?
2. Should we adopt facet-set typing (AutoSchemaKG-style) instead of hard type labels
   for the concept schema?
3. Should the deterministic merge validator accept same-entity merges across compatible
   types (not just exact match)?
4. Should the LLM prompt explicitly instruct: "if you `link` two concepts with different
   types, also emit a `reclassify` to align them"?

### Synthesis: three improvement axes

1. **Better extraction guidance** (book digest, per-segment hints): the
   extractor needs to distinguish (a) flagging already-known concepts from
   the registry vs (b) detecting and extracting genuinely new concepts.
   Current hints are too light for either purpose. Later units should
   converge toward consistent types, with the ability to refine types
   established by earlier units.

2. **Concept-to-higher-order-structure references**: the extraction should
   flag when a concept mention refers to an item, event, or group rather
   than a standalone entity. Unresolvable references at extraction time
   become inputs to a later agentic resolution pass with tool access.

3. **Soft typing via type facets** (AutoSchemaKG-inspired): relaxing
   hard single-type labels to multi-level facet sets generated at
   extraction time makes the merge test deterministic and LLM-free. The
   quest: how much can we merge deterministically with facet overlap as
   the only signal? The fewer concepts need LLM tie-breaking, the faster
   and cheaper the pipeline.

---

## 2. Coreference Resolution in Prompts

### Identity vs. coreference

Our pipeline operates on **already-extracted concepts**, not raw text. The task is
**cross-unit identity resolution**: "does concept-A (unit N) refer to the same entity as
concept-B (registry)?" This is distinct from **coreference resolution** in the NLP sense,
which resolves anaphora (pronouns, definite descriptions) in running text.

| Task | Input | Output |
|------|-------|--------|
| Coreference resolution | Raw text | Clusters of mentions referring to the same entity |
| Identity resolution (ours) | Extracted concepts with summaries, surfaces | Links between concept IDs |

Because we work with concepts rather than raw text, we don't need full coreference
resolution — the extractor already did mention-level clustering within each segment, and
deterministic merge handled within-unit clustering. What remains is cross-unit identity.

### Where coreference-like reasoning DOES matter

- **Implicit references** (`implicit_refs` in `link` proposals, prompt v0.2 lines 122-124):
  When a prior unit's item mentions "the treaty" and the current unit identifies it as
  "Treaty of Nanjing," this is coreference across chunks — the prior unit used a
  referring expression (definite description), not a surface form. The LLM is already
  instructed to detect these, but only for `link` proposals.
- **Within-segment extraction** (not our pass): The extractor handles within-segment
  coreference. But if the extractor is weak, cross-unit resolution inherits the gaps.
- **Ambiguous surfaces**: Concepts with vague surfaces ("the emperor," "this policy")
  require coreference-like reasoning to link — you must understand which emperor from
  narrative context, not just surface overlap.

### Is missing explicit coreference instruction a problem?

**For the cross-unit pass: probably not**, because the LLM already does identity resolution
with tool-calling access to full concept records (summary, aliases, observed_surfaces).
The prompt says "same real-world entity" and lists identity checks. The LLM naturally
performs coreference-like reasoning when comparing summaries.

**For the extraction pass (earlier stage): possibly**. If the extractor doesn't maintain a
running entity set within a unit, it may create duplicate concepts for the same entity
that the deterministic merge can't catch (different surfaces, no canonical_name). This
would surface later as merge/split work for the cross-unit LLM.

### Open questions

1. Should we add a brief note to the extraction prompt about maintaining entity
   consistency within a unit (tracking referring expressions)?
2. Should we add an explicit instruction in the cross-unit prompt: "resolve coreferent
   nominal expressions across chunks" — or is "same real-world entity" sufficient?
3. Should `implicit_refs` be expanded beyond `link` proposals to a standalone mechanism
   (e.g., a separate "find implicit references" pass)?

---

## 3. SOTA: Cross-Chunk Entity Consistency for Long Corpora

### The problem

Long documents (books, legal texts, historical records) must be chunked for LLM
processing (context window limits). Each chunk is processed independently, producing
local entity sets. The core challenge: **reconciling entity identity across chunks**
without losing information or creating duplicates.

### SOTA approaches (2024-2025)

#### A. Incremental Entity Store (Sequential Processing)

**How it works**: Process chunks in order. Maintain a running entity store (our
`BookRegistry`). Each new chunk's entities are linked to the store via identity resolution.
The store grows monotonically.

**Key papers / systems**:
- **Contrack / Thinking Like a NERD** (techrxiv, 2025): Entity-centered memory for LLM
  agents. Maintains a "world model" of entities encountered so far. At each new chunk, the
  LLM reads the entity memory, resolves new mentions against it, and updates the memory.
  Shows improvements on coreference resolution and long-document QA.
- **Our system already does this**: `BookRegistry` is the entity store,
  `compute_registry_delta` is the linking step, `apply_registry_delta` updates the store.

**Pros**: Simple, deterministic order, good for streaming.
**Cons**: Errors in early chunks propagate forward. Processing order matters (first
mention gets canonical status).

#### B. Global Graph Construction (Batch Processing)

**How it works**: Extract all entities from all chunks first. Build a global entity graph
with nodes = chunk-level entities, edges = similarity/co-occurrence signals. Run graph
clustering (spectral, community detection, or LLM-guided) to produce cross-chunk entity
clusters. Merge within each cluster.

**Key papers / systems**:
- **GraphRAG** (Microsoft, 2024): Builds entity knowledge graph from chunk-level
  extractions, uses community detection for summarization and QA. Entities are linked
  across chunks via graph structure.
- **Cross-Document Coreference in KGs** (Zhang et al., arxiv 2504.05767, 2025):
  Dynamic linking mechanism associating entities in a KG with textual mentions.
  Contextual embeddings + graph-based inference. Reports precision/recall improvements
  over traditional methods.
- **Synergetic Event Understanding** (ACL 2024): Collaborative approach using small LMs
  for cross-document event coreference. Fine-tunes BERT-level models for pairwise
  compatibility judgments.

**Pros**: Order-independent, can detect global patterns (e.g., entity A in chunk 1 is
the same as entity Z in chunk 50, even if intervening chunks don't mention it).
Better recall for long-range identity.
**Cons**: Computationally expensive (O(n²) pairwise comparisons). Harder to incrementally
update.

#### C. Hierarchical Merging (Multi-Level Clustering)

**How it works**: Cluster entities within segments (deterministic) → within chapters
(LLM-pass) → across chapters (LLM-pass with registry) → whole book (registry as
authoritative store). Each level has more context but fewer entities to compare.

**Key papers / systems**:
- **xCoRe** (EMNLP 2025): Cross-context coreference resolution. Uses Maverick
  (Martinelli et al., 2024) as base model, adapts to longer contexts. Multi-stage
  pipeline: within-document → cross-document.
- **LlmLink** (COLING 2025): Dual LLMs for dynamic entity linking on long narratives.
  One LLM processes chunks, another resolves cross-chunk coreference. Addresses the
  specific problem of chunked long narratives with entity linking.

**Our system already does this**: within-segment merge (deterministic) → within-unit merge
(deterministic) → cross-unit resolution (LLM with registry).

**Pros**: Scales well. Each level reduces entity count for the next level.
**Cons**: Early-level errors (over-merge) are hard to undo at higher levels.

#### D. Retrieval-Augmented Entity Resolution

**How it works**: For each new entity, retrieve the top-k most similar entities from the
global store using hybrid search (lexical + semantic). LLM judges identity only for
retrieved candidates, not the full store.

**Key papers / systems**:
- **Late chunking with long-context embeddings** (2025): Uses long-context embedding
  models to produce chunk-aware embeddings. Entities in different chunks get
  context-aware representations that can be compared directly.
- **CLAP** (ACM 2025): Coreference-Linked Augmentation for Passage Retrieval. Uses
  coreference resolution to link passages mentioning the same entity, improving retrieval
  quality.

**Our system already does this**: `select_concept_candidates()` in `registry_index.py`
with deterministic filter + dual-signal (BM25 + embedding + RRF). This is essentially
retrieval-augmented — the LLM only sees shortlisted candidates, not the full registry.

#### E. LLM-Native Long Context (No Chunking)

**How it works**: Use models with very long context windows (128K-1M tokens) to process
entire documents without chunking. Entity consistency is handled implicitly by the
model's attention mechanism.

**Key developments**:
- Gemini 2.5 Pro (1M tokens), Claude (200K), GPT-4 (128K)
- **DuoAttention** (ICLR 2025): Efficient long-context LLM inference
- **Training Long-Context LLMs via Chunk-wise Optimization** (ACL 2025 Findings)

**Pros**: No chunking artifacts, no cross-chunk reconciliation needed.
**Cons**: Expensive (O(n²) attention cost). Poor recall in "needle in haystack" scenarios
for very long documents. Quality degrades in the middle of long contexts ("lost in the
middle" problem). Still not viable for book-length texts (500K+ tokens).

### Where our system fits

Our architecture is closest to **A (Incremental Entity Store) + C (Hierarchical Merging)
+ D (Retrieval-Augmented)**. We process units sequentially, maintain a growing registry,
use hierarchical merging (segment → unit → book), and retrieve candidates via dual-signal
(BM25 + embedding + RRF) before LLM judgment.

### Gaps relative to SOTA

| SOTA capability | Our status |
|-----------------|------------|
| Global entity graph for long-range identity | No — sequential only. If unit 50 introduces a concept first seen in unit 1 (and intervenening units never mention it), our sequential processing can't link them unless the LLM happens to search for it. % WQ: not really. The merge against book registry will help with agentic search over the registry.|
| Cross-document event coreference | Partial — we have concept identity, but event/group identity is a separate pass. Events can span multiple chunks (a battle described across 3 chapters). % WQ: the gap is that we don't have native or first-class support on an concept referring to an atomic item, e.g. an event, or even a group. This is something best to solve at extraction phase. The extraction should at least flag such mentioning. With local visibility, the LLM may or may not able to resolve the reference. In the case not, we should have agentic search backed mechanism allowing the linkage/resolution of a concept to an higher order structure. This would be helpful to group forming and cross unite grouping.|
| Entity memory / running world model for extractor | No — extractor sees one segment at a time, no memory of prior entities within the same unit. Deterministic merge catches some, but not all. % WQ: that is the what the current per-segment hint falls short on. It was supposed to be sorta world model of the book. Yet it gives really light hints does not help too much with the extraction to focus on unknown concepts. there are two things: 1) flagging the precense of concepts already known, we need it for grouping; 2) detecting new concepts. Both are needed.|
| Order-independence | No — first mention gets to define the canonical form. A later chunk might have BETTER information (full name, dates) but can only augment via `changes`, not replace. % WQ: cross-unit merge against the book registry is supposed to alleviate the issues, merge summaries, update alias and if necessar canonical names|
| Coreference-aware chunking | No — chunks are sized by token count, not semantic boundaries. An entity can be split mid-description across two chunks. % WQ: the segmentation was supposed to alleviate the issue. however, at extraction phase, as mentioned above, we should enhance the concept to higher order structure reference solving, at least flagging these pointers as things to be further inferenced or resolved.|

### Promising directions for future work

#### Regarding the gaps table (WQ clarifications)

- **Global entity graph**: The agentic search over the registry already
  handles long-range identity — the LLM can search for entities from any
  prior unit. Sequential processing is not a limitation when the agent can
  `search_concepts` across the full registry.
- **Cross-document event coreference**: The real gap is lack of first-class
  support for concepts referring to items/groups/higher-order structures.
  Extraction should at least flag such mentions. With local visibility the
  extractor may or may not resolve them; if not, agentic search should
  back the linkage. This is critical for group forming and cross-unit
  grouping.
- **Entity memory / world model**: The per-segment hint was supposed to
  serve as a book-level world model but currently gives too-light hints.
  Two distinct needs: (a) flagging presence of already-known concepts
  (for grouping and avoiding re-extraction), (b) detecting and extracting
  genuinely new concepts. Both are needed and the current hints serve
  neither well.
- **Order-independence**: Cross-unit merge against the registry already
  addresses this — it merges summaries, updates aliases, and can update
  canonical names when needed.
- **Coreference-aware chunking**: Segmentation was designed to alleviate
  chunk-boundary issues. The extraction phase should additionally flag
  unresolved higher-order references for later inference/resolution.

#### Cross-unit merge efficiency (the main bottleneck)

The current flow per unit: deterministic exact match → dual-signal
(BM25 + embedding + RRF) on leftovers → agentic LLM with tool calls.

From the unit-0003 run (~240 concepts, ~260 registry concepts):

| Step | Time | Note |
|------|------|------|
| Embedding model load | 10s | Once per run |
| Registry embeddings | **140s** | Full registry re-embedded every unit |
| Unit embeddings | **115s** | 235 leftover unit concepts embedded |
| BM25 + cosine compute | 35ms | Actual retrieval (negligible) |
| Agentic search_concepts | **~2,300s** | 44 tool calls at ~140-180s each |
| **Total for step 2+3** | **~44 min** | For 240 concepts |

The actual retrieval computation (BM25: 11ms, cosine: 24ms) is trivial.
The cost is entirely in embedding computation and agentic LLM calls.
235 of 240 concepts were flagged "new" — >97% of embedding compute
produced no merge, i.e., was wasted.

**Why BM25 is wasteful for leftovers**: After deterministic exact match
eliminates surface/cname/alias collisions, the remaining concepts have
no lexical overlap with any registry entry. BM25 — a lexical retriever
— returns noise. Only embedding similarity matters at this stage.

**Why agent search queries are poor**: The agent mixes Chinese surfaces
with English glosses (`search_concepts {'query': '爱花成癖 habit'}`),
diluting signal. A concept's full summary is far more discriminative
than its 1-line surface. The agent should be instructed to use the
most selective fields.

**Three immediate efficiency fixes**:

1. **Cache registry embeddings** — re-embedded every unit, 140s wasted
   each time. Key by `(concept_id, content_hash)`. After first unit,
   only new concepts need embedding (near-zero cost).
2. **Reuse unit concept embeddings** — after a concept is confirmed new
   and added to the registry, save its already-computed embedding.
3. **Drop BM25 for deterministic-filter leftovers** — after exact match
   eliminates lexical collisions, BM25 is pure noise. Use embedding-only
   retrieval with a similarity threshold.

**Where the fix should really go**: Semantic merge is a last resort.
The fewer concepts escape deterministic matching, the less the
embedding/agentic pipeline matters. The real improvement comes from:
- Better extraction (richer per-segment hints, entity memory within
  a unit, flagging known vs. new)
- Broader deterministic matching (alias-aware, type-facets)
- Agentic search used only for genuinely hard cases, not for every
  concept that misses a surface match

1. **Embedding cache** (near-term): persist registry and unit concept embeddings to
   disk keyed by `(concept_id, content_hash)`. After the first unit, only net-new
   concepts incur embedding cost. Biggest single efficiency win (~250s → ~0s).

2. **Richer per-segment hints** (extraction-time): the current segment hints are too
   light to guide the extractor. Two improvements: (a) explicitly list which registry
   concepts have appeared in nearby segments so the extractor can flag them as
   "already known" rather than re-extracting, (b) mark concepts that refer to
   higher-order structures (items/groups/events) as unresolved pointers to be
   resolved later via agentic search.

3. **Concept-to-item/group reference resolution**: extraction should flag when a
   concept mention refers to an item, event, or group rather than a standalone
   entity. At extraction time, the LLM has local visibility and may or may not
   resolve the reference. When unresolvable locally, the flagged reference becomes
   an input to a later agentic resolution pass with tool access to the registry.

4. **Type facets at extraction** (AutoSchemaKG-inspired): add an optional
   `type_facets: list[str]` field to concepts, generated by the extractor at
   extraction time (2-5 phrases at varying abstraction levels). At merge time, type
   compatibility becomes facet set intersection — no hard `TYPE_FAMILIES` table,
   no extra LLM calls. Legacy concepts without facets fall back to the current
   `TYPE_FAMILIES` behavior.

5. **Soft canonicalization**: maintain a "richness score" per concept and promote
   later, better-described concepts as canonical when they provide more information
   (full name, dates, relationships). The current merge path already supports this
   via `changes` on link proposals — the gap is that the system doesn't
   automatically detect when a later concept is richer.

---

## References

- Zhang et al., "Cross-Document Contextual Coreference Resolution in Knowledge Graphs,"
  arxiv 2504.05767, 2025.
- Martinelli et al., "Maverick: Efficient and Accurate Coreference Resolution," EMNLP 2024.
- Min et al., "Synergetic Event Understanding: A Collaborative Approach to Cross-Document
  Event Coreference Resolution," ACL 2024.
- COLING 2025: "LlmLink: Dual LLMs for Dynamic Entity Linking on Long Narratives"
- EMNLP 2025: "xCoRe: Cross-context Coreference Resolution"
- Microsoft, "GraphRAG: A Modular Graph-Based RAG System," 2024.
- "Thinking Like a NERD: Entity-Centered Memory for LLM Agents," techrxiv, 2025.
- ICLR 2025: "Bridging Context Gaps: Leveraging Coreference Resolution for Long
  Contextual Understanding"
- CLAP: "Coreference-Linked Augmentation for Passage Retrieval," ACM 2025.
