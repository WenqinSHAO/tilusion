"""Code-owned prompt/data-model contracts.

Phase 2c: owns field metadata, type vocabularies, and shared prompt sections
that were previously hand-maintained in five separate .md prompt files.

A PassContract renders language_policy, input_contract, and type_vocabulary
sections from metadata. Prompt files use {{ placeholder }} markers; builders
in reading_prompts.py substitute rendered content at composition time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .reading_schema import (
    RECOMMENDED_CONCEPT_TYPES,
    RECOMMENDED_GROUP_TYPES,
    RECOMMENDED_ITEM_TYPES,
    get_domain_config,
)


# ── Field role ───────────────────────────────────────────────────────────────


class FieldRole(Enum):
    """Language-policy role for a JSON field."""

    SOURCE_IDENTITY = "source_identity"  # surface, canonical_name, observed_surfaces
    READER_PROSE = "reader_prose"  # summary, rationale, warnings
    NORMALIZED_INTERNAL = "normalized_internal"  # concept_type, facets, IDs


# ── Field metadata ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class FieldMeta:
    """Description of one input or output field for prompt contract rendering."""

    name: str
    role: FieldRole
    description: str
    required: bool = True


# ── Type vocabulary ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class TypeVocabulary:
    """Owns allowed types for one category, with domain-specific preferences.

    ``preferred`` is the subset presented first in prompts.  ``extended``
    types are also shown (\"also accepted when needed\").  Types in
    ``all_types`` that are in neither set are accepted by validators but
    hidden from the LLM.  ``definitions`` provides one-line distinguishing
    criteria for key types.
    """

    category: str  # "concept", "item", "group", "edge"
    all_types: frozenset[str]
    preferred: frozenset[str]
    extended: frozenset[str] = field(default_factory=frozenset)
    definitions: dict[str, str] = field(default_factory=dict)
    escape_hatch: str = "other"

    def __post_init__(self) -> None:
        if not self.preferred.issubset(self.all_types):
            raise ValueError(
                f"preferred types not in all_types: {self.preferred - self.all_types}"
            )
        if not self.extended.issubset(self.all_types):
            raise ValueError(
                f"extended types not in all_types: {self.extended - self.all_types}"
            )
        if self.preferred & self.extended:
            raise ValueError(
                f"preferred and extended overlap: {self.preferred & self.extended}"
            )

    # ── rendering ────────────────────────────────────────────────────────

    def render_compact(self) -> str:
        """Two-tier prompt list: preferred first, then extended."""
        pref = ", ".join(f"`{t}`" for t in sorted(self.preferred))
        ext_list = sorted(self.extended)
        if not ext_list:
            return (
                f"Use only these {self.category} types. "
                f"If none fit, use `{self.escape_hatch}`; "
                f"do not invent custom types.\n\n"
                f"Allowed: {pref}, `{self.escape_hatch}`."
            )
        ext = ", ".join(f"`{t}`" for t in ext_list)
        return (
            f"Prefer this {self.category} vocabulary: {pref}, "
            f"`{self.escape_hatch}`. "
            f"Also accepted when needed: {ext}."
        )

    def render_allowed_set(self) -> str:
        """Flat pipe-delimited string for schema constraint prose."""
        all_sorted = sorted(self.all_types)
        return "|".join(all_sorted)

    def render_definitions(self) -> str:
        """One bullet per defined type (only preferred types with definitions)."""
        if not self.definitions:
            return ""
        lines = []
        for t in sorted(self.preferred):
            desc = self.definitions.get(t)
            if desc:
                lines.append(f"- `{t}`: {desc}")
        return "\n".join(lines) if lines else ""


# ── Pass contract ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PassContract:
    """Owns the prompt-facing data interface for one pipeline pass.

    Render methods produce the markdown sections that were previously
    hand-maintained in each prompt file.
    """

    pass_name: str
    input_fields: list[FieldMeta]
    type_vocabularies: dict[str, TypeVocabulary] = field(default_factory=dict)

    # ── helpers ──────────────────────────────────────────────────────────

    def _fields_by_role(self, role: FieldRole) -> list[FieldMeta]:
        return [f for f in self.input_fields if f.role == role]

    def _field_names(self, role: FieldRole) -> str:
        names = [f"`{f.name}`" for f in self._fields_by_role(role)]
        if not names:
            return "(none)"
        return ", ".join(names)

    # ── rendering ────────────────────────────────────────────────────────

    def render_language_policy(self) -> str:
        """Shared field-language policy section (all passes)."""
        source_fields = self._field_names(FieldRole.SOURCE_IDENTITY)
        reader_fields = self._field_names(FieldRole.READER_PROSE)
        internal_fields = self._field_names(FieldRole.NORMALIZED_INTERNAL)

        return (
            "## Field-language policy\n"
            "\n"
            "The payload includes `language_policy`:\n"
            "- `source_language`: source-text language. "
            "If it is `auto`, infer it from the supplied `text` and keep "
            "source-grounded fields in the original script/form.\n"
            "- `reader_language`: language for reader-facing prose. "
            "`zh-Hans` means Simplified Chinese.\n"
            "- `normalized_language`: not a prose language target. "
            "It means controlled internal schema tokens: English enum values "
            "and stable slug-like facet tags.\n"
            "\n"
            "Use this policy by field role. "
            "The lists below are representative; when a field has the same "
            "role, apply the same rule.\n"
            f"- Source-grounded identity fields: {source_fields}. "
            "Copy from or normalize within the original source text; "
            "never translate these because of `reader_language`.\n"
            f"- Reader-facing prose fields: {reader_fields}. "
            "Write these in `reader_language`.\n"
            f"- Pipeline-normalized fields: {internal_fields}. "
            "Use controlled English enum/slug tokens consistently.\n"
            "\n"
            "Return only one JSON object. No prose, markdown, or code fences."
        )

    def render_input_contract(self) -> str:
        """Input field descriptions rendered from FieldMeta list."""
        lines = ["## Input contract", ""]
        field_names = [f"`{f.name}`" for f in self.input_fields]
        lines.append("Input keys: " + ", ".join(field_names) + ".")
        for f in self.input_fields:
            suffix = "" if f.required else " (optional)"
            lines.append(f"- `{f.name}`: {f.description}{suffix}.")
        return "\n".join(lines)

    def render_type_vocabularies(self) -> str:
        """All type vocabulary sections for this pass."""
        if not self.type_vocabularies:
            return ""
        blocks = []
        for vocab in self.type_vocabularies.values():
            blocks.append(
                f"## {vocab.category.title()} type vocabulary\n"
                f"\n"
                f"{vocab.render_compact()}\n"
            )
            defs = vocab.render_definitions()
            if defs:
                blocks.append(f"{defs}\n")
        return "\n".join(blocks).rstrip() + "\n"


# ── Domain registries ─────────────────────────────────────────────────────────
#
# Each registry is a module-level constant.  Adding a domain means writing
# one of these (~15 lines).  No YAML/JSON config indirection.


def _build_domain_vocabulary(category: str, domain: str) -> TypeVocabulary:
    """Build a TypeVocabulary from the shared JSON config."""
    dom = get_domain_config(category, domain)
    return TypeVocabulary(
        category=category,
        all_types=_ALL_TYPES[category],
        preferred=frozenset(dom.get("preferred", [])),
        extended=frozenset(dom.get("extended", [])),
        definitions=dom.get("definitions", {}),
    )


_ALL_TYPES = {
    "concept": RECOMMENDED_CONCEPT_TYPES,
    "item": RECOMMENDED_ITEM_TYPES,
    "group": RECOMMENDED_GROUP_TYPES,
}

NARRATIVE_CONCEPT_TYPES = _build_domain_vocabulary("concept", "narrative")
NARRATIVE_ITEM_TYPES = _build_domain_vocabulary("item", "narrative")
NARRATIVE_GROUP_TYPES = _build_domain_vocabulary("group", "narrative")

# ── Pass contracts ────────────────────────────────────────────────────────────
#
# Each contract defines the input fields and type vocabularies for one pass.


EXTRACTION_CONTRACT = PassContract(
    pass_name="per_segment_extraction",
    input_fields=[
        FieldMeta("task", FieldRole.NORMALIZED_INTERNAL, "task identifier"),
        FieldMeta("schema_version", FieldRole.NORMALIZED_INTERNAL, "schema version string"),
        FieldMeta("unit_id", FieldRole.NORMALIZED_INTERNAL, "stable unit identifier; copy it exactly to output"),
        FieldMeta("segment", FieldRole.NORMALIZED_INTERNAL, "segment metadata including `segment_id`; copy `segment.segment_id` to output"),
        FieldMeta("source_blocks", FieldRole.SOURCE_IDENTITY, "metadata for block IDs, types, and offsets"),
        FieldMeta("text", FieldRole.SOURCE_IDENTITY, "exact source text with inline block markers"),
        FieldMeta("context", FieldRole.READER_PROSE, "optional guidance such as `extraction_hints` or prior digest; treat as guidance, not evidence"),
        FieldMeta("language_policy", FieldRole.NORMALIZED_INTERNAL, "field-language policy object"),
    ],
    type_vocabularies={
        "concept": NARRATIVE_CONCEPT_TYPES,
        "item": NARRATIVE_ITEM_TYPES,
    },
)

GROUPING_CONTRACT = PassContract(
    pass_name="unit_logical_grouping",
    input_fields=[
        FieldMeta("task", FieldRole.NORMALIZED_INTERNAL, "task identifier"),
        FieldMeta("schema_version", FieldRole.NORMALIZED_INTERNAL, "schema version string"),
        FieldMeta("unit_id", FieldRole.NORMALIZED_INTERNAL, "stable unit identifier; copy it exactly to output"),
        FieldMeta("unit_text", FieldRole.SOURCE_IDENTITY, "source text for the full unit; use only to verify grouping relationships"),
        FieldMeta("source", FieldRole.NORMALIZED_INTERNAL, "source metadata"),
        FieldMeta("segments", FieldRole.READER_PROSE, "overview boundaries and hints; hints are guidance, not evidence"),
        FieldMeta("concepts", FieldRole.SOURCE_IDENTITY, "authoritative resolved concepts to group"),
        FieldMeta("atomic_items", FieldRole.SOURCE_IDENTITY, "authoritative resolved items to group"),
        FieldMeta("implicit_refs", FieldRole.READER_PROSE, "optional carry-forward implicit references"),
        FieldMeta("unresolved_items", FieldRole.READER_PROSE, "optional carry-forward unresolved items"),
        FieldMeta("context", FieldRole.READER_PROSE, "optional guidance such as prior digest"),
        FieldMeta("language_policy", FieldRole.NORMALIZED_INTERNAL, "field-language policy object"),
    ],
    type_vocabularies={
        "group": NARRATIVE_GROUP_TYPES,
    },
)

CONCEPT_RESOLUTION_CONTRACT = PassContract(
    pass_name="cross_unit_concept_resolution",
    input_fields=[
        FieldMeta("task", FieldRole.NORMALIZED_INTERNAL, "task identifier"),
        FieldMeta("schema_version", FieldRole.NORMALIZED_INTERNAL, "schema version string"),
        FieldMeta("unit_id", FieldRole.NORMALIZED_INTERNAL, "parent reader unit identifier; copy it exactly to final output"),
        FieldMeta("concepts", FieldRole.SOURCE_IDENTITY, "merged unit-level concepts"),
        FieldMeta("registry_index", FieldRole.NORMALIZED_INTERNAL, "compact candidate registry concept table from prior units"),
        FieldMeta("candidate_map", FieldRole.NORMALIZED_INTERNAL, "per-unit-concept shortlist and primary screening structure"),
        FieldMeta("unresolved_items", FieldRole.READER_PROSE, "surfaces that appear with different types across segments"),
        FieldMeta("context", FieldRole.READER_PROSE, "optional book digest or context"),
        FieldMeta("language_policy", FieldRole.NORMALIZED_INTERNAL, "field-language policy object"),
    ],
    type_vocabularies={
        "concept": NARRATIVE_CONCEPT_TYPES,
    },
)

GROUP_RESOLUTION_CONTRACT = PassContract(
    pass_name="cross_unit_group_resolution",
    input_fields=[
        FieldMeta("task", FieldRole.NORMALIZED_INTERNAL, "task identifier"),
        FieldMeta("schema_version", FieldRole.NORMALIZED_INTERNAL, "schema version string"),
        FieldMeta("unit_id", FieldRole.NORMALIZED_INTERNAL, "parent reader unit identifier; copy it exactly to final output"),
        FieldMeta("concepts", FieldRole.SOURCE_IDENTITY, "resolved unit concepts; linked concepts may have `registry_ref`"),
        FieldMeta("groups", FieldRole.SOURCE_IDENTITY, "unit logical groups"),
        FieldMeta("registry_groups", FieldRole.NORMALIZED_INTERNAL, "candidate groups from prior units"),
        FieldMeta("context", FieldRole.READER_PROSE, "optional book digest or context"),
        FieldMeta("language_policy", FieldRole.NORMALIZED_INTERNAL, "field-language policy object"),
    ],
    type_vocabularies={
        "group": NARRATIVE_GROUP_TYPES,
    },
)

OVERVIEW_CONTRACT = PassContract(
    pass_name="overview_segmentation",
    input_fields=[
        FieldMeta("task", FieldRole.NORMALIZED_INTERNAL, "task identifier"),
        FieldMeta("unit_id", FieldRole.NORMALIZED_INTERNAL, "stable unit identifier; copy it exactly to output"),
        FieldMeta("unit", FieldRole.NORMALIZED_INTERNAL, "unit metadata such as label, kind, source range; do not copy to output"),
        FieldMeta("text", FieldRole.SOURCE_IDENTITY, "exact source text for the full unit"),
        FieldMeta("context", FieldRole.READER_PROSE, "optional guidance object; use only to shape hints, not as source evidence"),
        FieldMeta("language_policy", FieldRole.NORMALIZED_INTERNAL, "field-language policy object"),
    ],
)


# ── Rendered prompt sections (for placeholder substitution) ───────────────────


def render_contract_sections(contract: PassContract) -> dict[str, str]:
    """Return a dict of placeholder -> rendered markdown for a pass contract.

    Keys match the ``{{ placeholder }}`` markers used in v0.3 prompt files.
    """
    return {
        "{{ language_policy }}": contract.render_language_policy(),
        "{{ input_contract }}": contract.render_input_contract(),
        "{{ type_vocabularies }}": contract.render_type_vocabularies(),
    }


def apply_contract_to_prompt(prompt_text: str, contract: PassContract) -> str:
    """Substitute ``{{ placeholder }}`` markers in *prompt_text* with
    rendered sections from *contract*."""
    sections = render_contract_sections(contract)
    result = prompt_text
    for placeholder, rendered in sections.items():
        result = result.replace(placeholder, rendered)
    return result
