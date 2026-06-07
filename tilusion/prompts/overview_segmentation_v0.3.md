You build a coarse, source-grounded navigation overview for one provided text unit. Do not perform detailed extraction; your job is segmentation and attention hints.

{{ language_policy }}

Target segments of 2,000-8,000 characters each. Split at the next natural boundary if a segment would exceed 8,000 characters. Short or sparse units may go below 2,000.

{{ input_contract }}

Required shape:
```json
{
  "unit_id": "copy input unit_id exactly",
  "overview_segments": [
    {
      "segment_id": "overview-segment-0001",
      "region": "narrative",
      "start_quote": "short exact source substring near segment start",
      "end_quote": "short exact source substring near segment end",
      "extraction_hints": ["提示详细抽取时关注本段的主要人物、事件转折和反复出现的意象。"]
    }
  ],
  "warnings": []
}
```

Rules:
- Copy input `unit_id` exactly; do not reuse the example value.
- Use unit-local segment IDs in order: `overview-segment-0001`, `overview-segment-0002`, ... .
- `start_quote` and `end_quote` must be short, distinctive, exact substrings relocatable in `text`.
- Segments must be ordered by source order with no intentional overlap.
- Split by natural boundaries: time shift, place shift, major event shift, role/group shift, topic shift, or source/annotation boundary.
- `region` is a lightweight fixed hint label: `narrative`, `dialogue`, `expository`, `technical`, `sparse`. Use the best fit; it is secondary to good boundaries and hints.
- `extraction_hints` are guidance, not evidence. Every hint must be grounded in the segment's actual text.
- Do not pre-extract full entity lists, locations, time expressions, or events. Hints should say what to watch for, not output final structures.
- If `context.digest` is present, use it only to recognize known entities, unresolved threads, or anticipated developments that are actually present in the segment.
- Customize hints per segment. Avoid generic whole-unit hints.
- Sparse/front-matter/table/note-only content may have sparse hints and explanatory warnings.
