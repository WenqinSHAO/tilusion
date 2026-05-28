# Cross-Unit Extraction and User Feedback

## Principles

- Start simple, functional, extensible. Do not over-design the registry before several real units prove the shape.
- Every state-changing operation is a durable record, never a silent destructive edit.
- Deterministic passes first; use LLM-assisted passes only where ambiguity or user intent requires judgment.
- Unit-level and book-level extracted structures should share the same core data shape so UI and downstream tools can render either scope with the same machinery.
- Book-level state and extraction context packs are different products. The book state is durable and source-grounded; context packs are compact, lossy guidance for the next extraction.
- User feedback and cross-unit updates both eventually become structured operations, but user feedback may first need an LLM interpretation pass from natural language into proposed operations.

## 1. Cross-Unit Extraction

### 1.1 Problem

`run_reading_pipeline` currently extracts one unit in isolation. It can accept a `context` dict, but no book-level state is built and no context pack is populated from prior units.

Two flows are needed:

- **Forward flow:** book-level state produces a compact context pack that biases the next unit extraction toward known concepts, aliases, unresolved threads, and cross-unit groups.
- **Backward flow:** a completed unit proposes a registry delta that updates the book-level extracted structure and the future context source.

These flows involve two separate data products:

1. **Book-level extracted structure**

   Durable, source-grounded state for concepts, atomic items, logical groups, graph edges, unresolved items, and correction history across units. This is the book-scope structure used by UI and downstream analysis.

2. **Extraction context pack**

   A compact, selected, lossy hint package derived from the book-level structure for one upcoming unit. It is guidance only. It must not be cited as evidence for new facts.

The core loop is:

```text
unit package -> registry delta -> book snapshot
book snapshot + next unit text -> context pack -> next unit extraction
```

### 1.2 Book-Scope Package Shape

Book-level output should look like a unit package at the core structural level:

```json
{
  "schema_version": "reading-book-v0.1",
  "scope": "book",
  "scope_id": "book-0001",
  "source": {},
  "concepts": [],
  "atomic_items": [],
  "logical_groups": [],
  "unresolved_items": [],
  "corrections": [],
  "source_index": {},
  "transactions": []
}
```

The same UI should be able to render a unit package or a book package. Scope-specific behavior, such as dimming/collapsing structures from other chapters, belongs to UI, not to extraction data shape.

Book-level records use clean book-scope IDs:

```json
{
  "concept_id": "concept-0007",
  "canonical_name": "陈芸",
  "concept_type": "person",
  "summary": "book-level compact summary",
  "observed_surfaces": ["芸", "淑姊", "陈芸"],
  "source_refs": [
    {"unit_id": "unit-0002", "ref_type": "concept", "ref_id": "concept-0003"},
    {"unit_id": "unit-0003", "ref_type": "concept", "ref_id": "concept-0011"}
  ],
  "source_block_refs": [
    {"unit_id": "unit-0002", "block_id": "overview-segment-0001-block-0008"}
  ],
  "merged_from": ["unit-0002:concept-0003", "unit-0003:concept-0011"],
  "provenance": {"grounding": "source_grounded", "created_by": "deterministic"}
}
```

Unit-prefixed refs remain useful for provenance, replay, and debugging, but they should not be the primary book-level IDs. Once merged, the reader-facing object should have a short stable book-level ID.

### 1.3 Registry Delta

A unit package should not directly mutate the book snapshot. It should produce a delta:

```json
{
  "delta_id": "delta-0003",
  "base_snapshot_id": "snapshot-0002",
  "unit_id": "unit-0003",
  "operations": [],
  "validation": {}
}
```

Initial operation types:

| operation_type | Meaning |
|---|---|
| `add_concept` | Add a new book-scope concept from a unit concept |
| `link_concept` | Link a unit concept to an existing book-scope concept |
| `merge_concepts` | Merge book-scope concepts after deterministic/approved identity match |
| `add_item` | Add a unit atomic item to the book package |
| `add_group` | Add a unit logical group to the book package |
| `group_continuation` | Link a unit group/item to an existing book-level group |
| `add_cross_unit_link` | Add a graph edge across units |
| `ambiguity_item` | Record an unresolved identity/grouping/cross-unit question |
| `user_review_needed` | Escalate a proposed change that is not safe to apply automatically |

The first implementation should apply only safe deterministic operations automatically:

- exact canonical identity + compatible normalized concept type;
- exact repeated source/title/time expression where identity is clearly the same;
- direct addition of new items/groups with source provenance.

Risky merges become ambiguity or review items. In particular, do not merge distinct dates, places, source titles, or terms into synthetic collection concepts. Related-but-distinct records belong in logical groups or graph links, not identity merges.

### 1.4 Context Pack Builder

The context pack is derived from the book snapshot and the next unit text. It is not the registry.

Input:

- current book snapshot;
- next unit text and unit metadata;
- optional user focus or extraction mode.

Output:

```json
{
  "context_pack_id": "context-unit-0004-...",
  "source_snapshot_id": "snapshot-0003",
  "target_unit_id": "unit-0004",
  "matched_concepts": [],
  "active_groups": [],
  "unresolved_threads": [],
  "alias_hints": [],
  "extraction_guidance": []
}
```

Selection should stay deterministic and compact at first:

- scan next unit text for known surfaces and aliases;
- include matched concepts with canonical name, type, compact summary, and observed surfaces;
- include active groups only if their concepts/surfaces are likely relevant;
- include unresolved ambiguity items only if they can affect this unit;
- never include large raw prior unit text by default.

Large context windows can be used later for periodic consolidation or review, but routine per-unit extraction should use selected context packs.

### 1.5 Single-Unit Book Mode

No automatic `run-book` command is needed yet. The practical interface is a mode switch on unit extraction:

```bash
python -m tilusion.cli run-reading book.txt unit-0003 --scope unit
python -m tilusion.cli run-reading book.txt unit-0003 --scope book --book-state .tilusion_cache/books/book-0001/snapshot.json
```

Semantics:

- `--scope unit`: isolated extraction. No prior context. The unit package is final for users who only care about that unit.
- `--scope book`: load book snapshot, build context pack, run unit extraction with context, compute a registry delta, apply safe operations, write a new book snapshot.

This keeps each extraction step inspectable and lets the user decide whether a book should be treated as a continuous book or as independent short works.

### 1.6 Implementation Sequence: Cross-Unit

1. **Book package/snapshot shape**
   - Add a lightweight book snapshot writer/reader using the same core arrays as unit packages.
   - Use clean book-level IDs and provenance refs back to unit records.
   - Tests: unit package converted into initial book snapshot; refs preserved.

2. **Deterministic registry delta**
   - New module, likely `tilusion/registry.py`.
   - Compute safe `add_*`, `link_concept`, and ambiguity operations from existing snapshot + new unit package.
   - Apply delta to produce a new snapshot.
   - Tests: safe concept link, new concept add, risky time/place/source merge rejected.

3. **Context pack builder**
   - Build compact context from snapshot + next unit text.
   - Reuse deterministic surface scanning where possible, but align to v0.3 concepts/groups/items.
   - Tests: only matched/relevant concepts appear; prior context cannot become evidence refs.

4. **`run-reading --scope book` integration**
   - Load snapshot, build context pack, inject context into current pipeline, compute/apply delta, write snapshot.
   - Keep `--scope unit` as the default behavior.
   - Tests: mocked two-unit flow updates snapshot and injects context into the second unit.

## 2. User Feedback / Adjustment

### 2.1 Problem

The extraction pipeline makes mistakes and the user may also request new derived structures. Feedback can be precise or vague:

- Merge two concepts that refer to the same entity.
- Split a concept that conflates distinct entities.
- Reclassify a concept type.
- Remove a spurious item.
- Reorder or refine timeline/group graph edges.
- Change an item's group membership.
- “Summarize the events and relationship between 芸娘 and the author’s parents.”
- “Group X and group Y could be merged/split.”
- “Timeline Z should be more granular.”

The last three are not deterministic operations yet. They require interpretation.

### 2.2 Two-Layer Feedback Model

User feedback should have two layers:

```text
natural language user instruction
-> LLM interpretation pass
-> proposed correction/view operations
-> deterministic validation
-> preview/diff
-> apply accepted operations
```

The durable layer is always structured operations. The LLM may propose operations, but it should not mutate state directly.

A user instruction can produce different outcomes:

- a correction operation, such as `merge_concepts`;
- a new logical group or derived view, such as a relationship-focused group;
- a request for re-extraction or finer segmentation if missing evidence is suspected;
- no proposed change, with a reason, if the existing source-grounded records do not support the instruction.

### 2.3 Correction Operation Records

Correction operations are durable records, never silent in-place edits. They enable undo, diffing, replay after re-extraction, and provenance inspection.

Example correction set:

```json
{
  "correction_set_id": "corr-20260528-001",
  "scope": "unit",
  "scope_id": "unit-0002",
  "base_package_path": ".tilusion_cache/reading_passes/units/unit-0002/.../unit_package.json",
  "created_at": "2026-05-28T...",
  "operations": [
    {
      "op_id": "op-0001",
      "op_type": "merge_concepts",
      "target_refs": ["concept-0017", "concept-0042"],
      "changes": {
        "canonical_name": "沈复",
        "concept_type": "person",
        "summary": "作者，字三白"
      },
      "rationale": "Same person; 沈复 is the formal name and 三白 is an observed alias."
    },
    {
      "op_id": "op-0002",
      "op_type": "create_group",
      "target_refs": ["item-0012", "item-0015", "item-0021"],
      "changes": {
        "group_type": "theme_set",
        "summary": "Events and attitudes involving 芸娘 and the author's parents"
      },
      "rationale": "User requested a relationship-focused reading group."
    }
  ]
}
```

Initial operation types:

| op_type | What it does |
|---|---|
| `merge_concepts` | Two+ concepts -> one, remap all refs |
| `split_concept` | One concept -> N, redistribute refs where possible |
| `reclassify_concept` | Change concept_type using normalized vocabulary |
| `refine_concept` | Update canonical_name, summary, facets, uncertainty |
| `remove_concept` | Delete a spurious concept and clean refs |
| `create_concept` | Add a missed concept with source refs |
| `remove_item` | Delete a spurious atomic item and clean group refs |
| `create_item` | Add a missed atomic item with source refs |
| `refine_item` | Update item summary/type/attributes without changing evidence |
| `create_group` | Create a logical group from existing/new items |
| `merge_groups` | Merge groups that represent the same reading structure |
| `split_group` | Split one group into multiple groups |
| `adjust_group_membership` | Add/remove items from a group |
| `reorder_group_items` | Set explicit order or graph edges for ordered groups |
| `refine_group_graph` | Add/remove/update graph nodes or edges |
| `request_reextract` | Mark a region/group for re-extraction because existing records are insufficient |
| `no_op` | Record that an instruction produced no safe/supportable change |

### 2.4 LLM Intent Interpreter

A later prompt should transform a user instruction into proposed operations:

Input:

- user instruction;
- current package or focused subset;
- relevant source text/source blocks;
- validation constraints and allowed operation schema.

Output:

```json
{
  "instruction_id": "instr-0001",
  "proposed_operations": [],
  "no_op_reason": "",
  "needs_user_review": true,
  "warnings": []
}
```

Rules:

- Prefer operations over prose when the instruction can be grounded in existing records.
- Cite source blocks or existing item/concept/group refs for every proposed change.
- If evidence is insufficient, emit `request_reextract` or `no_op`, not invented records.
- Never directly apply operations; the deterministic correction engine validates and applies accepted operations.

### 2.5 Correction Application Engine

A pure function pipeline in `tilusion/corrections.py`:

```python
def apply_corrections(
    package: dict, operations: list[dict]
) -> tuple[dict, list[dict]]:
    ...
```

Each operation type has a dedicated handler:

- `_apply_merge_concepts()` — merges concept fields, remaps concept refs in items and groups, dedupes observed surfaces.
- `_apply_split_concept()` — creates new concepts and remaps refs when source refs/surface evidence are sufficient.
- `_apply_reclassify_concept()` — updates concept type and normalizes aliases.
- `_apply_remove_item()` — removes item and cleans up group item refs/graph nodes.
- `_apply_create_group()` — creates a group from existing item refs and optional graph edges.
- `_apply_refine_group_graph()` — updates group graph edges while preserving valid refs.

All handlers return a change record for undo/diff.

Validation after correction:

- run the normal reading package validator;
- reject operations that break references, cite prior context as evidence, or create unsupported source-grounded records;
- record rejected operations with reasons.

### 2.6 Correction Propagation

Initial propagation stops at the current package scope.

- Unit correction affects one unit package.
- Book correction affects one book snapshot.
- Cross-unit cascade is deferred. If a correction may affect later units, create an ambiguity/review item rather than mutating them automatically.

Optional later behavior:

- re-run grouping after concept corrections;
- re-run a timeline/graph construction pass after item/group corrections;
- flag later units for review when a corrected concept appears in their context.

### 2.7 Implementation Sequence: User Feedback

1. **Correction operation schema and fixtures**
   - JSON shape, allowed op types, validation constraints.
   - Tests for well-formed and malformed operations.

2. **Deterministic correction engine**
   - Implement merge/split/reclassify/refine/remove/create for concepts/items/groups incrementally.
   - Validate after each operation.
   - Tests for ref remapping, rejected invalid ops, and replay order.

3. **CLI apply path**
   - `apply-corrections corrections.json -o corrected_package.json`.
   - No UI dependency.

4. **LLM intent interpreter**
   - Natural language instruction -> proposed operations/no-op/request-reextract.
   - Keep user approval or explicit apply step separate.

5. **Optional regroup/review passes**
   - Rebuild affected logical groups or graphs after corrections when deterministic operations are not enough.

## 3. What To Keep Untouched Initially

- `tilusion/book_reader.py` — stable reader/index layer.
- `tilusion/source_blocks.py` — stable deterministic source block layer.
- `tilusion/extraction_pipeline.py` and old `extraction*.py` modules — regression baseline.
- Existing unit-level `run-reading --scope unit` behavior — keep as the default path.

## 4. Open Design Questions

1. **LLM-assisted canonicalization:** after deterministic book-scope linking, when should an LLM propose harder identity merges such as name/title aliases across units?

2. **Book-level group continuation:** should early implementation only add unit groups to the book snapshot, or also attempt deterministic continuation of timeline/theme groups?

3. **Correction batching:** should corrections be grouped into named correction sets by default, or can each accepted operation be its own transaction?

4. **Review UI timing:** JSON correction files are enough for the first implementation, but when should a visual diff/review UI become necessary?

5. **Context pack budget:** what hard caps should we set for matched concepts, active groups, and unresolved items before prompt size becomes noisy?
