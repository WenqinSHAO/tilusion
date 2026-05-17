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
)
from tilusion.book_reader import build_book_index, extract_unit_text
from tilusion.extraction_quality import relocate_evidence_quote, validate_extraction_quality
from tilusion.extraction_pipeline import (
    build_overview_composition,
    build_segment_extraction_composition,
    generated_prompt_part,
    run_chained_extraction,
    run_segment_extraction_pass,
)


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
    assert envelope.prompt_version == "segment-extraction-v0.4"
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
    assert PROMPT_VERSION == "segment-extraction-v0.4"
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
    assert "Do not remove, normalize, or rewrite note markers" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "at least one cited evidence quote should contain that exact surface string" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Do not cite a paragraph opening as evidence" in LOCAL_BUNDLE_SYSTEM_PROMPT
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


def test_prompt_composition_tracks_static_and_generated_parts() -> None:
    generated = generated_prompt_part(
        "validation-feedback",
        role="deterministic_validation_feedback",
        content="No unresolved evidence.",
        generated_by="validate_extraction_quality",
        metadata={"issue_count": 0},
    )

    prompt = build_segment_extraction_composition([generated])

    assert prompt.parts[0].role == "static_task_contract"
    assert prompt.parts[1].part_id == "validation-feedback"
    assert prompt.parts[1].generated_by == "validate_extraction_quality"
    assert "prompt-part:segment-extraction-contract" in prompt.content
    assert "No unresolved evidence." in prompt.content
    assert len(prompt.content_hash) == 64


def test_overview_composition_tracks_static_prompt_contract() -> None:
    prompt = build_overview_composition()

    assert prompt.composition_id == "overview-segmentation-v0.1"
    assert prompt.parts[0].part_id == "overview-segmentation-contract"
    assert "coarse, source-grounded navigation overview" in prompt.content
    assert "start_quote" in prompt.content
    assert "end_quote" in prompt.content


def test_segment_extraction_pass_caches_intermediate_artifacts(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    cache_dir = tmp_path / "pass-cache"

    first = run_segment_extraction_pass(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
    )
    second = run_segment_extraction_pass(
        book,
        "unit-0001",
        backend=FailingBackend(),
        cache_dir=cache_dir,
    )

    assert not first.cache_hit
    assert second.cache_hit
    assert first.cache_key == second.cache_key
    assert second.result.data["unit_id"] == "unit-0001"
    for path in first.artifact_paths.values():
        assert Path(path).exists()
    assert "validation_report" in first.artifact_paths
    assert first.validation_report.to_dict()["evidence_location_summary"]["exact"] == 1


def test_chained_extraction_runs_overview_then_segment_passes(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    cache_dir = tmp_path / "chain-cache"

    record = run_chained_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
    )

    assert record.unit_id == "unit-0001"
    assert record.source_length["chars"] == len("Chapter 1\nAlice left home.\n")
    assert record.overview.data["overview_segments"]
    assert len(record.resolved_segments) == 1
    assert record.resolved_segments[0].to_dict()["length"]["chars"] == len("Chapter 1\nAlice left home.")
    assert len(record.segment_passes) == 1
    assert record.validation_report["passed"]
    assert record.validation_report["segment_lengths"][0]["segment_id"] == "overview-segment-0001"
    assert record.repair_hints["ready_for_llm_repair"] is False
    for path in record.artifact_paths.values():
        assert Path(path).exists()
    assert Path(record.overview.artifact_paths["result"]).exists()
    assert Path(record.segment_passes[0].artifact_paths["result"]).exists()


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
        "event_mentions": [
            {
                "event_id": "event-0001",
                "summary": "少年随母归宁。",
                "participant_mention_ids": [],
                "location_mention_ids": [],
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
        "event_mentions": [
            {
                "event_id": "event-0001",
                "summary": "沈复受业于赵省斋。",
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


def run_empty_context():
    return ExtractionContext(frontier="unit-0001")


class FailingBackend:
    model_identity = "mock-local-bundle-v0"

    def complete_json(self, system_prompt, user_payload):
        raise AssertionError("cache hit should not call backend")
