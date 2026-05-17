You are repairing a completed timeline construction pass. The previous round produced one or more partially-ordered timelines. Some issues remain unresolved.

Your job:
Fix the specific issues listed in `repair_targets` while preserving all valid timelines unchanged.

Do NOT redo the entire timeline construction. Only fix what is listed.

Focus on:
- Adding events that are missing from all timelines' ordered_events (listed in `repair_targets.missing_events`)
- Fixing self-loops where an event lists itself in before_events
- Fixing phantom references in before_events that point to non-existent event IDs
- Fixing cycle issues in the DAG (remove the weakest-supported edge to break each cycle)
- Adding rationale to ordered_events entries that have before_events edges but no rationale

For each issue in `repair_targets.validation_issues`, either:
- Apply the targeted fix and note it in `quality_notes.summary`
- If repair is impossible with available information, keep the issue in `unresolved_items` and explain why in `quality_notes.blocking_concerns`

Preserve:
- All valid timeline entries and their ordering edges
- All existing unit-scope IDs (unit-event-N, unit-timeline-N)
- The existing quality_notes.summary, updated to reflect any repairs made

Return only one JSON object with exactly these keys:
- `timelines`: the repaired timelines array
- `quality_notes`: updated quality notes dict
- `unresolved_items`: list of unresolved issues (omit if empty)
- `warnings`: list of repair warnings (omit if empty)

Do NOT include entity_records, location_records, event_records, or thread_records. The pipeline has those already.

Return only one JSON object. Do not include prose, markdown, or code fences.
