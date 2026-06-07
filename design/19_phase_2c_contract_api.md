# Phase 2c: Prompt/Data-Model Contract — API Design

Status: **design** — concrete Python API for the contract refactor before
Phase 3 (repair/retry).

## Problem restated

Five v0.3 prompt files hand-maintain the same metadata in prose:
- Field-language policy (same role triage in 5 files)
- Type vocabularies (concept/item/group/edge, same lists in 5 files)
- Input field descriptions (same `unit_id`, `segment`, `source_blocks` etc.)
- Output schema examples (same shape with different field subsets)

A type change (e.g., drop `dataset` for novels) requires editing 5 prompt
files + `reading_schema.py` + tests. This is the drift surface Phase 2c
should eliminate.

## Design constraint

Keep it small. No framework, no plugin system, no YAML config files. One
new module (`tilusion/prompt_contracts.py`, est. ~150 lines) that owns the
metadata and renders prompt sections. Existing `reading_schema.py` type
constants remain the source of truth; the contract module reads them.

## API

### 1. Field role enum and metadata

```python
from enum import Enum

class FieldRole(Enum):
    SOURCE_IDENTITY = "source_identity"    # surface, canonical_name, observed_surfaces
    READER_PROSE = "reader_prose"          # summary, rationale, warnings
    NORMALIZED_INTERNAL = "normalized_internal"  # concept_type, facets, IDs

@dataclass(slots=True)
class FieldMeta:
    name: str                              # JSON field name
    role: FieldRole
    description: str                       # one-line, in English (renderer translates)
    required: bool = True
    example: str | None = None             # realistic populated example value
```

Design choice: descriptions are in English; the renderer produces target-language
prompt text. This is simpler than maintaining per-language field descriptions.

### 2. Pass contract

```python
@dataclass(slots=True)
class PassContract:
    pass_name: str                         # "per_segment_extraction", "unit_grouping", etc.
    input_fields: list[FieldMeta]
    output_fields: list[FieldMeta]
    type_vocabularies: dict[str, TypeVocabulary]  # keyed by category name

    def render_language_policy(self) -> str:
        """Render the shared field-language policy section."""
        ...

    def render_input_contract(self) -> str:
        """Render input field descriptions."""
        ...

    def render_output_schema(self) -> str:
        """Render output JSON schema example with populated fields."""
        ...

    def render_type_vocabulary(self, category: str) -> str:
        """Render allowed types for one category."""
        ...
```

Each pass gets one `PassContract` instance. The render methods produce the
markdown sections that currently live as hand-written prose in each prompt
file. The static prompt file shrinks to only the pass-specific content:
binding rules, region guidance, multi-round protocol, proposal rules.

### 3. Type vocabulary

```python
@dataclass(slots=True)
class TypeVocabulary:
    category: str                          # "concept", "item", "group", "edge"
    all_types: frozenset[str]              # all recognized types
    preferred: frozenset[str]              # subset to present first
    definitions: dict[str, str]            # type -> one-line distinguishing criteria
    escape_hatch: str = "other"
    allow_custom: bool = False

    def render_compact(self) -> str:
        """Render as two-tier prompt list: preferred first, then extended."""
        ...

    def render_allowed_set(self) -> str:
        """Render as flat `type1|type2|...` string for schema constraints."""
        ...
```

The `preferred` subset is the key mechanism for domain narrowing.
`definitions` provides the distinguishing criteria currently missing from
prompts (prompt says "timeline" but doesn't define it).

### 4. Type registries for document domains

```python
# Pre-built registries for common document types

NARRATIVE_CONCEPT_TYPES = TypeVocabulary(
    category="concept",
    all_types=RECOMMENDED_CONCEPT_TYPES,
    preferred=frozenset({
        "person", "place", "time_anchor", "object", "term",
        "theme", "motif", "emotion", "social_role", "symbol",
        "source", "other",
    }),
    definitions={
        "person": "named or referenced individual",
        "place": "named or described location",
        "time_anchor": "explicit date, season, or relative time expression",
        "object": "physical thing with narrative significance",
        "term": "specialized word or concept used in the text",
        "theme": "recurring abstract idea or motif-variant",
        "motif": "concrete recurring image, object, or pattern",
        "emotion": "named emotional state or expression",
        "social_role": "relational role (spouse, servant, official)",
        "symbol": "object/place/person with explicit symbolic meaning",
        "source": "cited or referenced text, letter, document, work",
    },
)

NARRATIVE_ITEM_TYPES = TypeVocabulary(
    category="item",
    all_types=RECOMMENDED_ITEM_TYPES,
    preferred=frozenset({
        "event", "action", "statement", "observation",
        "description", "habit", "question", "note", "other",
    }),
    definitions={
        "event": "discrete happening with temporal extent",
        "action": "single agentive act",
        "statement": "something said, written, or asserted by a character",
        "observation": "narrator/character noticing or perceiving something",
        "description": "static depiction of scene, person, or object",
        "habit": "recurring behavior or custom",
        "question": "explicit or implicit question raised",
        "note": "editorial, footnote, or commentary aside",
    },
)

NARRATIVE_GROUP_TYPES = TypeVocabulary(
    category="group",
    all_types=RECOMMENDED_GROUP_TYPES,
    preferred=frozenset({
        "timeline", "temporal_sequence", "theme_set",
        "method_example_set", "contrast_set", "other",
    }),
    definitions={
        "timeline": "coarse unit/book-level arc of major happenings",
        "temporal_sequence": "local micro-episode or event chain with clear ordering",
        "theme_set": "items sharing a theme/motif, no temporal ordering",
        "method_example_set": "techniques, methods, and their examples",
        "contrast_set": "items presented in explicit contrast",
    },
)
```

Design choice: the registry is a frozen dataclass, not a JSON file. Adding a
domain means writing a Python module-level constant (~20 lines). This avoids
YAML/JSON config parsing indirection while keeping types owned in code.

### 5. Prompt composition with contracts

The v0.3 prompt files shrink. Example: the extraction prompt becomes:

```markdown
You extract source-grounded reading structures from one text segment.

{{ language_policy }}

{{ input_contract }}

{{ output_schema }}

## Type vocabulary

{{ concept_type_vocabulary }}
{{ item_type_vocabulary }}

## Binding rules

- Current `source_blocks` and marked `text` are the only evidence source.
- ... (pass-specific rules remain hand-written) ...

## Region guidance

- ... (pass-specific, hand-written) ...
```

The `{{ placeholders }}` are rendered by `PassContract` at composition time.
`reading_prompts.py` builders inject the rendered sections as
`generated_prompt_part` entries — reusing the existing `PromptComposition`
machinery.

### 6. What stays hand-written in prompt files

- Binding rules (pass-specific, need precise wording)
- Region guidance (pass-specific heuristics)
- Multi-round protocol (agentic passes only)
- Proposal rules (resolution passes only)
- Anti-examples (inline with schema, language-matched)

These are genuinely pass-specific and don't drift across files.

## Files

| File | Action | Est. lines |
|------|--------|------------|
| `tilusion/prompt_contracts.py` | CREATE | ~150 |
| `tilusion/reading_schema.py` | Minor: add type definitions dict | +10 |
| `tilusion/reading_prompts.py` | Modify: use contracts in builders | ~40, ~20 |
| `tilusion/prompts/*.md` | Shrink: replace sections with placeholders | ~50 removed per file |
| `tests/test_prompt_contracts.py` | CREATE | ~80 |

## What this does NOT change

- Schema dataclasses (`Concept`, `AtomicItem`, `LogicalGroup`) — unchanged.
- Validation — unchanged.
- Pipeline orchestration — unchanged.
- Cache key semantics — prompt content hash still captures the full rendered
  prompt, so narrowing types still busts the cache correctly.

## Acceptance criteria

- Adding a new concept type requires editing `reading_schema.py` only.
- Narrowing types for a domain requires passing a different `TypeVocabulary`
  to the contract, not editing any prompt file.
- Field-language policy is rendered from `PassContract.render_language_policy()`,
  not copy-pasted across 5 files.
- Tests assert `TypeVocabulary.preferred` membership, not prompt substrings.
- Existing 423 tests pass; new contract tests cover rendering and domain subsets.
