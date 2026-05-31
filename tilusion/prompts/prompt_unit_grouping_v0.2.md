You review one unit's merged and resolved concepts and atomic items, and build logical groups with optional graph structure. Concepts are already resolved by a prior cross-unit pass — do not propose merges, splits, or type changes.

**CRITICAL — Language:** Write ALL text fields in the source language (Chinese→Chinese, English→English). Never translate or mix. Only group_type, edge_type, and node/item/concept IDs use English vocabulary.

## Hierarchy (one-directional dependency chain)

- A book is split into extraction units (chapters, sections, or large chunks).
- Each unit is split into segments for per-segment extraction.
- Per-segment extraction produces local concepts and atomic items grounded in deterministic source blocks. Atomic items declare which source blocks they draw from (`source_block_refs`); the reverse mapping is computed deterministically.
- A deterministic merge step merges concepts with matching identity signals into unit-level concepts.
- A prior cross-unit pass resolves concept identity (links concepts to registry, resolves within-unit merges/splits, captures implicit item references).
- **This pass** builds logical groups from atomic items using the resolved concepts. Logical groups are built from atomic items — they do not reference source blocks directly.
- A later group resolution pass continues or mutates groups across units.

You receive already-extracted and already-resolved structures. Your job is grouping only. Do not re-extract from source text. Do not edit concepts.

The caller provides JSON with:
- `task`: `unit_logical_grouping`.
- `schema_version`: `reading-unit-v0.3`.
- `unit_id`: parent reader unit identifier.
- `unit_text`: the full original unit source text. This is reference material for resolving ambiguous surfaces and understanding context. Do not re-extract from it.
- `source`: book-level metadata (path, title, unit label).
- `segments`: segment metadata with `segment_id`, `title`, and `summary` from the overview pass. Provides coarse navigation context — which segment covers what — to inform grouping decisions without re-reading the full unit text.
- `concepts`: merged and resolved unit-level concepts. Each has a `concept_id` (clean `concept-NNNN`), `surface`, `concept_type`, `merged_from`, `summary`, and all standard concept fields. Do not modify these — they have already been reviewed.
- `atomic_items`: stabilized unit-level items with `item_id` (clean `item-NNNN`), `concept_refs` pointing to the concept IDs above, and all standard item fields.
- `implicit_refs`: optional map from prior-unit implicit references. Maps concept_id → list of {item_ref, concept_ref, reason} objects from the cross-unit resolution pass. Use this to understand which concepts have cross-unit narrative continuity, but do not modify concept_refs — the caller handles that.
- `unresolved_items`: items the prior pass could not resolve. Escalate if still ambiguous; otherwise drop them.
- `context`: reserved for future cross-unit continuity guidance. Currently empty.

Return only one JSON object. Do not include prose, markdown, or code fences.

Required top-level keys:
- `unit_id`: copy input `unit_id` exactly.
- `logical_groups`: logical/thematic groupings of atomic items.
- `unresolved_items`: items you cannot confidently resolve.
- `warnings`: free-text notes on uncertainties, limitations, or decisions.

Minimum shape:

```json
{
  "unit_id": "unit-0001",
  "logical_groups": [
    {
      "group_id": "group-0001",
      "group_type": "timeline|temporal_sequence|theme_set|concept_map|discourse_graph|claim_evidence_map|viewpoint_evolution|open_thread_list|method_example_set|motif_development|contrast_set|other|custom",
      "summary": "short description of this group",
      "item_refs": ["item-0001", "item-0003"],
      "concept_refs": ["concept-0001"],
      "graph": {
        "nodes": [
          {"node_id": "node-0001", "item_ref": "item-0001", "label": "optional label"}
        ],
        "edges": [
          {
            "source": "node-0001",
            "target": "node-0002",
            "edge_type": "precedes|causes|enables|explains|follows_from|continues|resolves|supports|contradicts|qualifies|contrasts|elaborates|part_of|refers_to|exemplifies|defines|uses_method|produces_result|has_limitation|raises_question|answers_question|aliases|same_as_candidate|related_to|other|custom",
            "summary": "short description of this edge",
            "source_block_refs": [],
            "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            "uncertainty": []
          }
        ]
      },
      "uncertainty": [],
      "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}
    }
  ],
  "unresolved_items": [
    {
      "item_id": "unresolved-0001",
      "kind": "ambiguous_item|other",
      "summary": "what is uncertain and why"
    }
  ],
  "warnings": []
}
```

Rules:

- `unit_text` is reference material only. Do not re-extract concepts or items from it.
- The input `concepts` list is the authoritative concept set. Do not modify concepts — they have already been resolved by the prior cross-unit pass.
- Items are stable. Do not modify item content. If `implicit_refs` provides continuity cues, use them to inform grouping decisions and item placement.
- Do not emit concept_deltas. Concept identity is already resolved.

Logical group guidance:

- Group items by their narrative function: what happens (plot events, actions), who characters are (traits, backstory, relationships), what the world is like (setting, history, rules), and what ideas recur (themes, motifs, symbols). Items that serve the same narrative purpose belong together.
- Flat collection groups (`theme_set`, `contrast_set`, `open_thread_list`, `method_example_set`) do not need a graph — `group_type`, `summary`, and `item_refs` alone is fine. For structure-rich group types (`timeline`, `temporal_sequence`, `discourse_graph`, `claim_evidence_map`, `concept_map`, `viewpoint_evolution`, `motif_development`), provide graph nodes and edges to capture relationships between items. See specific edge type guidance below.
- Prefer fewer meaningful groups over many tiny ones. Aim to place most items in a group — an item should be ungrouped only when it genuinely stands alone with no narrative connection to any other item. Cross-group membership is allowed.
- `item_refs` must refer to existing input `atomic_items[*].item_id` values.
- `concept_refs` must refer to existing input `concepts[*].concept_id` values.
- Graph `nodes[*].item_ref` must refer to an item in `item_refs` for the same group.
- Graph `edges[*].source` and `edges[*].target` must refer to `node_id` values within the same group's graph.
- `source_block_refs` on edges must cite existing source block IDs when the edge is source-grounded (grounding: "source_grounded"). Most graph edges are synthetic/inferred — use `"grounding": "llm_inferred"` and omit `source_block_refs` unless the relationship is directly stated in a specific source block.
- `group_type` is schema-light. Use a recommended type when it fits; otherwise use `other` or a concise custom string.
- A `timeline` or `temporal_sequence` group should use a graph with `precedes`, `causes`, `enables`, `continues`, `resolves`, or `follows_from` edges. **Order `item_refs` in absolute chronological order** (earliest first), not in narrative/appearance order. Use each item's `temporal_attributes` to determine chronological position. Items with explicit dates or absolute time anchors come first; items with only relative temporal hints come after. If two items have ambiguous temporal ordering, place the one appearing earlier in the narrative first and note the uncertainty in `warnings`.
- A `discourse_graph` or `claim_evidence_map` group should use `supports`, `contradicts`, `qualifies`, `contrasts`, `elaborates`, `raises_question`, or `answers_question` edges.

Unresolved items guidance:

- Resolve the input `unresolved_items` where the unit text and context make the answer clear.
- Escalate only genuinely ambiguous cases where you cannot decide.

Prior context is guidance for aliasing and continuity. It is never evidence for facts in the current unit.

Preserve uncertainty instead of inventing facts. If the source is ambiguous, say so in `warnings` or escalate to `unresolved_items`.
