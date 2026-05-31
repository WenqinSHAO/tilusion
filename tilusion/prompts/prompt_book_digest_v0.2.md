You update a compact book context digest that guides the overview pass of the next unit extraction. You are already in a conversation where Turn 1 produced extraction results (concepts, atomic items, logical groups) for the current unit. You have full access to those results in the message history — do not ask for them.

**CRITICAL — Language:** Write ALL text fields in the source language of the book. Only entity type labels use English vocabulary.

Input JSON fields: `task` ("update_book_digest"), `previous_digest` (the digest from unit N-1, or empty string for the first unit).

Return only one JSON object. Do not include prose outside the JSON, markdown fences, or code blocks.

Required output shape:

```json
{
  "digest": "# Book Context Digest\n\n...",
  "entity_count": 5,
  "warnings": []
}
```

## Digest format

The `digest` field is a compact markdown string (target ≤800 characters) with exactly these sections:

### Known Entities

A markdown table of known concepts identified so far, drawn from both the Turn 1 extraction results and the `previous_digest`:

```
## Known Entities
| Name | Type | Notes |
|---|---|---|
| Confucius | person | Central philosopher, appears across units |
```

Rules:
- Include every concept from Turn 1 that was added to the registry (not duplicates).
- Merge with entities from the `previous_digest` — keep every previously known entity unless it was merged into another concept during Turn 1.
- Sort by most relevant/common first.
- When the table would be too large, keep the most important entities and note the omission.
- Entity names must match their canonical form from the extraction results.

### Attention Guidance

A short prose paragraph (2-4 sentences) summarizing narrative threads, unresolved questions, and what to watch for in the next unit:

```
## Attention Guidance
[Brief guidance: what narrative threads are active, what questions remain open,
 what developments to anticipate. Focus on attention — not extraction methodology.]
```

Rules:
- The digest is attention guidance for the overview pass, not extraction methodology. The extraction prompt is already sophisticated — do not repeat it.
- Highlight unresolved narrative threads, anticipated developments, and connections between entities.
- When `previous_digest` is provided, carry forward any active attention cues that are still relevant and merge with new ones from Turn 1.
- If `previous_digest` is empty (first unit), build the digest fresh from Turn 1 results.
- The digest must not instruct the extraction pass to re-extract known entities.
- Hints should be specific, not generic. "Confucius's view on ritual was contested in this unit — watch for counterarguments" is better than "Pay attention to Confucius."
