You review one unit's resolved concepts and atomic items and build logical groups with optional graph structure. Concepts and items are already resolved; do not re-extract source text and do not edit concepts or items.

{{ language_policy }}

{{ input_contract }}

## Output schema

```json
{
  "unit_id": "copy input unit_id exactly",
  "logical_groups": [
    {
      "group_id": "group-0001",
      "group_type": "temporal_sequence",
      "summary": "一次局部事件链，呈现人物行动的先后关系。",
      "item_refs": ["item-0001", "item-0002"],
      "concept_refs": ["concept-0001"],
      "graph": {
        "nodes": [{"node_id": "node-0001", "item_ref": "item-0001", "label": ""}],
        "edges": [
          {
            "source": "node-0001",
            "target": "node-0002",
            "edge_type": "precedes",
            "summary": "前一事项发生在后一事项之前。",
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
  "unresolved_items": [],
  "warnings": []
}
```

{{ type_vocabularies }}

## Rules

- Build groups from `atomic_items`; `item_refs` must reference input item IDs.
- `concept_refs` must reference input concept IDs.
- Graph node `item_ref` must be in the same group's `item_refs`.
- Graph edge endpoints must reference node IDs inside the same group.
- Use only these edge types: `mentions`, `refers_to`, `aliases`, `same_as_candidate`, `part_of`, `elaborates`, `supports`, `contradicts`, `qualifies`, `contrasts`, `causes`, `enables`, `explains`, `follows_from`, `precedes`, `continues`, `resolves`, `raises_question`, `answers_question`, `exemplifies`, `defines`, `uses_method`, `produces_result`, `has_limitation`, `related_to`, `other`. Do not use custom edge types.
- Prefer fewer meaningful groups, but do not force a local episode into a huge timeline. A small ordered episode can be a `temporal_sequence`; broader arcs should be `timeline`. A `timeline` must span ≥10 items or multiple segments — single episodes use `temporal_sequence`.
- For `timeline` and `temporal_sequence`, order `item_refs` chronologically when possible. Use item `temporal_attributes`, time-anchor concepts, and source order; note uncertainty when ordering is inferred.
- Adjacent temporal sequences with the same key entities and continuous time should either be represented as one `timeline` group or connected later through `part_of` / `precedes` cross-group edges. Inside one unit, use `timeline` when you can identify the larger arc confidently.
- Flat collections such as `theme_set`, `contrast_set`, and `method_example_set` may have an empty graph.
- `source_block_refs` on graph edges should cite text supporting the relationship itself, not merely the source/target items. Omit when the edge is inferred.
- Every `atomic_item` in the input must appear in at least one group's `item_refs` or in `unresolved_items`. Do not silently drop items. If an item does not fit any group, list it in `unresolved_items` with a brief reason.
- Resolve input `unresolved_items` only when the current structures make the answer clear; otherwise carry them forward.
- Preserve uncertainty instead of inventing facts.
