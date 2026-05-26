You finalize one source-grounded reading unit package from completed region-level passes.

The goal is a clean, inspectable unit package. Do not build final timelines or other derived views in this pass.

The caller provides JSON with:
- `unit_id` and source metadata.
- restored regions and source length information.
- source spans, source blocks, concept mentions, logical groups, and links from previous passes.
- deterministic validation reports and repair hints.
- optional context pack for alias/continuity guidance only.

Return only one JSON object. Do not include prose, markdown, or code fences.

Required top-level keys:
- `schema_version`
- `unit_id`
- `source`
- `source_spans`
- `source_blocks`
- `concept_mentions`
- `logical_groups`
- `links`
- `derived_views`
- `unresolved_items`
- `validation`
- `context_metadata`

Minimum shape:

```json
{
  "schema_version": "reading-unit-v0.1",
  "unit_id": "unit-0001",
  "source": {},
  "source_spans": [],
  "source_blocks": [],
  "concept_mentions": [],
  "logical_groups": [],
  "links": [],
  "derived_views": [],
  "unresolved_items": [],
  "validation": {},
  "context_metadata": {}
}
```

Rules:
- Preserve source grounding and provenance from prior passes.
- Stabilize unit-level IDs if needed, but do not break references.
- Deduplicate obvious local duplicates. If uncertain, keep both and add an unresolved item.
- Resolve local aliases only when supported by current-unit evidence or strong context guidance. Prior context is guidance, not evidence.
- Do not create `entity_records`, `location_records`, `atom_records`, `thread_records`, or top-level `timelines`.
- Keep timelines, discourse graphs, claim/evidence maps, theme maps, viewpoint evolution, and open-thread lists out of core records. They belong in later derived-view passes.
- If derived views are already included by caller, preserve them only under `derived_views` and ensure `is_source_of_truth` is false.
- Surface blocking validation issues in `unresolved_items`; do not silently drop them.
