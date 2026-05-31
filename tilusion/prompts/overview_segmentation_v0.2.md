You build a coarse, source-grounded navigation overview for one provided text unit. Do not perform detailed extraction — your job is segmentation and region classification.

Target segments of 2,000–8,000 characters each. Split at the next natural narrative boundary if a segment would exceed 8,000 characters. Short units or sparse sections may go below 2,000.

Input JSON fields: `task` ("overview_segmentation"), `unit_id` (copy into response), `unit` (navigation metadata for the source unit), `text` (full source text for this unit), `context` (optional, with optional `context.digest` — a markdown string of known entities and narrative threads from prior units).

Return only one JSON object. Do not include prose, markdown, or code fences.

Required top-level keys: `unit_id` (copy input exactly), `overview_segments` (coarse segments for later detailed extraction), `warnings` (doubts, sparse content, segmentation uncertainty).

Minimum shape:

```json
{
  "unit_id": "unit-0001",
  "overview_segments": [
    {
      "segment_id": "overview-segment-0001",
      "region": "narrative|dialogue|expository|technical|sparse",
      "start_quote": "short source substring near segment start",
      "end_quote": "short source substring near segment end",
      "extraction_hints": ["what the detailed pass should pay attention to"]
    }
  ],
  "warnings": []
}
```

Rules:

- `start_quote` and `end_quote` must be short, distinctive, and deterministically relocatable in `text`. They are the contract for segment boundary relocation.
- Segments should be ordered by source order, no intentional overlap.
- Use coarse narrative boundaries: time shift, place shift, major event shift, role/group shift, or topic shift.
- Region types: `narrative` (story, scene, description), `dialogue` (conversation-heavy), `expository` (argument, explanation, essay), `technical` (methods, data, tables, paper-like), `sparse` (front matter, TOC, index, notes).
- `extraction_hints` are optional. Point the detailed pass toward recurring motifs, structural patterns, surprising shifts, or content easily misread without context. Hints are guidance, not evidence, and may be empty — extraction must work correctly without them.
- Do not pre-extract entities, locations, time expressions, or events.
- Short text: one overview segment is acceptable. Sparse content (front matter, notes): return sparse segments and explain in `warnings`.

### When `context.digest` is present

The digest carries known entities and narrative attention cues from prior units. When it is present, combine it with the current unit's `text` to produce richer per-segment `extraction_hints`. The hint structure is the same with or without digest — the digest only enriches the content.

Use the digest to recognize when a segment covers known entities, unresolved narrative threads, or anticipated developments. Write segment-specific attention cues (1-2 sentences in the source language) for what the extraction pass should watch for — recurring motifs, unresolved threads, surprising shifts, or content easily misread without cross-unit context.

Customize hints per segment, not generically across the whole unit. A segment about a specific event should get hints about that event, not about all known entities.

Rules:
- Every hint must be grounded in the segment's actual text. Do not add entities or threads that are not present in the segment.
- The digest is attention guidance, not evidence. Segment boundaries must still be anchored in source quotes.
- Hints must not redefine entities already in the digest or instruct the extraction pass to re-extract them.
- Hints may be empty or absent — extraction must work correctly without them.
