# Registry Merge Contract: Identity, Facets, and Soft Typing

Status: **Part 3 working contract**. This document records the current merge
semantics and the roadmap for improving cross-unit concept consistency. It is
not a frozen spec; it should be revised after each LLM-backed quality run.

## Why This Exists

Cross-unit extraction succeeds only if the book registry can link repeated
entities without corrupting distinct ones. Recent runs showed both sides of the
problem:

- under-merge: `沈复`, `沈三白`, `余`, `芸`, and related timelines can remain split;
- over-merge: distinct people or places can be collapsed when the merge signal is
  too weak;
- prompt/code mismatch: the LLM may produce surfaces, types, aliases, and facets
  that the merge code interprets differently than intended.

Part 3 treats merge behavior as a first-class quality target. Facets and soft
typing belong here: they are not only schema decoration, but part of the
deterministic and LLM-assisted identity contract.

## Current Contract

### Identity Evidence

A concept merge needs an identity signal before type flexibility is considered.
Current accepted identity signals are:

- same exact source surface;
- shared canonical name across members;
- overlap among usable source forms: surface, aliases, observed surfaces.

Generic forms are not usable identity evidence by themselves. Examples include
`余`, `吾`, `我`, `予`, `作者`, `叙述者`, `先生`, `夫人`, `妻`, `妻子`, `夫`, `丈夫`,
`友人`, `主人`, and their English role equivalents. These forms may remain as
aliases, but they do not justify deterministic merge alone.

### Hard Boundary Types

Some types should stay strict because a false merge is especially destructive:

- `place`: distinct places with different surfaces should not merge;
- `time_anchor`: distinct dates or time expressions should not merge;
- `source`: distinct works/titles/sources should not merge unless identity is
  explicit and source-grounded.

Related but distinct boundary concepts should be connected by groups or graph
edges, not identity merges.

### Soft Typing

Soft type compatibility is identity-gated:

1. Establish identity evidence first.
2. If normalized types match, the merge can proceed.
3. If normalized types differ, facet overlap can permit the merge.
4. Without identity evidence, facet overlap is only topical similarity and must
   not create a merge.

Current code uses facet intersection as the soft-type bridge after identity is
established. This is intentionally conservative compared with semantic search:
semantic similarity selects candidates; it does not authorize identity.

### Facet Semantics

Facets are normalized internal tags for merge/search support, not reader prose.
They should help answer: "what kind of thing is this, and in what role/domain is
it operating?"

Useful facet classes:

- class facets: `person`, `place`, `time_anchor`, `object`, `method`, `term`,
  `source`, `theme`;
- domain facets: `family_life`, `flower_arrangement`, `travel`, `illness`,
  `literary_commentary`;
- role facets: `narrator`, `spouse`, `father`, `friend`, `teacher`, `artist`;
- continuity facets: `recurring_entity`, `local_detail`, `unit_theme`;
- method/argument facets: `procedure`, `aesthetic_principle`, `claim`,
  `evidence`, `example`.

Facets should support an already plausible identity. They should not be treated
as entity names. Generic facets such as only `person`, `theme`, or `object` are
weak evidence; richer overlaps such as `person + spouse + recurring_entity` or
`method + flower_arrangement + procedure` are more meaningful.

## Current Implementation State

Already done:

- output fields are part of prompt contracts, so language/type policy applies to
  LLM outputs;
- extraction prompt asks for source-grounded identity fields, reader-facing prose,
  normalized internal facets, and separated concept/item type vocabularies;
- cross-category concept/item type warnings can be auto-fixed deterministically;
- repair propagation copies fixed validation-subject fields back to returned LLM
  data;
- registry merge rejects missing identity signals before soft typing;
- generic alias-only identity signals are ignored by deterministic registry dedup
  and merge-boundary surface overlap;
- deterministic registry dedup logs skipped merge attempts.

Known gaps:

- facet overlap is currently binary and does not distinguish generic from
  meaningful overlap;
- accepted/rejected merge reasons are not yet summarized in a structured metric;
- soft-type merges are not yet logged by type pair and facet intersection;
- LLM merge proposals and deterministic dedup are still harder to audit than
  extraction/grouping metrics;
- timeline/group continuation quality depends on resolved concept identity but
  has separate grouping logic that needs its own contract later.

## Examples

### Safe Merge Candidates

- `沈复` + `沈三白`: same person when source forms/canonical names/aliases support
  the identity. Generic alias `余` alone is insufficient, but it can supplement
  stronger evidence.
- `芸` + `陈芸` + `芸娘`: same recurring person when source forms and role facets
  support the identity.
- `插花之法` + `剪枝养节之法`: merge only if the source and context show the same
  method; otherwise group as related methods.

### Unsafe Merge Candidates

- `苏州` + `扬州`: both places, but distinct surfaces and no identity signal.
- `七月` + `中秋`: both time anchors, but distinct temporal references.
- two different people both carrying alias `先生`: generic alias-only signal.
- two themes sharing only `theme` or `family_life`: topical overlap, not identity.

## Part 3 Roadmap for Merge Quality

### 1. Add Merge Observability First

Before changing more heuristics, log and persist summary data:

- accepted merges by reason: same surface, shared canonical name, usable alias,
  soft-type facet bridge, LLM proposal;
- rejected merges by reason: no identity signal, hard boundary type, type/facet
  mismatch, generic alias only;
- soft-type merge type pairs and intersecting facets;
- deterministic dedup candidates found, accepted, rejected/skipped;
- concepts left split despite same/similar canonical forms.

This should be visible in CLI logs and preferably in `runs.json`/run manifests
so extraction-quality analysis can compare runs.

### 2. Make Facet Overlap More Meaningful

Move from binary facet intersection to weighted or classified overlap:

- ignore class-only overlaps (`person`, `theme`, `object`) for soft-type bridges;
- prefer role/domain/continuity overlaps;
- treat hard boundary types separately regardless of facet overlap;
- log the exact facets that permitted a soft-type merge.

### 3. Tighten Candidate Selection With the Contract

Semantic search should find possible candidates, but the contract should decide
what evidence is required before merge. Candidate selection can surface:

- exact source/canonical matches;
- usable alias matches;
- generic alias suppressed matches;
- semantic-only matches that require LLM judgment;
- facet/domain similarity without identity evidence.

### 4. Align Prompts After Code Contract Changes

Prompt changes should follow the merge contract, not precede it. Once facet
classification and merge metrics are explicit, update prompts to ask for the
facet shapes the code actually consumes.

### 5. Re-run and Audit

After each batch:

1. run units already used for comparison (`unit-0002` through current extracted
   unit);
2. inspect merge logs and registry visualization;
3. update the relevant quality catalog;
4. adjust the contract if the run exposes a better rule.

## Non-Goals for This Contract

- It does not decide group/timeline continuation rules, except that those rules
  depend on resolved concept identity.
- It does not make facets reader-facing UI text.
- It does not replace LLM judgment for genuinely ambiguous identity cases.
- It does not authorize semantic-similarity-only merges.
