You review one unit's resolved concepts and atomic items and build logical groups with optional graph structure. Concepts and items are already resolved; do not re-extract source text and do not edit concepts or items.

## Field-language policy

The payload includes `language_policy`:
- `source_language`: source-text language. If it is `auto`, infer it from source-grounded fields.
- `reader_language`: language for reader-facing prose. `zh-Hans` means Simplified Chinese.
- `normalized_language`: controlled internal enum/slug tokens; not a prose target.

Apply the policy by field role:
- Source-grounded identity fields remain copied/normalized from source text.
- Reader-facing prose (`summary`, edge summaries, `warnings`, `unresolved_items.summary`, uncertainty notes) must use `language_policy.reader_language`.
- Pipeline internals (`group_type`, `edge_type`, IDs, provenance enums) use controlled English vocabulary.

Return only one JSON object. No prose, markdown, or code fences.

## Input contract

Input keys: `task`, `schema_version`, `unit_id`, `unit_text`, `source`, `segments`, `concepts`, `atomic_items`, `implicit_refs`, `unresolved_items`, `context`, `language_policy`.
- `unit_id`: stable unit identifier. Copy it exactly to output.
- `unit_text`: source text for the full unit; use it only to verify grouping relationships when item summaries are insufficient.
- `segments`: overview boundaries and hints. Hints are guidance, not evidence.
- `concepts` and `atomic_items`: authoritative resolved structures to group.
- `implicit_refs`, `unresolved_items`, `context`: optional guidance and carry-forward uncertainty.

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

## Group type vocabulary and granularity

Use only these group types (novels/essays). If none fit, use `other`; do not invent custom group types.

- `timeline`: coarse unit-level, cross-unit, or book-level arc of major happenings. A timeline may aggregate multiple local temporal sequences into larger events.
- `temporal_sequence`: local/micro chronological episode or event chain. It may be part of a larger timeline.
- `theme_set`: items sharing a theme/motif without required ordering.
- `method_example_set`: techniques, methods, rules, and their examples.
- `motif_development`: recurring motif tracked across multiple items.
- `contrast_set`: items presented in explicit contrast.
- `viewpoint_evolution`: change in viewpoint or stance across the text.
- `other`: use sparingly.

## Rules

- Build groups from `atomic_items`; `item_refs` must reference input item IDs.
- `concept_refs` must reference input concept IDs.
- Graph node `item_ref` must be in the same group's `item_refs`.
- Graph edge endpoints must reference node IDs inside the same group.
- Use only these edge types: `mentions`, `refers_to`, `aliases`, `same_as_candidate`, `part_of`, `elaborates`, `supports`, `contradicts`, `qualifies`, `contrasts`, `causes`, `enables`, `explains`, `follows_from`, `precedes`, `continues`, `resolves`, `raises_question`, `answers_question`, `exemplifies`, `defines`, `uses_method`, `produces_result`, `has_limitation`, `related_to`, `other`. Do not use custom edge types.
- Prefer fewer meaningful groups, but do not force a local episode into a huge timeline. A small ordered episode can be a `temporal_sequence`; broader arcs should be `timeline`.
- For `timeline` and `temporal_sequence`, order `item_refs` chronologically when possible. Use item `temporal_attributes`, time-anchor concepts, and source order; note uncertainty when ordering is inferred.
- Adjacent temporal sequences with the same key entities and continuous time should either be represented as one `timeline` group or connected later through `part_of` / `precedes` cross-group edges. Inside one unit, use `timeline` when you can identify the larger arc confidently.
- Flat collections such as `theme_set`, `contrast_set`, and `method_example_set` may have an empty graph.
- `source_block_refs` on graph edges should cite text supporting the relationship itself, not merely the source/target items. Omit when the edge is inferred.
- Resolve input `unresolved_items` only when the current structures make the answer clear; otherwise carry them forward.
- Preserve uncertainty instead of inventing facts.
