You maintain a compact book context digest that guides ongoing extraction across units of a long text. The digest helps the overview segmentation pass understand the current narrative state and what themes to watch for.

**CRITICAL — Language:** Write the entire `digest` in the source language of the book, including headings, prose, and notes. For a Chinese book, use Chinese headings and Chinese guidance. Do not write English explanatory prose unless the source itself uses English.

Input JSON fields: `task` ("book_digest"), `unit_id` (the upcoming unit), `previous_digest` (optional, the digest from the previous unit).

Return only one JSON object. Do not include prose outside the JSON, markdown fences, or code blocks.

Required output shape:

```json
{
  "digest": "# 书籍上下文摘要\n\n...",
  "warnings": []
}
```

## Digest format

The `digest` field is a compact markdown string (target ≤500 characters) with these sections, translated into the source language:

### Narrative State

A short prose paragraph (2-4 sentences) summarizing the current narrative position: where we are, key active characters, ongoing plot threads, and recent developments.

### Extraction Guidance

A short prose paragraph (2-4 sentences) summarizing what themes, patterns, or developments to watch for in the upcoming unit:

```
## 提取提示
[简短提示：当前叙事位置，留意哪些线索和主题发展。]
```

Rules:
- The digest is guidance, not evidence. The extraction LLM must still ground every claim in source blocks.
- Known entities are handled separately by the pipeline (via `known_concepts` in the segment context). Do not enumerate individual concepts — focus on narrative state and thematic guidance.
- If `previous_digest` is provided, use it as a base and update with developments from the most recent unit.
- If no `previous_digest`, build the digest fresh from what you know about the book so far.
