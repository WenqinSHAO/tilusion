from __future__ import annotations

from pathlib import Path

import pytest

from tilusion.extraction import (
    DEFAULT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEEPSEEK_CONTEXT_TOKENS,
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    ExtractionContext,
    ExtractionBudgetError,
    ExtractionError,
    LOCAL_BUNDLE_SYSTEM_PROMPT,
    MockExtractionBackend,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_cache_key,
    build_local_bundle_prompt,
    check_extraction_budget,
    estimate_deepseek_tokens,
    parse_json_response,
    run_local_bundle_extraction,
    validate_extraction_quality,
)
from tilusion.book_reader import build_book_index, extract_unit_text


def test_local_bundle_prompt_has_cache_relevant_structure(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    index = build_book_index(book)
    unit = index.units[1]
    text = extract_unit_text(book, unit)

    envelope = build_local_bundle_prompt(unit, text, context=run_empty_context())
    key = build_cache_key(envelope, MockExtractionBackend().model_identity)
    model_payload = envelope.to_model_payload()

    assert envelope.task == "local_bundle_extraction"
    assert envelope.prompt_version == "segment-extraction-v0.3"
    assert envelope.schema_version == "segment-extraction-v0.2"
    assert envelope.unit["id"] == "unit-0001"
    assert envelope.unit["source_range"]["kind"] == "txt-span"
    assert model_payload == {
        "unit": envelope.unit,
        "prior_context": envelope.context,
        "text": envelope.text,
    }
    assert "task" not in model_payload
    assert "prompt_version" not in model_payload
    assert "schema_version" not in model_payload
    assert len(key) == 64
    assert DEFAULT_MODEL == "deepseek-v4-flash"
    assert DEFAULT_MAX_TOKENS == 32768
    assert DEEPSEEK_CONTEXT_TOKENS == 1_000_000
    assert DEEPSEEK_MAX_OUTPUT_TOKENS == 384_000
    assert PROMPT_VERSION == "segment-extraction-v0.3"
    assert SCHEMA_VERSION == "segment-extraction-v0.2"


def test_local_bundle_system_prompt_is_reusable_segment_extraction_contract() -> None:
    assert "stateless extraction worker" not in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "local bundle" not in LOCAL_BUNDLE_SYSTEM_PROMPT.lower()
    assert "You extract grounded narrative structure from one provided text segment" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "The larger tool helps humans" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Minimum JSON shape" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "New entities and locations may appear in this segment" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "alias_candidate_of" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "IDs are temporary and response-local only" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Evidence quotes must be exact substrings" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Do not use the entire input segment as one evidence span" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "The pipeline will reconstruct original-file locators" in LOCAL_BUNDLE_SYSTEM_PROMPT


def test_local_bundle_extraction_uses_mock_backend_and_cache(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    result = run_local_bundle_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
    )
    cached = run_local_bundle_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
    )

    assert result.data["unit_id"] == "unit-0001"
    assert result.data["evidence_spans"][0]["quote"] == "Chapter 1"
    assert cached.to_dict() == result.to_dict()
    assert list(cache_dir.glob("*.json"))


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
        "event_mentions": [
            {
                "event_id": "event-0001",
                "summary": "Alice met Bob in Paris.",
                "participant_mention_ids": ["entity-0001"],
                "location_mention_ids": ["location-0001"],
                "time_expression_ids": [],
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
                "quote": "x" * 321,
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
        "event_mentions": [
            {
                "event_id": "event-0001",
                "summary": "Bad references.",
                "participant_mention_ids": ["entity-missing"],
                "location_mention_ids": ["location-missing"],
                "time_expression_ids": ["time-missing"],
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
    assert "evidence_quote_not_found" in codes
    assert "evidence_quote_ambiguous" in codes
    assert "evidence_quote_too_long" in codes
    assert "unresolved_evidence_ref" in codes
    assert "missing_evidence_refs" in codes
    assert "unresolved_object_ref" in codes
    assert "surface_not_in_cited_evidence" in codes
    assert any(
        issue.code == "evidence_quote_not_found" and issue.source_windows
        for issue in report.issues
    )
    assert repair_payload["quality_summary"]["truncated"] is True
    assert repair_payload["repair_instructions"]
    assert len(repair_payload["issues"]) == 3


def run_empty_context():
    return ExtractionContext(frontier="unit-0001")
