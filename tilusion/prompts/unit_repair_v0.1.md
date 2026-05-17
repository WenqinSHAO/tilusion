You are repairing a completed unit finalization. The previous round produced unit-level entity, location, event, and thread records. Some issues remain unresolved.

Your job:
Fix the specific issues listed in `repair_targets` while preserving all valid records unchanged.

Do NOT redo the entire finalization. Only fix what is listed.

Focus on:
- Replacing missing or unresolvable evidence quotes with valid source substrings when the source text is available in the segment results
- Adding evidence_span_ids to objects that are missing them
- Fixing malformed evidence references that contain prose instead of valid evidence IDs
- Removing or flagging objects whose evidence cannot be repaired
- Resolving ambiguous short evidence quotes by suggesting longer or more specific alternatives

For each unresolved item in `repair_targets.unresolved_items`, either:
- Apply the suggested repair and note it in the object's qc_notes
- If repair is impossible with available information, keep the item in `unresolved_items` and explain why in `quality_notes.blocking_concerns`

Preserve:
- All valid entity_records, location_records, event_records, and thread_records
- All mention_refs, time_refs, and evidence_refs provenance
- The existing unit-scope IDs (unit-entity-N, unit-location-N, unit-event-N, unit-thread-N)
- The existing quality_notes.summary, updated to reflect any repairs made

Return the complete corrected unit extraction JSON with the same schema as the original finalization.

Return only one JSON object. Do not include prose, markdown, or code fences.
