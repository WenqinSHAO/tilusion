from __future__ import annotations

import pytest

from tilusion.repair import (
    DeterministicAutoFixer,
    _parse_dot_path,
    _parse_index_path,
    _resolve_path,
)


# ── Path helpers ──


def test_parse_index_path_simple() -> None:
    parent, index = _parse_index_path("concepts[3]")
    assert parent == "concepts"
    assert index == 3


def test_parse_index_path_nested() -> None:
    parent, index = _parse_index_path("concepts[3].source_block_refs[0]")
    assert parent == "concepts[3].source_block_refs"
    assert index == 0


def test_parse_index_path_no_bracket() -> None:
    parent, index = _parse_index_path("schema_version")
    assert parent is None
    assert index is None


def test_parse_dot_path_simple() -> None:
    parent, key = _parse_dot_path("concepts[0].surface")
    assert parent == "concepts[0]"
    assert key == "surface"


def test_parse_dot_path_top_level() -> None:
    parent, key = _parse_dot_path("schema_version")
    assert parent is None
    assert key is None


def test_resolve_path_nested() -> None:
    data = {"concepts": [{"concept_id": "c1", "surface": "Test"}]}
    assert _resolve_path(data, "concepts[0].surface") == "Test"
    assert _resolve_path(data, "concepts[0].concept_id") == "c1"
    assert _resolve_path(data, "concepts") == [{"concept_id": "c1", "surface": "Test"}]


# ── Auto-fixer tests ──


def test_fix_invalid_ref_removes_from_list() -> None:
    fixer = DeterministicAutoFixer()
    data = {
        "concepts": [
            {"concept_id": "c1", "source_block_refs": ["b1", "missing-block", "b2"]}
        ]
    }
    issues = [
        {
            "severity": "error",
            "code": "unknown_ref",
            "path": "concepts[0].source_block_refs[1]",
            "message": "Reference missing-block does not resolve.",
            "repair_hint": "",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "unknown_ref" in fixed
    assert remaining == []
    assert data["concepts"][0]["source_block_refs"] == ["b1", "b2"]


def test_fix_empty_string_list_item() -> None:
    fixer = DeterministicAutoFixer()
    data = {"concepts": [{"aliases": ["Alice", "", "  ", "Bob"]}]}
    issues = [
        {
            "severity": "error",
            "code": "empty_string_list_item",
            "path": "concepts[0].aliases[1]",
            "message": "List item must be non-empty.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "empty_string_list_item" in fixed
    assert data["concepts"][0]["aliases"] == ["Alice", "  ", "Bob"]


def test_fix_duplicate_object_id() -> None:
    fixer = DeterministicAutoFixer()
    data = {
        "concepts": [
            {"concept_id": "c1", "surface": "First"},
            {"concept_id": "c1", "surface": "Second"},
        ]
    }
    issues = [
        {
            "severity": "error",
            "code": "duplicate_object_id",
            "path": "concepts[1].concept_id",
            "message": "Duplicate id c1.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "duplicate_object_id" in fixed
    assert data["concepts"][0]["concept_id"] == "c1"
    assert data["concepts"][1]["concept_id"] == "c1_dedup2"


def test_fix_missing_required_field() -> None:
    fixer = DeterministicAutoFixer()
    data = {"concepts": [{"surface": "Alice"}]}
    issues = [
        {
            "severity": "error",
            "code": "missing_required_field",
            "path": "concepts[0].concept_id",
            "message": "Missing required field concept_id.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "missing_required_field" in fixed
    assert data["concepts"][0]["concept_id"] == ""


def test_fix_schema_version_mismatch() -> None:
    fixer = DeterministicAutoFixer()
    data = {"schema_version": "v0.1-old", "unit_id": "u1"}
    issues = [
        {
            "severity": "error",
            "code": "schema_version_mismatch",
            "path": "schema_version",
            "message": "Expected schema_version v0.3.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "schema_version_mismatch" in fixed
    assert data["schema_version"] == "reading-unit-v0.3"


def test_fix_stale_core_field() -> None:
    fixer = DeterministicAutoFixer()
    data = {"source_spans": [{"old": "data"}], "unit_id": "u1", "concepts": []}
    issues = [
        {
            "severity": "error",
            "code": "stale_core_field",
            "path": "source_spans",
            "message": "source_spans is not a v0.3 core field.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "stale_core_field" in fixed
    assert "source_spans" not in data
    assert "unit_id" in data


def test_fix_wrong_field_type_coerces_none_to_empty_string() -> None:
    fixer = DeterministicAutoFixer()
    data = {"concepts": [{"concept_id": "c1", "surface": None}]}
    issues = [
        {
            "severity": "error",
            "code": "wrong_field_type",
            "path": "concepts[0].surface",
            "message": "Field surface must be str.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "wrong_field_type" in fixed
    assert data["concepts"][0]["surface"] == ""


def test_fix_wrong_field_type_coerces_non_list_to_empty_list() -> None:
    fixer = DeterministicAutoFixer()
    data = {"concepts": [{"concept_id": "c1", "source_block_refs": None}]}
    issues = [
        {
            "severity": "error",
            "code": "wrong_field_type",
            "path": "concepts[0].source_block_refs",
            "message": "Reference field must be a list.",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "wrong_field_type" in fixed
    assert data["concepts"][0]["source_block_refs"] == []


def test_not_auto_fixable_errors_are_kept() -> None:
    fixer = DeterministicAutoFixer()
    data: dict = {}
    issues = [
        {
            "severity": "error",
            "code": "invalid_grounding",
            "path": "concepts[0].provenance.grounding",
            "message": "Bad grounding.",
        },
        {
            "severity": "error",
            "code": "prior_context_used_as_evidence",
            "path": "concepts[0].source_block_refs[0]",
            "message": "Prior context used as evidence.",
        },
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert fixed == []
    assert len(remaining) == 2


def test_multiple_fixes_in_one_pass() -> None:
    fixer = DeterministicAutoFixer()
    data = {
        "schema_version": "wrong-version",
        "source_spans": [{"old": "data"}],
        "concepts": [
            {"concept_id": "c1", "aliases": ["Alice", "", "Bob"], "source_block_refs": ["b1", "bad-ref"]}
        ],
    }
    issues = [
        {"severity": "error", "code": "schema_version_mismatch", "path": "schema_version", "message": "Bad version."},
        {"severity": "error", "code": "stale_core_field", "path": "source_spans", "message": "Stale."},
        {"severity": "error", "code": "empty_string_list_item", "path": "concepts[0].aliases[1]", "message": "Empty."},
        {"severity": "error", "code": "unknown_ref", "path": "concepts[0].source_block_refs[1]", "message": "Unknown."},
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert set(fixed) == {"schema_version_mismatch", "stale_core_field", "empty_string_list_item", "unknown_ref"}
    assert remaining == []
    assert data["schema_version"] == "reading-unit-v0.3"
    assert "source_spans" not in data
    assert data["concepts"][0]["aliases"] == ["Alice", "Bob"]
    assert data["concepts"][0]["source_block_refs"] == ["b1"]


def test_unknown_error_code_is_kept_as_remaining() -> None:
    fixer = DeterministicAutoFixer()
    data: dict = {}
    issues = [{"severity": "error", "code": "some_future_code", "path": "x", "message": "Unknown."}]
    fixed, remaining = fixer.fix(data, issues)
    assert fixed == []
    assert len(remaining) == 1


def test_fixer_handles_malformed_paths_gracefully() -> None:
    fixer = DeterministicAutoFixer()
    data = {"concepts": []}
    issues = [
        {"severity": "error", "code": "unknown_ref", "path": "concepts[99].source_block_refs[0]", "message": "Bad path."},
        {"severity": "error", "code": "invalid_ref", "path": "", "message": "Empty path."},
    ]
    fixed, remaining = fixer.fix(data, issues)
    # Both should fail gracefully and remain
    assert len(remaining) == 2


def test_fix_missing_source_block_refs_inherits_from_merged_source() -> None:
    """When a concept has merged_from refs pointing to still-present source concepts,
    inherit source_block_refs from those source concepts."""
    fixer = DeterministicAutoFixer()
    data = {
        "concepts": [
            {
                "concept_id": "seg-0001-concept-0001",
                "surface": "余",
                "source_block_refs": ["seg-0001-block-0000", "seg-0002-block-0001"],
                "merged_from": [],
            },
            {
                "concept_id": "concept-0002",
                "surface": "余",
                "source_block_refs": [],
                "merged_from": ["seg-0001-concept-0001"],
            },
        ]
    }
    issues = [
        {
            "severity": "error",
            "code": "missing_source_block_refs",
            "path": "concepts[1].source_block_refs",
            "message": "Source-grounded concepts must cite at least one source block.",
            "repair_hint": "",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert "missing_source_block_refs" in fixed
    assert remaining == []
    # Inherited refs from the source concept with matching concept_id
    assert set(data["concepts"][1]["source_block_refs"]) == {"seg-0001-block-0000", "seg-0002-block-0001"}


def test_fix_missing_source_block_refs_no_merged_from_is_not_fixed() -> None:
    """Concept without merged_from — can't inherit, stays unfixed."""
    fixer = DeterministicAutoFixer()
    data = {
        "concepts": [
            {
                "concept_id": "concept-0001",
                "surface": "白泥",
                "source_block_refs": [],
                "merged_from": [],
            },
        ]
    }
    issues = [
        {
            "severity": "error",
            "code": "missing_source_block_refs",
            "path": "concepts[0].source_block_refs",
            "message": "Source-grounded concepts must cite at least one source block.",
            "repair_hint": "",
        }
    ]
    fixed, remaining = fixer.fix(data, issues)
    assert fixed == []
    assert len(remaining) == 1
