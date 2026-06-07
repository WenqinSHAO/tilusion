from __future__ import annotations

import pytest

from tilusion.prompt_contracts import (
    EXTRACTION_CONTRACT,
    GROUP_RESOLUTION_CONTRACT,
    GROUPING_CONTRACT,
    CONCEPT_RESOLUTION_CONTRACT,
    NARRATIVE_CONCEPT_TYPES,
    NARRATIVE_GROUP_TYPES,
    NARRATIVE_ITEM_TYPES,
    OVERVIEW_CONTRACT,
    FieldMeta,
    FieldRole,
    PassContract,
    TypeVocabulary,
    apply_contract_to_prompt,
    render_contract_sections,
)
from tilusion.reading_schema import (
    RECOMMENDED_CONCEPT_TYPES,
    RECOMMENDED_GROUP_TYPES,
    RECOMMENDED_ITEM_TYPES,
)


# ── TypeVocabulary ────────────────────────────────────────────────────────────


class TestTypeVocabularyValidation:
    def test_preferred_must_be_subset_of_all(self) -> None:
        with pytest.raises(ValueError, match="preferred types not in all_types"):
            TypeVocabulary(
                category="test",
                all_types=frozenset({"a", "b"}),
                preferred=frozenset({"a", "c"}),
            )

    def test_extended_must_be_subset_of_all(self) -> None:
        with pytest.raises(ValueError, match="extended types not in all_types"):
            TypeVocabulary(
                category="test",
                all_types=frozenset({"a", "b"}),
                preferred=frozenset({"a"}),
                extended=frozenset({"c"}),
            )

    def test_preferred_and_extended_must_not_overlap(self) -> None:
        with pytest.raises(ValueError, match="preferred and extended overlap"):
            TypeVocabulary(
                category="test",
                all_types=frozenset({"a", "b", "c"}),
                preferred=frozenset({"a", "b"}),
                extended=frozenset({"b", "c"}),
            )

    def test_valid_vocabulary_construction(self) -> None:
        vocab = TypeVocabulary(
            category="concept",
            all_types=frozenset({"a", "b", "c", "other"}),
            preferred=frozenset({"a", "b", "other"}),
            extended=frozenset({"c"}),
        )
        assert vocab.category == "concept"
        assert vocab.escape_hatch == "other"


class TestTypeVocabularyRendering:
    def test_render_compact_with_extended(self) -> None:
        vocab = TypeVocabulary(
            category="concept",
            all_types=frozenset({"person", "place", "other", "group"}),
            preferred=frozenset({"person", "place", "other"}),
            extended=frozenset({"group"}),
        )
        out = vocab.render_compact()
        assert "Prefer this concept vocabulary" in out
        assert "`person`" in out
        assert "`place`" in out
        assert "`other`" in out
        assert "Also accepted when needed" in out
        assert "`group`" in out

    def test_render_compact_without_extended(self) -> None:
        vocab = TypeVocabulary(
            category="item",
            all_types=frozenset({"event", "action", "other"}),
            preferred=frozenset({"event", "action", "other"}),
        )
        out = vocab.render_compact()
        assert "Use only these item types" in out
        assert "`event`" in out
        assert "`action`" in out
        assert "Also accepted" not in out

    def test_render_definitions_only_preferred(self) -> None:
        vocab = TypeVocabulary(
            category="concept",
            all_types=frozenset({"person", "place", "other"}),
            preferred=frozenset({"person", "place", "other"}),
            definitions={"person": "a human", "place": "a location"},
        )
        out = vocab.render_definitions()
        assert "- `person`: a human" in out
        assert "- `place`: a location" in out
        assert "other" not in out  # no definition for other

    def test_render_definitions_empty(self) -> None:
        vocab = TypeVocabulary(
            category="concept",
            all_types=frozenset({"a", "other"}),
            preferred=frozenset({"a", "other"}),
        )
        assert vocab.render_definitions() == ""


# ── PassContract ──────────────────────────────────────────────────────────────


class TestPassContractRendering:
    def test_render_language_policy_includes_field_lists(self) -> None:
        contract = PassContract(
            pass_name="test",
            input_fields=[
                FieldMeta("surface", FieldRole.SOURCE_IDENTITY, "test surface"),
                FieldMeta("summary", FieldRole.READER_PROSE, "test summary"),
                FieldMeta("concept_type", FieldRole.NORMALIZED_INTERNAL, "test type"),
            ],
        )
        out = contract.render_language_policy()
        assert "## Field-language policy" in out
        assert "`surface`" in out
        assert "`summary`" in out
        assert "`concept_type`" in out
        assert "source_language" in out
        assert "reader_language" in out
        assert "normalized_language" in out
        assert "Return only one JSON object" in out

    def test_render_language_policy_empty_roles(self) -> None:
        contract = PassContract(
            pass_name="test",
            input_fields=[
                FieldMeta("task", FieldRole.NORMALIZED_INTERNAL, "task id"),
            ],
        )
        out = contract.render_language_policy()
        assert "Source-grounded identity fields: (none)" in out

    def test_render_input_contract(self) -> None:
        contract = PassContract(
            pass_name="test",
            input_fields=[
                FieldMeta("unit_id", FieldRole.NORMALIZED_INTERNAL, "stable unit id"),
                FieldMeta("text", FieldRole.SOURCE_IDENTITY, "exact source text"),
            ],
        )
        out = contract.render_input_contract()
        assert "## Input contract" in out
        assert "`unit_id`" in out
        assert "`text`" in out
        assert "stable unit id" in out
        assert "exact source text" in out

    def test_render_type_vocabularies(self) -> None:
        vocab = TypeVocabulary(
            category="concept",
            all_types=frozenset({"person", "place", "other"}),
            preferred=frozenset({"person", "place", "other"}),
            definitions={"person": "a human"},
        )
        contract = PassContract(
            pass_name="test",
            input_fields=[],
            type_vocabularies={"concept": vocab},
        )
        out = contract.render_type_vocabularies()
        assert "## Concept type vocabulary" in out
        assert "`person`" in out

    def test_render_type_vocabularies_empty(self) -> None:
        contract = PassContract(pass_name="test", input_fields=[])
        assert contract.render_type_vocabularies() == ""


# ── Domain registries ─────────────────────────────────────────────────────────


class TestNarrativeDomainRegistries:
    def test_concept_types_match_schema(self) -> None:
        assert NARRATIVE_CONCEPT_TYPES.all_types == RECOMMENDED_CONCEPT_TYPES
        assert NARRATIVE_CONCEPT_TYPES.preferred.issubset(RECOMMENDED_CONCEPT_TYPES)
        assert NARRATIVE_CONCEPT_TYPES.extended.issubset(RECOMMENDED_CONCEPT_TYPES)

    def test_item_types_match_schema(self) -> None:
        assert NARRATIVE_ITEM_TYPES.all_types == RECOMMENDED_ITEM_TYPES
        assert NARRATIVE_ITEM_TYPES.preferred.issubset(RECOMMENDED_ITEM_TYPES)
        assert NARRATIVE_ITEM_TYPES.extended.issubset(RECOMMENDED_ITEM_TYPES)

    def test_group_types_match_schema(self) -> None:
        assert NARRATIVE_GROUP_TYPES.all_types == RECOMMENDED_GROUP_TYPES
        assert NARRATIVE_GROUP_TYPES.preferred.issubset(RECOMMENDED_GROUP_TYPES)

    def test_concept_preferred_set_matches_v0_3_prompt(self) -> None:
        expected = {"person", "place", "time_anchor", "object", "term", "method", "source", "other"}
        assert NARRATIVE_CONCEPT_TYPES.preferred == expected

    def test_concept_extended_set_matches_v0_3_prompt(self) -> None:
        expected = {"group", "organization"}
        assert NARRATIVE_CONCEPT_TYPES.extended == expected

    def test_item_preferred_set_matches_v0_3_prompt(self) -> None:
        expected = {"event", "action", "statement", "argument", "observation", "technique", "process", "other"}
        assert NARRATIVE_ITEM_TYPES.preferred == expected

    def test_group_preferred_set_matches_v0_3_prompt(self) -> None:
        expected = {
            "timeline", "temporal_sequence", "theme_set", "method_example_set",
            "motif_development", "contrast_set", "viewpoint_evolution", "other",
        }
        assert NARRATIVE_GROUP_TYPES.preferred == expected


# ── Built-in pass contracts ───────────────────────────────────────────────────


class TestBuiltinContracts:
    def test_extraction_contract_renders_all_sections(self) -> None:
        sections = render_contract_sections(EXTRACTION_CONTRACT)
        rendered = sections["{{ language_policy }}"]
        assert "Field-language policy" in rendered
        # Output fields now included: surface is a source-grounded output field
        assert "`surface`" in rendered
        assert "`summary`" in rendered
        rendered_input = sections["{{ input_contract }}"]
        assert "`unit_id`" in rendered_input
        assert "`text`" in rendered_input
        type_sec = sections["{{ type_vocabularies }}"]
        assert "Concept type vocabulary" in type_sec
        assert "Item type vocabulary" in type_sec

    def test_grouping_contract_renders_group_types(self) -> None:
        sections = render_contract_sections(GROUPING_CONTRACT)
        type_sec = sections["{{ type_vocabularies }}"]
        assert "Group type vocabulary" in type_sec
        assert "`timeline`" in type_sec
        assert "`temporal_sequence`" in type_sec

    def test_concept_resolution_contract_renders_concept_types(self) -> None:
        sections = render_contract_sections(CONCEPT_RESOLUTION_CONTRACT)
        type_sec = sections["{{ type_vocabularies }}"]
        assert "Concept type vocabulary" in type_sec

    def test_group_resolution_contract_renders_group_types(self) -> None:
        sections = render_contract_sections(GROUP_RESOLUTION_CONTRACT)
        type_sec = sections["{{ type_vocabularies }}"]
        assert "Group type vocabulary" in type_sec

    def test_overview_contract_no_type_vocabularies(self) -> None:
        sections = render_contract_sections(OVERVIEW_CONTRACT)
        type_sec = sections["{{ type_vocabularies }}"]
        assert type_sec == ""


# ── Placeholder substitution ──────────────────────────────────────────────────


class TestPlaceholderSubstitution:
    def test_apply_contract_substitutes_all_placeholders(self) -> None:
        prompt = "Task.\n\n{{ language_policy }}\n\n{{ input_contract }}\n\nRules."
        result = apply_contract_to_prompt(prompt, EXTRACTION_CONTRACT)
        assert "{{ language_policy }}" not in result
        assert "{{ input_contract }}" not in result
        assert "Task." in result
        assert "Rules." in result
        assert "Field-language policy" in result
        assert "Input contract" in result

    def test_apply_contract_handles_missing_placeholders(self) -> None:
        prompt = "Just a prompt.\n\n{{ input_contract }}"
        result = apply_contract_to_prompt(prompt, EXTRACTION_CONTRACT)
        assert "{{ input_contract }}" not in result
        assert "Just a prompt." in result
        assert "Input contract" in result

    def test_apply_contract_no_type_vocabularies_renders_empty(self) -> None:
        prompt = "{{ type_vocabularies }}"
        result = apply_contract_to_prompt(prompt, OVERVIEW_CONTRACT)
        assert result == ""
