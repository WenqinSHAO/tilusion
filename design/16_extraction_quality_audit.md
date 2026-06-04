# Extraction Quality Audit: Units 2–4, 浮生六记

Status: **audit complete** — findings feed into Phase 2+3 prioritization.

Based on `registry.json` (book-f7f51ac962a6dc02) after sequential extraction
of unit-0002 through unit-0004. Registry contains 722 concepts, 420 items,
26 groups.

---

## Summary of Problems

| # | Problem | Severity | Count | Phase to address |
|---|---------|----------|-------|-----------------|
| 1 | Systematic language non-compliance | High | 79 concepts, 15 groups | Prompt fix |
| 2 | English-only surfaces for Chinese entities | High | 19 concepts | Prompt fix |
| 3 | Group type proliferation & inconsistency | Medium | 7 types for 26 groups | Phase 2 |
| 4 | Temporal sequence fragmentation | Medium | 6 groups for 1 narrative arc | Group resolution |
| 5 | Misclassified group types | Medium | 3+ groups | Group resolution |
| 6 | Unmerged semantically identical groups | Medium | 1 pair | Group resolution |
| 7 | Non-standard concept types | Low | 25 concepts | Prompt fix |
| 8 | No facets populated | Info | 0/722 concepts | Phase 2 |
| 9 | Missing canonical names | Medium | 282/722 (39%) | Phase 2 |
| 10 | Provenance tag inconsistency | Low | 10 concepts with [?] | Pipeline fix |

---

## 1. Systematic Language Non-Compliance (unit-0003)

The extraction prompt requires source-language output (Chinese for a Chinese
book). Unit-0003 systematically violated this.

### 1a. English concept summaries

79 of 722 concepts (~11%) have English summaries. The English concepts are
concentrated in unit-0003's extraction range (concept-0281 through
concept-0540+). Examples:

| Concept ID | Surface | Type | Summary (English) |
|------------|---------|------|-------------------|
| concept-0281 | 篱东菊绽 | term | Chrysanthemums blooming by the eastern fence, a seasonal scene. |
| concept-0283 | 插瓶 | method | The method of arranging flowers in vases. |
| concept-0285 | 起把宜紧 | term | A principle: the stems emerging from the mouth of the vase should be compact... |
| concept-0482 | 荷花 | object | Lotus flower, used for tea scenting. |
| concept-0497 | 鲍姓者 | person | A person surnamed Bao who sold wontons |
| concept-0509 | 就事论事 | term | a method of frugality meaning to deal with matters as they arise... |

The Chinese surfaces are correct — the extractor *read* the source correctly —
but wrote summaries in English instead of Chinese. This is a prompt-adherence
issue, not a comprehension issue.

### 1b. English group summaries

15 of 26 groups have English summaries, all from unit-0003's grouping pass:

| Group ID | Type | Summary |
|----------|------|---------|
| group-0003 | timeline | Childhood memories and anecdotes in chronological order |
| group-0004 | timeline | Adult flower and plant cultivation events |
| group-0005 | method_example_set | Flower arrangement methods: vase type, cutting, techniques, and principles |
| group-0006 | theme_set | Garden design and space optimization principles |
| group-0007 | method_example_set | Creative plantings: water lily, vegetable heart, stone, lotus, etc. |
| group-0008 | timeline | Making a miniature rockery and its destruction |
| group-0009 | theme_set | Incense, fruit offerings, and insect-in-flower method |
| group-0010 | timeline | Life at Xiaoshuanglou: friends, gatherings, painting, and games |
| group-0011 | timeline | Outing to South Garden for flower viewing |
| group-0012 | method_example_set | Frugal living methods and examples by Yun |
| group-0013 | theme_set | The author's life philosophy and reflections on living well |
| group-0014 | discourse_graph | Tea scenting with lotus flowers |
| group-0015 | method_example_set | Potted tree pruning methods and critiques |
| group-0016 | theme_set | Footnotes and commentary items |
| group-0017 | theme_set | Active flower screen and its benefits |

Units 0002 and 0004 produced Chinese group summaries. Unit-0003 alone produced
15 English groups.

### Root cause

The extraction prompt (`prompt_per_segment_extraction_v0.2.md`) says:
> Write ALL text fields in the source language (Chinese→Chinese, English→English).

The grouping prompt likely has the same instruction. The non-compliance is
either:
- The DeepSeek model ignoring the language constraint on certain segments.
- The instruction being too easy to miss in a long system prompt.
- A temperature/decoding issue causing language switches mid-output.

**Recommended fix**: Move the language instruction closer to the field output
instructions. Add an explicit "DO NOT translate" warning. Consider adding a
post-extraction language validator that flags English content in Chinese
source pipelines and triggers re-extraction.

---

## 2. English-Only Surfaces for Chinese Entities

19 concepts have English surfaces (no Chinese surface at all), all from
unit-0004:

| Concept ID | Surface | Type | Should be |
|------------|---------|------|-----------|
| concept-0081 | congee | object | 粥 |
| concept-0216 | old maid | person | 老妇 |
| concept-0616 | half-shoulder luggage | object | 半肩行李 |
| concept-0626 | box and belongings | object | 箱笼 |
| concept-0630 | previous summer rental | event | 前夏租屋 |
| concept-0636 | back door | object | 后门 |
| concept-0637 | alley | place | 巷 |
| concept-0638 | boat dock | place | 船埠 |
| concept-0639 | patrol officer | person | 巡丁 |
| concept-0640 | sick woman | social_role | 病妇 |
| concept-0641 | son-in-law | social_role | 女婿 |
| concept-0642 | boatman | person | 船夫 |
| concept-0643 | Hua workers | group | 华家工人 |
| concept-0644 | boat | object | 船 |
| concept-0645 | untie mooring rope | action | 解缆 |
| concept-0646 | loud weeping | emotion | 号哭 |
| concept-0648 | footnote marker | other | 注脚 |
| concept-0651 | heartbroken | emotion | 心碎 |
| concept-0652 | wiping tears | emotion | 拭泪 |

These are translations of Chinese source text into English surfaces. The
extractor translated rather than transcribed. Counter-example: concept-0081
also has Chinese in its summary (芸为沈复藏的粥和小菜) but the surface
itself is "congee".

**Root cause**: The extractor sometimes takes the concept's *meaning* as the
surface rather than the *lexical form* from the source text. For common
objects and roles, it defaults to English translation.

**Recommended fix**: Add explicit instruction: "surface MUST be a verbatim
quote or close paraphrase IN THE SOURCE LANGUAGE from the source text. Never
translate the surface."

---

## 3. Group Type Proliferation & Inconsistency

26 groups use 7 distinct types:

| Group Type | Count | Notes |
|------------|-------|-------|
| temporal_sequence | 7 | Same concept as "timeline", but Chinese-named units use this |
| theme_set | 7 | |
| timeline | 5 | Same concept as "temporal_sequence", English-named units use this |
| method_example_set | 4 | |
| claim_evidence_map | 1 | Non-standard, appears once |
| discourse_graph | 1 | Non-standard, appears once |
| other | 1 | group-0026, should be theme_set |

### 3a. "timeline" vs "temporal_sequence" — same concept, two names

The "timeline" label appears on 5 groups from unit-0003 (English output).
The "temporal_sequence" label appears on 7 groups from units 0002/0004
(Chinese output). They are semantically the same structure: chronologically
ordered event chains. The naming difference is another manifestation of the
language split, not a structural distinction.

### 3b. Non-standard group types

"claim_evidence_map" (group-0002) and "discourse_graph" (group-0014) are not
in the standard group type vocabulary. They appear once each, suggesting the
extractor invented types rather than choosing from the provided vocabulary.

**Recommended fix**: Phase 2's prompt tightening should include an explicit
closed set of group types. The group resolution pass should reclassify
non-standard types.

---

## 4. Temporal Sequence Fragmentation

Groups 0018-0023 form a continuous narrative arc — Yun's decline, death,
and its aftermath — but are sliced into 6 separate temporal_sequences:

| Group | Items | Summary |
|-------|-------|---------|
| group-0018 | 9 | 家庭冲突与驱逐：从代笔、纳妾、借贷到被逐出家门 |
| group-0019 | 5 | 憨园事件与芸病加重 |
| group-0020 | 20 | 西人索债与夜遁 |
| group-0021 | 14 | 寄居华家与靖江求助 |
| group-0022 | 20 | 邗江再聚与芸病逝 |
| group-0023 | 41 | 芸逝世后：葬仪、回煞与沈复漂泊 |

Total: 109 items. These are extracted from sequential segments within
unit-0004. The extractor created a new group per segment/section rather than
recognizing them as sub-sequences of one larger narrative arc.

By contrast, group-0001 ("沈复与芸的爱情与婚姻生活的时间线") holds 140
items spanning the entire book as a single temporal sequence — also from
unit-0002.

**The inconsistency**: unit-0002 produced one large timeline (140 items).
Unit-0004 produced 6 small ones (5-41 items each) for the same book. The
extractor's grouping behavior varies by unit.

**User's feedback**: group-0021 ("寄居华家与靖江求助") is clearly part of
the bigger timeline of Yun's decline. group-0008 ("Making a miniature rockery
and its destruction", 7 items) is a micro-episode that shouldn't be a
timeline at all — it's a narrative event.

**Recommended fix**: The group resolution pass should handle merging of
adjacent temporal sequences. The grouping prompt should emphasize that
temporal sequences can span the entire unit, not just a single segment.

---

## 5. Misclassified Group Types

Several groups are labeled "timeline" but don't contain chronological event
chains:

| Group | Label | Actual content |
|-------|-------|---------------|
| group-0004 | timeline | Adult flower and plant cultivation events — really a collection of horticultural descriptions, not a timeline |
| group-0008 | timeline | Making a miniature rockery and its destruction — a single narrative episode, not a timeline |
| group-0011 | timeline | Outing to South Garden for flower viewing — a single event/outing, not a timeline |

group-0004 and group-0005 overlap in subject (both about flowers) but have
different types (timeline vs method_example_set). They could be restructured
as one method_example_set or merged.

**Root cause**: The extractor over-uses "timeline" as a catch-all for any
sequence of related items, even when the items aren't temporally ordered.

---

## 6. Unmerged Semantically Identical Groups

group-0016 (theme_set, English: "Footnotes and commentary items", 4 items)
and group-0026 (other, Chinese: "注释类（脚注）", 2 items) describe the
same thing: footnote/commentary content in the source. They should be one
group.

- group-0016 has concept_refs: ['concept-0335', 'concept-0336', 'concept-0334', 'concept-0337']
- group-0026 has concept_refs: [] (empty)

The empty concept_refs on group-0026 means it was never linked to concepts
from prior units. The group resolution pass (Conversation E) should have
detected the semantic overlap but didn't — likely because one has an English
summary and the other Chinese.

**This is the user's specific complaint about group-0016 vs group-0026.**

---

## 7. Non-Standard Concept Types

25 concepts use types outside the standard vocabulary:

| Type | Count | Standard equivalent |
|------|-------|-------------------|
| event | 8 | Should likely be `other` or a new atomic item with concept refs |
| statement | 6 | Should be `other` or restructured as an item |
| action | 7 | Should be `method` or `other` |
| activity | 4 | Should be `method` or `other` |

These types are not in the prompt's allowed type list. The extractor
invented them.

---

## 8. No Facets Populated

0 of 722 concepts have a non-empty `facets` field. The field exists in the
schema (it was added during v0.2 prompt design) but the extractor never
populates it. This confirms Phase 2's plan to add facet generation to the
extraction prompt is necessary — the schema change alone is not sufficient.

---

## 9. Missing Canonical Names

282 of 722 concepts (39%) have no canonical_name. Without a canonical name:

- Deterministic merge can only use surface collision and aliases.
- Cross-unit identity resolution has one fewer signal.
- The `link` proposal requirement to set canonical_name on the registry
  concept is harder to satisfy.

**Example**: concept-0081 has surface="congee", canonical_name="congee"
(both English), but should have canonical_name="粥" or similar Chinese name.

---

## 10. Provenance Tag Inconsistency

10 concepts have `[?]:` provenance tags (unresolved source unit). Only 58
of 722 concepts have any provenance tag at all. The remaining 664 concepts
have summaries without `[unit-N]:` prefixes — their origin is untraceable.

This happens because `merge_concepts` concatenates summaries but provenance
tags from merged-in concepts may be lost or never emitted by the extractor.

---

## Recommended Priority

1. **Fix prompt language compliance** (Problems 1, 2): Add stronger
   language guards to extraction prompts. This is the single biggest
   quality issue and costs nothing in compute.

2. **Phase 2 — facet generation + canonical name coverage** (Problems 7, 8, 9):
   Add facet extraction to the prompt and track canonical_name coverage as
   a metric. These two signals directly improve deterministic merge quality.

3. **Group resolution improvements** (Problems 3, 4, 5, 6): Tighten group
   type vocabulary, merge semantically identical groups across units, and
   allow the group resolution pass to merge adjacent temporal sequences.

4. **Post-extraction validation** (Problem 10): Add a language validator
   that checks whether a Chinese-source pipeline produced English text
   fields, and trigger re-extraction when detected.
