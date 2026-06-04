from __future__ import annotations

import pytest

from tilusion.backend import (
    DEFAULT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEEPSEEK_CONTEXT_TOKENS,
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    ExtractionBudgetError,
    ExtractionError,
    check_extraction_budget,
    estimate_deepseek_tokens,
    parse_json_response,
)
from tilusion.extraction_quality import relocate_evidence_quote, validate_extraction_quality
from tilusion.overview import resolve_overview_segments
from tilusion.pass_utils import (
    build_pass_cache_key,
    generated_prompt_part,
)
from tilusion.extraction_prompts import build_overview_composition


def test_local_bundle_system_prompt_is_reusable_segment_extraction_contract() -> None:
    from tilusion.pass_utils import load_static_prompt_part
    part = load_static_prompt_part(
        "test", role="static_task_contract",
        resource_name="prompt_per_segment_extraction_v0.2.md",
    )
    prompt = part.content
    assert "You extract source-grounded reading structures from one text segment" in prompt
    assert "Minimum shape" in prompt
    assert "Do not build unit-level logical groups" in prompt
    assert "concept_type" in prompt
    assert "atomic_items" in prompt
    assert "source_blocks" in prompt
    assert "block_id" in prompt
    assert "only evidence source" in prompt


def test_prompt_composition_tracks_static_and_generated_parts() -> None:
    generated = generated_prompt_part(
        "validation-feedback",
        role="deterministic_validation_feedback",
        content="No unresolved evidence.",
        generated_by="validate_extraction_quality",
        metadata={"issue_count": 0},
    )

    prompt = build_overview_composition([generated])

    assert prompt.parts[0].role == "static_task_contract"
    assert prompt.parts[1].part_id == "validation-feedback"
    assert prompt.parts[1].generated_by == "validate_extraction_quality"
    assert "prompt-part:overview-segmentation-contract" in prompt.content
    assert "No unresolved evidence." in prompt.content
    assert len(prompt.content_hash) == 64


def test_overview_composition_tracks_static_prompt_contract() -> None:
    prompt = build_overview_composition()

    assert prompt.composition_id == "overview-segmentation-v0.2"
    assert prompt.parts[0].part_id == "overview-segmentation-contract"
    assert "coarse, source-grounded navigation overview" in prompt.content
    assert "start_quote" in prompt.content
    assert "end_quote" in prompt.content
    assert "region" in prompt.content
    assert "Do not pre-extract entities" in prompt.content


def test_resolve_overview_segments_extends_last_segment_to_unit_end() -> None:
    text = "Alpha starts.\n\nBeta continues.\n\nTail note remains."
    data = {
        "unit_id": "unit-0001",
        "overview_segments": [
            {
                "segment_id": "seg-0001",
                "title": "Alpha",
                "summary": "First part",
                "start_quote": "Alpha starts.",
                "end_quote": "Beta continues.",
            }
        ],
        "warnings": [],
    }

    segments, repairs = resolve_overview_segments(data, text)

    assert repairs == []
    assert len(segments) == 1
    assert segments[0].start == 0
    assert segments[0].end == len(text)
    assert segments[0].text == text


def test_resolve_overview_segments_deoverlaps_adjacent_segments() -> None:
    text = "A begins. Middle shared. B begins. End."
    data = {
        "unit_id": "unit-0001",
        "overview_segments": [
            {
                "segment_id": "seg-0001",
                "title": "A",
                "summary": "First",
                "start_quote": "A begins.",
                "end_quote": "B begins.",
            },
            {
                "segment_id": "seg-0002",
                "title": "B",
                "summary": "Second",
                "start_quote": "B begins.",
                "end_quote": "End.",
            },
        ],
        "warnings": [],
    }

    segments, repairs = resolve_overview_segments(data, text)

    assert repairs == []
    assert len(segments) == 2
    assert segments[0].end == segments[1].start
    assert segments[0].text + segments[1].text == text


def test_resolve_overview_segments_partial_anchor_fills_from_neighbour() -> None:
    """Segment with only start_quote resolved gets end from next segment's start."""
    text = "A begins. Middle section with lots of text. B begins. End."
    data = {
        "unit_id": "unit-0001",
        "overview_segments": [
            {
                "segment_id": "seg-0001",
                "title": "A",
                "summary": "First",
                "start_quote": "A begins.",
                "end_quote": "Middle section with lots of text.",
            },
            {
                "segment_id": "seg-0002",
                "title": "Middle",
                "summary": "Middle",
                "start_quote": "Middle section with lots of text.",
                "end_quote": "not present in source",
            },
            {
                "segment_id": "seg-0003",
                "title": "B",
                "summary": "Last",
                "start_quote": "B begins.",
                "end_quote": "End.",
            },
        ],
        "warnings": [],
    }

    segments, repairs = resolve_overview_segments(data, text)

    # seg-0002 should be included despite failed end_quote
    assert len(segments) == 3
    assert len(repairs) == 1
    assert repairs[0]["code"] == "segment_span_unresolved"
    assert repairs[0]["segment_id"] == "seg-0002"
    # seg-0002 end should be bounded by seg-0003 start
    middle_seg = next(s for s in segments if s.segment_id == "seg-0002")
    last_seg = next(s for s in segments if s.segment_id == "seg-0003")
    assert middle_seg.end == last_seg.start
    assert middle_seg.start < middle_seg.end


def test_resolve_overview_segments_both_anchors_missing_drops_segment() -> None:
    text = "Only real text."
    data = {
        "unit_id": "unit-0001",
        "overview_segments": [
            {
                "segment_id": "seg-0001",
                "title": "Missing",
                "summary": "Missing",
                "start_quote": "not present",
                "end_quote": "also absent",
            }
        ],
        "warnings": [],
    }

    segments, repairs = resolve_overview_segments(data, text)

    assert segments == []
    assert len(repairs) == 1
    assert repairs[0]["code"] == "segment_span_unresolved"


def test_extraction_budget_rejects_oversized_input() -> None:
    with pytest.raises(ExtractionBudgetError, match="likely to exceed model context"):
        check_extraction_budget(
            "",
            {"text": "字" * 1000},
            max_output_tokens=500,
            context_tokens=1000,
        )


def test_extraction_budget_rejects_output_above_model_limit() -> None:
    with pytest.raises(ExtractionBudgetError, match="exceeds DeepSeek V4 max output"):
        check_extraction_budget(
            "",
            {"text": "short"},
            max_output_tokens=DEEPSEEK_MAX_OUTPUT_TOKENS + 1,
        )


def test_token_estimate_distinguishes_cjk_and_english_text() -> None:
    assert estimate_deepseek_tokens("字" * 10) > estimate_deepseek_tokens("a" * 10)


def test_parse_json_response_reports_likely_truncation() -> None:
    with pytest.raises(ExtractionError, match="output truncation"):
        parse_json_response('{"unit_id": "unit-0001", "warnings": ["unfinished')


def test_extraction_quality_report_passes_clean_result() -> None:
    text = "Alice met Bob in Paris.\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "Alice met Bob in Paris.",
                "start_hint": "line 1",
                "end_hint": "line 1",
            }
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "Alice",
                "kind": "person",
                "summary": "Alice is mentioned locally.",
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "location_mentions": [
            {
                "mention_id": "location-0001",
                "surface": "Paris",
                "kind": "physical",
                "summary": "Paris is the event location.",
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "atom_mentions": [
            {
                "atom_id": "atom-0001",
                "atom_kind": "narrative_event",
                "summary": "Alice met Bob in Paris.",
                "participant_mention_ids": ["entity-0001"],
                "location_mention_ids": ["location-0001"],
                "time_expression_ids": [],
                "thread_ids": [],
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }

    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")

    assert report.passed
    assert report.issue_count == 0
    assert report.to_repair_payload()["quality_summary"]["passed"] is True
    assert report.to_repair_payload()["quality_summary"]["llm_actionable_issue_count"] == 0


def test_evidence_relocation_accepts_missing_note_marker() -> None:
    text = "余年十三，随母归宁[7]，两小无嫌。\n"
    quote = "余年十三，随母归宁，两小无嫌。"

    location = relocate_evidence_quote(text, quote, evidence_id="evidence-0001")

    assert location.status == "relocated"
    assert location.strategy == "annotation_whitespace_tolerant"
    assert location.match_text == "余年十三，随母归宁[7]，两小无嫌。"


def test_evidence_relocation_accepts_omitted_speech_quotes() -> None:
    text = "一日，芸问曰：“各种古文，宗何为是？”余曰：“《国策》。”"
    quote = "一日，芸问曰：各种古文，宗何为是？"

    location = relocate_evidence_quote(text, quote, evidence_id="overview-segment-0003:start_quote")

    assert location.status == "relocated"
    assert location.strategy == "annotation_whitespace_punctuation_dropped"
    assert location.match_text == "一日，芸问曰：“各种古文，宗何为是？”"


def test_extraction_quality_report_tracks_relocated_evidence_without_error() -> None:
    text = "余年十三，随母归宁[7]，两小无嫌。\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "余年十三，随母归宁，两小无嫌。",
                "start_hint": "line 1",
                "end_hint": "line 1",
            }
        ],
        "entity_mentions": [],
        "location_mentions": [],
        "atom_mentions": [
            {
                "atom_id": "atom-0001",
                "atom_kind": "narrative_event",
                "summary": "少年随母归宁。",
                "participant_mention_ids": [],
                "location_mention_ids": [],
                "time_expression_ids": [],
                "thread_ids": [],
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }

    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")

    assert report.passed
    assert report.issue_count == 0
    assert report.evidence_locations[0].status == "relocated"
    assert "[7]" in (report.evidence_locations[0].match_text or "")


def test_surface_grounding_uses_reconstructed_evidence_context() -> None:
    text = "时吾父稼夫公在会稽幕府 [2] ，专役相迓 [3] ，受业于武林赵省斋先生门下 [4] 。\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "时吾父稼夫公在会稽幕府",
                "start_hint": "paragraph start",
                "end_hint": "paragraph start",
            }
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "赵省斋先生",
                "kind": "person",
                "summary": "沈复的老师。",
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "location_mentions": [
            {
                "mention_id": "location-0001",
                "surface": "武林",
                "kind": "physical",
                "summary": "赵省斋先生门下受业处。",
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "atom_mentions": [
            {
                "atom_id": "atom-0001",
                "atom_kind": "narrative_event",
                "summary": "沈复受业于赵省斋。",
                "participant_mention_ids": ["entity-0001"],
                "location_mention_ids": ["location-0001"],
                "time_expression_ids": [],
                "thread_ids": [],
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }

    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")

    assert report.passed
    assert report.issue_count == 0


def test_surface_grounding_allows_generic_prefix_suffix_support() -> None:
    text = "是年冬，值其堂姊出阁，余又随母往。\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "是年冬，值其堂姊出阁",
                "start_hint": "line 1",
                "end_hint": "line 1",
            }
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "芸堂姊",
                "kind": "person",
                "summary": "芸的堂姊。",
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "location_mentions": [],
        "atom_mentions": [],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }

    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")

    assert report.passed
    assert [issue.code for issue in report.issues] == []


def test_surface_grounding_warning_stays_out_of_llm_repair_payload() -> None:
    text = "Alice met Bob in Paris.\n\nCharlie stayed home.\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "Alice met Bob in Paris",
                "start_hint": "line 1",
                "end_hint": "line 1",
            }
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "Charlie",
                "kind": "person",
                "summary": "Unsupported by the cited paragraph.",
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "location_mentions": [],
        "atom_mentions": [],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }

    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")
    repair_payload = report.to_repair_payload()

    assert report.passed
    assert [issue.code for issue in report.issues] == ["surface_not_in_evidence_context"]
    assert repair_payload["quality_summary"]["issue_count"] == 1
    assert repair_payload["quality_summary"]["llm_actionable_issue_count"] == 0
    assert repair_payload["issues"] == []


def test_extraction_quality_report_finds_repairable_llm_issues() -> None:
    text = "Alice met Bob in Paris.\nAlice returned later.\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "Alice met Bob in Paris",
                "start_hint": "line 1",
                "end_hint": "line 1",
            },
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "Alice",
                "start_hint": "line 1",
                "end_hint": "line 1",
            },
            {
                "evidence_id": "evidence-0003",
                "unit_id": "unit-0001",
                "quote": "x" * 601,
                "start_hint": "too broad",
                "end_hint": "too broad",
            },
            {
                "evidence_id": "evidence-0004",
                "unit_id": "unit-0001",
                "quote": "Alice met Bob in Rome.",
                "start_hint": "bad place",
                "end_hint": "bad place",
            },
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "Charlie",
                "kind": "person",
                "summary": "Unsupported person mention.",
                "evidence_span_ids": ["evidence-0001", "evidence-missing"],
            }
        ],
        "location_mentions": [],
        "atom_mentions": [
            {
                "atom_id": "atom-0001",
                "atom_kind": "narrative_event",
                "summary": "Bad references.",
                "participant_mention_ids": ["entity-missing"],
                "location_mention_ids": ["location-missing"],
                "time_expression_ids": ["time-missing"],
                "thread_ids": [],
                "evidence_span_ids": [],
            }
        ],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }

    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")
    codes = {issue.code for issue in report.issues}
    repair_payload = report.to_repair_payload(max_issues=3)

    assert not report.passed
    assert "duplicate_object_id" in codes
    assert "evidence_quote_missing" in codes
    assert "evidence_quote_ambiguous" in codes
    assert "evidence_quote_too_long" in codes
    assert "unresolved_evidence_ref" in codes
    assert "missing_evidence_refs" in codes
    assert "unresolved_object_ref" in codes
    assert "surface_not_in_evidence_context" in codes
    assert any(
        issue.code == "evidence_quote_missing" and issue.source_windows
        for issue in report.issues
    )
    assert repair_payload["evidence_relocation"]["unresolved"]
    assert repair_payload["quality_summary"]["truncated"] is True
    assert repair_payload["repair_instructions"]
    assert len(repair_payload["issues"]) == 3
    assert all(issue["code"] != "surface_not_in_evidence_context" for issue in repair_payload["issues"])


def test_canonical_name_avoids_surface_warning_when_surface_is_attested() -> None:
    """When canonical_name differs from surface, only surface is checked against evidence."""
    text = "Alice met Bob in Paris.\nCharlie stayed home.\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "Alice met Bob in Paris",
                "start_hint": "line 1",
                "end_hint": "line 1",
            }
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "Alice",
                "canonical_name": "Alice Johnson",
                "kind": "person",
                "summary": "Main character",
                "alias_candidate_of": None,
                "alias_confidence": None,
                "alias_rationale": None,
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "location_mentions": [],
        "atom_mentions": [],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }
    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")
    codes = [issue.code for issue in report.issues]
    assert "surface_not_in_evidence_context" not in codes


def test_surface_warning_still_fires_when_surface_absent_regardless_of_canonical() -> None:
    """Even with canonical_name set, if surface is not in evidence, warning fires."""
    text = "Alice met Bob in Paris.\n\nCharlie stayed home.\n"
    data = {
        "unit_id": "unit-0001",
        "evidence_spans": [
            {
                "evidence_id": "evidence-0001",
                "unit_id": "unit-0001",
                "quote": "Alice met Bob in Paris",
                "start_hint": "line 1",
                "end_hint": "line 1",
            }
        ],
        "entity_mentions": [
            {
                "mention_id": "entity-0001",
                "surface": "Charlie",
                "canonical_name": "Charles Brown",
                "kind": "person",
                "summary": "A character not in the evidence paragraph",
                "alias_candidate_of": None,
                "alias_confidence": None,
                "alias_rationale": None,
                "evidence_span_ids": ["evidence-0001"],
            }
        ],
        "location_mentions": [],
        "atom_mentions": [],
        "time_expressions": [],
        "thread_candidates": [],
        "warnings": [],
    }
    report = validate_extraction_quality(data, text, expected_unit_id="unit-0001")
    assert "surface_not_in_evidence_context" in [
        issue.code for issue in report.issues
    ]


