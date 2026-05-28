from __future__ import annotations

import json
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
    LocalBundleResult,
    MockExtractionBackend,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_cache_key,
    build_local_bundle_prompt,
    check_extraction_budget,
    estimate_deepseek_tokens,
    parse_json_response,
    run_local_bundle_extraction,
    sha256_text,
)
from tilusion.book_reader import build_book_index, extract_unit_text
from tilusion.extraction_quality import relocate_evidence_quote, validate_extraction_quality
from tilusion.extraction_pipeline import (
    ExtractionPassRecord,
    _build_segment_quality_overview,
    _derive_unresolved_detail,
    _detect_timeline_cycles,
    _dominant_issue_codes,
    book_context_cache_metadata,
    build_overview_composition,
    build_pass_cache_key,
    build_segment_extraction_composition,
    build_unit_finalization_composition,
    build_unit_repair_composition,
    build_unit_repair_payload,
    build_unit_timeline_composition,
    build_unit_timeline_payload,
    build_unit_timeline_repair_composition,
    generated_prompt_part,
    refresh_chain_validation_cache,
    run_chained_extraction,
    run_segment_extraction_pass,
    run_unit_finalization_pass,
    run_unit_repair_pass,
    run_unit_timeline_pass,
    run_unit_timeline_repair_pass,
    validate_unit_timeline_result,
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
    assert envelope.prompt_version == "segment-extraction-v0.7"
    assert envelope.schema_version == "segment-extraction-v0.4"
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
    assert DEFAULT_MAX_TOKENS == 326_400
    assert DEEPSEEK_CONTEXT_TOKENS == 850_000
    assert DEEPSEEK_MAX_OUTPUT_TOKENS == 326_400
    assert PROMPT_VERSION == "segment-extraction-v0.7"
    assert SCHEMA_VERSION == "segment-extraction-v0.4"


def test_local_bundle_system_prompt_is_reusable_segment_extraction_contract() -> None:
    assert "stateless extraction worker" not in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "local bundle" not in LOCAL_BUNDLE_SYSTEM_PROMPT.lower()
    assert "You extract grounded narrative structure from one provided text segment" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "The larger tool helps humans" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Minimum JSON shape" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "New entities, locations, atoms, and threads may appear that have no prior record" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "alias_candidate_of" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "IDs are temporary and response-local only" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Evidence quotes must be exact substrings" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Do not remove, normalize, or rewrite note markers" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "at least one cited evidence quote should contain that exact surface string" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Do not cite a paragraph opening as evidence" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "Do not use the entire input segment as one evidence span" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "The pipeline will reconstruct original-file locators" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "atom_kind" in LOCAL_BUNDLE_SYSTEM_PROMPT
    assert "narrative_event" in LOCAL_BUNDLE_SYSTEM_PROMPT


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

    assert prompt.composition_id == "overview-segmentation-v0.2"
    assert prompt.parts[0].part_id == "overview-segmentation-contract"
    assert "coarse, source-grounded navigation overview" in prompt.content
    assert "start_quote" in prompt.content
    assert "end_quote" in prompt.content
    assert "region" in prompt.content
    assert "Do not pre-extract entities" in prompt.content


def test_unit_finalization_composition_tracks_static_prompt_contract() -> None:
    prompt = build_unit_finalization_composition()

    assert prompt.composition_id == "unit-finalization-v0.3"
    assert prompt.parts[0].part_id == "unit-finalization-contract"
    assert "You finalize source-grounded extraction" in prompt.content
    assert "atom_records" in prompt.content
    assert "Do not construct a final timeline" in prompt.content


def test_unit_repair_composition_shares_finalization_prefix() -> None:
    final = build_unit_finalization_composition()
    repair = build_unit_repair_composition()

    assert repair.composition_id == "unit-repair-v0.1"
    assert len(repair.parts) == 2
    assert repair.parts[0].part_id == "unit-finalization-contract"
    assert repair.parts[0].content == final.parts[0].content
    assert repair.parts[1].part_id == "unit-repair-instructions"
    assert "repairing a completed unit finalization" in repair.parts[1].content


def test_unit_repair_payload_includes_repair_targets() -> None:
    manifest = {
        "unit_id": "unit-0001",
        "source_length": {"chars": 100},
        "resolved_segments": [],
        "validation_report": {"segment_pass_count": 1, "resolved_segment_count": 1},
        "repair_hints": {},
        "segment_passes": [],
    }
    finalization_data = {
        "unresolved_items": [{"code": "test_issue", "path": "entity-0001"}],
        "quality_notes": {
            "summary": "Test finalization.",
            "blocking_concerns": ["Missing evidence for entity-0001"],
        },
        "warnings": ["Some quotes are ambiguous"],
    }
    payload = build_unit_repair_payload(manifest, finalization_data)

    assert payload["task"] == "unit_finalization"
    assert payload["unit_id"] == "unit-0001"
    assert "repair_targets" in payload
    assert payload["repair_targets"]["unresolved_items"] == finalization_data["unresolved_items"]
    assert payload["repair_targets"]["blocking_concerns"] == ["Missing evidence for entity-0001"]
    assert payload["repair_targets"]["warnings"] == ["Some quotes are ambiguous"]


def test_unit_repair_pass_completes_and_caches(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n" * 20, encoding="utf-8")
    chain_dir = tmp_path / "chain"
    record = run_chained_extraction(
        book, "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=chain_dir,
    )
    final = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
    )
    assert not final.cache_hit

    final_pass_dir = str(Path(final.artifact_paths["manifest"]).parent)
    repair = run_unit_repair_pass(
        final_pass_dir,
        backend=MockExtractionBackend(),
    )
    assert not repair.cache_hit
    assert repair.validation_report["passed"]
    assert repair.data["unit_id"] == "unit-0001"
    for path in repair.artifact_paths.values():
        assert Path(path).exists()

    cached_repair = run_unit_repair_pass(
        final_pass_dir,
        backend=MockExtractionBackend(),
    )
    assert cached_repair.cache_hit


def test_unit_timeline_composition_extends_repair() -> None:
    repair = build_unit_repair_composition()
    timeline = build_unit_timeline_composition()

    assert timeline.composition_id == "unit-timeline-v0.4"
    assert len(timeline.parts) == 3
    assert timeline.parts[0].part_id == "unit-finalization-contract"
    assert timeline.parts[0].content == repair.parts[0].content
    assert timeline.parts[1].part_id == "unit-repair-instructions"
    assert timeline.parts[1].content == repair.parts[1].content
    assert timeline.parts[2].part_id == "unit-timeline-instructions"
    assert "partially-ordered timelines" in timeline.parts[2].content


def test_unit_timeline_payload_includes_unit_records() -> None:
    manifest = {
        "unit_id": "unit-0001",
        "source_length": {"chars": 100},
        "resolved_segments": [],
        "validation_report": {"segment_pass_count": 1, "resolved_segment_count": 1},
        "repair_hints": {},
        "segment_passes": [],
    }
    repaired = {
        "entity_records": [{"entity_id": "unit-entity-0001"}],
        "location_records": [{"location_id": "unit-location-0001"}],
        "atom_records": [{"atom_id": "unit-atom-0001"}, {"atom_id": "unit-atom-0002"}],
        "thread_records": [{"thread_id": "unit-thread-0001"}],
    }
    payload = build_unit_timeline_payload(manifest, repaired)

    assert payload["task"] == "unit_timeline"
    assert payload["unit_id"] == "unit-0001"
    assert "unit_records" in payload
    assert len(payload["unit_records"]["atom_records"]) == 2
    assert len(payload["unit_records"]["entity_records"]) == 1


def test_unit_timeline_pass_completes_and_caches(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n" * 20, encoding="utf-8")
    record = run_chained_extraction(
        book, "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=tmp_path / "chain",
    )
    final = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
    )
    repair = run_unit_repair_pass(
        str(Path(final.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )

    timeline = run_unit_timeline_pass(
        str(Path(repair.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )
    assert not timeline.cache_hit
    assert timeline.validation_report["passed"]
    assert timeline.data["unit_id"] == "unit-0001"
    assert len(timeline.data["timelines"]) >= 1
    assert "unit-timeline-" in timeline.data["timelines"][0]["timeline_id"]
    for path in timeline.artifact_paths.values():
        assert Path(path).exists()

    cached = run_unit_timeline_pass(
        str(Path(repair.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )
    assert cached.cache_hit


def test_validate_timeline_result_detects_missing_timelines() -> None:
    report = validate_unit_timeline_result(
        {"unit_id": "unit-0001"},
        expected_unit_id="unit-0001",
    )
    assert not report["passed"]
    assert any(i["code"] == "missing_required_field" for i in report["issues"])


def test_validate_timeline_result_detects_event_mismatch() -> None:
    data = {
        "unit_id": "unit-0001",
        "timelines": [
            {
                "timeline_id": "unit-timeline-0001",
                "summary": "Test",
                "confidence": "high",
                "ordered_atoms": [
                    {"atom_id": "unit-atom-0001", "before_atoms": []}
                ],
            }
        ],
        "atom_records": [
            {"atom_id": "unit-atom-0001"},
            {"atom_id": "unit-atom-0002"},
        ],
    }
    report = validate_unit_timeline_result(data, expected_unit_id="unit-0001")
    missing = [i for i in report["issues"] if i["code"] == "atoms_missing_from_timelines"]
    assert len(missing) == 1
    assert missing[0]["severity"] == "warning"


def test_validate_timeline_result_detects_self_loop() -> None:
    data = {
        "unit_id": "unit-0001",
        "timelines": [
            {
                "timeline_id": "unit-timeline-0001",
                "summary": "Test",
                "confidence": "high",
                "ordered_atoms": [
                    {"atom_id": "unit-atom-0001", "before_atoms": ["unit-atom-0001"]}
                ],
            }
        ],
        "atom_records": [{"atom_id": "unit-atom-0001"}],
    }
    report = validate_unit_timeline_result(data, expected_unit_id="unit-0001")
    assert any(i["code"] == "timeline_self_loop" for i in report["issues"])


def test_validate_timeline_result_detects_phantom_ref() -> None:
    data = {
        "unit_id": "unit-0001",
        "timelines": [
            {
                "timeline_id": "unit-timeline-0001",
                "summary": "Test",
                "confidence": "high",
                "ordered_atoms": [
                    {"atom_id": "unit-atom-0001", "before_atoms": ["unit-atom-0999"]}
                ],
            }
        ],
        "atom_records": [{"atom_id": "unit-atom-0001"}],
    }
    report = validate_unit_timeline_result(data, expected_unit_id="unit-0001")
    assert any(i["code"] == "timeline_phantom_ref" for i in report["issues"])


def test_validate_timeline_result_allows_shared_events_as_intersection() -> None:
    data = {
        "unit_id": "unit-0001",
        "timelines": [
            {
                "timeline_id": "unit-timeline-0001",
                "summary": "Timeline 1",
                "confidence": "high",
                "ordered_atoms": [
                    {"atom_id": "unit-atom-0001", "before_atoms": []}
                ],
            },
            {
                "timeline_id": "unit-timeline-0002",
                "summary": "Timeline 2",
                "confidence": "medium",
                "ordered_atoms": [
                    {"atom_id": "unit-atom-0001", "before_atoms": []}
                ],
            },
        ],
        "atom_records": [{"atom_id": "unit-atom-0001"}],
    }
    report = validate_unit_timeline_result(data, expected_unit_id="unit-0001")
    shared = [i for i in report["issues"] if i["code"] == "timeline_shared_atom"]
    assert len(shared) == 1
    assert shared[0]["severity"] == "warning"


def test_detect_timeline_cycles_finds_cycle() -> None:
    # A -> B -> C -> A (cycle)
    events = [
        {"atom_id": "A", "before_atoms": ["B"]},
        {"atom_id": "B", "before_atoms": ["C"]},
        {"atom_id": "C", "before_atoms": ["A"]},
    ]
    cycles = _detect_timeline_cycles(events)
    assert len(cycles) >= 1

    # A -> B, B -> C, A -> C (no cycle)
    events_dag = [
        {"atom_id": "A", "before_atoms": ["B", "C"]},
        {"atom_id": "B", "before_atoms": ["C"]},
        {"atom_id": "C"},
    ]
    cycles_dag = _detect_timeline_cycles(events_dag)
    assert len(cycles_dag) == 0


def test_unit_timeline_repair_composition_extends_timeline() -> None:
    timeline = build_unit_timeline_composition()
    repair = build_unit_timeline_repair_composition()

    assert repair.composition_id == "unit-timeline-repair-v0.1"
    assert len(repair.parts) == 4
    assert repair.parts[0].content == timeline.parts[0].content
    assert repair.parts[1].content == timeline.parts[1].content
    assert repair.parts[2].content == timeline.parts[2].content
    assert repair.parts[3].part_id == "unit-timeline-repair-instructions"
    assert "repairing a completed timeline construction" in repair.parts[3].content


def test_unit_timeline_repair_pass_completes_and_caches(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n" * 20, encoding="utf-8")
    record = run_chained_extraction(
        book, "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=tmp_path / "chain",
    )
    final = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
    )
    repair = run_unit_repair_pass(
        str(Path(final.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )
    timeline = run_unit_timeline_pass(
        str(Path(repair.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )

    trepair = run_unit_timeline_repair_pass(
        str(Path(timeline.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )
    assert not trepair.cache_hit
    assert trepair.validation_report["passed"]
    assert trepair.data["unit_id"] == "unit-0001"
    for path in trepair.artifact_paths.values():
        assert Path(path).exists()

    cached = run_unit_timeline_repair_pass(
        str(Path(timeline.artifact_paths["manifest"]).parent),
        backend=MockExtractionBackend(),
    )
    assert cached.cache_hit


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
    assert "validated_result" in first.artifact_paths
    assert first.validation_report.to_dict()["evidence_location_summary"]["exact"] == 1
    validated_result = Path(first.artifact_paths["validated_result"]).read_text(encoding="utf-8")
    assert "source_location" in validated_result


def _context_pack(enabled: bool, pack_id: str = "context-pack-test") -> dict:
    return {
        "book_id": "book-test",
        "context_pack_id": pack_id,
        "context_pack_hash": pack_id.replace("context-pack-", "hash-"),
        "selection_policy": "cross-unit-context-v0.1",
        "context_injection": {"enabled": enabled},
        "context": {
            "entities": [
                {
                    "entity_id": "unit-0001:unit-entity-0001",
                    "canonical_name": "Alice",
                    "matched_surfaces": [],
                    "match_count": 1,
                }
            ],
            "locations": [],
            "active_threads": [],
            "recent_events": [],
            "landmark_events": [],
            "time_anchors": [],
            "arc_summaries": [],
        },
    }


def test_pass_cache_key_is_book_context_aware_only_when_prompt_injected() -> None:
    prompt = build_segment_extraction_composition()
    payload = {"unit": {"id": "unit-0001"}, "prior_context": {}, "text": "Alice"}
    base = build_pass_cache_key(
        pass_name="segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity="mock",
    )
    disabled = build_pass_cache_key(
        pass_name="segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity="mock",
        cache_context=book_context_cache_metadata(_context_pack(False)),
    )
    enabled = build_pass_cache_key(
        pass_name="segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity="mock",
        cache_context=book_context_cache_metadata(_context_pack(True)),
    )

    assert disabled == base
    assert enabled != base


def test_chained_extraction_can_inject_book_context_and_isolate_cache(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    cache_dir = tmp_path / "chain-cache"
    pack = _context_pack(True, "context-pack-one")

    first = run_chained_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
        book_context_pack=pack,
    )
    second = run_chained_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
        book_context_pack=pack,
    )
    third = run_chained_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
        book_context_pack=_context_pack(True, "context-pack-two"),
    )

    assert first.cache_dir == second.cache_dir
    assert first.cache_dir != third.cache_dir
    assert second.segment_passes[0].cache_hit is True
    assert third.segment_passes[0].cache_hit is False
    prompt_composition = json.loads(
        Path(first.segment_passes[0].artifact_paths["prompt_composition"]).read_text(encoding="utf-8")
    )
    assert prompt_composition["parts"][-1]["part_id"] == "book-scope-context"
    assert first.segment_passes[0].result.context_hash != sha256_text("{}")


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
    assert record.resolved_segments[0].to_dict()["length"]["chars"] == len("Chapter 1\nAlice left home.\n")
    assert len(record.segment_passes) == 1
    assert record.validation_report["passed"]
    assert record.validation_report["segment_lengths"][0]["segment_id"] == "overview-segment-0001"
    overview = record.validation_report["segment_quality_overview"]
    assert overview["total_overview_segments"] == 1
    assert overview["resolved_segments"] == 1
    assert overview["unresolved_segments"] == 0
    assert overview["unresolved_reasons"] == []
    assert overview["dominant_issues"] == []
    assert len(overview["per_segment"]) == 1
    seg = overview["per_segment"][0]
    assert seg["segment_id"] == "overview-segment-0001"
    assert seg["passed"] is True
    assert seg["evidence"]["exact"] == 1
    assert record.repair_hints["ready_for_llm_repair"] is False
    assert record.repair_hints["non_actionable_warnings"]["total"] == 0
    for path in record.artifact_paths.values():
        assert Path(path).exists()
    assert Path(record.overview.artifact_paths["result"]).exists()
    assert Path(record.segment_passes[0].artifact_paths["result"]).exists()
    refreshed = refresh_chain_validation_cache(record.cache_dir)
    assert refreshed["overview"]["cache_hit"] is True
    assert refreshed["segment_passes"][0]["cache_hit"] is True
    assert refreshed["segment_passes"][0]["artifact_paths"]["validated_result"]
    refreshed_overview = refreshed["validation_report"]["segment_quality_overview"]
    assert refreshed_overview["total_overview_segments"] == 1
    assert refreshed_overview["resolved_segments"] == 1

    final = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
    )
    cached_final = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
    )
    assert not final.cache_hit
    assert cached_final.cache_hit
    assert final.validation_report["passed"]
    assert final.data["unit_id"] == "unit-0001"
    for path in final.artifact_paths.values():
        assert Path(path).exists()


def test_unit_finalization_cache_is_book_context_aware(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    cache_dir = tmp_path / "chain-cache"
    pack = _context_pack(True, "context-pack-one")
    record = run_chained_extraction(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=cache_dir,
        book_context_pack=pack,
    )

    first = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
        book_context_pack=pack,
    )
    second = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
        book_context_pack=pack,
    )
    third = run_unit_finalization_pass(
        record.cache_dir,
        backend=MockExtractionBackend(),
        book_context_pack=_context_pack(True, "context-pack-two"),
    )

    assert second.cache_hit is True
    assert third.cache_hit is False
    assert first.cache_key != third.cache_key
    prompt_composition = json.loads(
        Path(first.artifact_paths["prompt_composition"]).read_text(encoding="utf-8")
    )
    assert prompt_composition["parts"][-1]["part_id"] == "book-scope-context"


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


def test_segment_quality_overview_reports_unresolved_segments_and_dominant_issues() -> None:
    segment_reports = [
        {
            "unit_id": "seg-0001",
            "passed": True,
            "issue_count": 2,
            "issues": [
                {"code": "surface_not_in_evidence_context", "severity": "warning"},
                {"code": "surface_not_in_evidence_context", "severity": "warning"},
            ],
            "evidence_location_summary": {"exact": 4, "relocated": 2, "ambiguous": 0, "missing": 0},
        },
        {
            "unit_id": "seg-0002",
            "passed": False,
            "issue_count": 2,
            "issues": [
                {"code": "evidence_quote_missing", "severity": "error"},
                {"code": "surface_not_in_evidence_context", "severity": "warning"},
            ],
            "evidence_location_summary": {"exact": 1, "relocated": 0, "ambiguous": 0, "missing": 1},
        },
    ]
    segment_lengths = [
        {"segment_id": "seg-0001", "chars": 800, "start": 0, "end": 800},
        {"segment_id": "seg-0002", "chars": 2400, "start": 800, "end": 3200},
    ]
    repairs = [
        {
            "segment_id": "seg-0003",
            "code": "segment_span_unresolved",
            "start_location": {"status": "ambiguous", "candidate_count": 4},
            "end_location": {"status": "exact", "start": 5000, "end": 5050},
        }
    ]

    overview = _build_segment_quality_overview(
        total_overview_segments=3,
        overview_repairs=repairs,
        segment_reports=segment_reports,
        segment_lengths=segment_lengths,
    )

    assert overview["total_overview_segments"] == 3
    assert overview["resolved_segments"] == 2
    assert overview["unresolved_segments"] == 1
    assert len(overview["unresolved_reasons"]) == 1
    assert overview["unresolved_reasons"][0]["segment_id"] == "seg-0003"
    assert "ambiguous" in overview["unresolved_reasons"][0]["detail"]
    assert overview["dominant_issues"] == ["surface_not_in_evidence_context", "evidence_quote_missing"]
    assert len(overview["per_segment"]) == 2
    assert overview["per_segment"][0]["issue_codes"] == {"surface_not_in_evidence_context": 2}
    assert overview["per_segment"][1]["issue_codes"] == {
        "evidence_quote_missing": 1,
        "surface_not_in_evidence_context": 1,
    }
    assert overview["per_segment"][1]["passed"] is False
    assert overview["per_segment"][1]["evidence"]["missing"] == 1


def test_derive_unresolved_detail_reports_reason() -> None:
    missing = _derive_unresolved_detail(
        {
            "code": "segment_span_unresolved",
            "start_location": {"status": "missing", "candidate_count": 0},
            "end_location": {"status": "exact", "start": 100, "end": 200},
        }
    )
    assert "unrelocatable" in missing

    ambiguous = _derive_unresolved_detail(
        {
            "code": "segment_span_unresolved",
            "start_location": {"status": "ambiguous", "candidate_count": 3},
            "end_location": {"status": "exact", "start": 100, "end": 200},
        }
    )
    assert "ambiguous" in ambiguous
    assert "3 candidates" in ambiguous

    inverted = _derive_unresolved_detail(
        {
            "code": "segment_span_unresolved",
            "start_location": {"status": "exact", "start": 200, "end": 300},
            "end_location": {"status": "exact", "start": 50, "end": 150},
        }
    )
    assert "inverted" in inverted


def test_dominant_issue_codes_orders_by_frequency() -> None:
    reports = [
        {"issues": [{"code": "a"}, {"code": "b"}, {"code": "a"}]},
        {"issues": [{"code": "c"}, {"code": "a"}]},
    ]
    assert _dominant_issue_codes(reports) == ["a", "b", "c"]


def test_segment_cache_map_finds_results_by_text_hash(tmp_path: Path) -> None:
    from tilusion.extraction_pipeline import _build_segment_cache_map

    segments_dir = tmp_path / "segments"
    seg_a_dir = segments_dir / "seg-a" / "cache-key-a"
    seg_a_dir.mkdir(parents=True)
    seg_a_dir.joinpath("result.json").write_text(
        json.dumps({"source_text_hash": "abc123", "unit_id": "seg-a"}),
        encoding="utf-8",
    )
    seg_b_dir = segments_dir / "seg-b" / "cache-key-b"
    seg_b_dir.mkdir(parents=True)
    seg_b_dir.joinpath("result.json").write_text(
        json.dumps({"source_text_hash": "def456", "unit_id": "seg-b"}),
        encoding="utf-8",
    )

    cache_map = _build_segment_cache_map(segments_dir)

    assert len(cache_map) == 2
    assert "abc123" in cache_map
    assert "def456" in cache_map
    assert cache_map["abc123"].name == "result.json"


def test_segment_cache_map_skips_non_dirs_and_missing_results(tmp_path: Path) -> None:
    from tilusion.extraction_pipeline import _build_segment_cache_map

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir(parents=True)
    (segments_dir / "empty-dir").mkdir()
    (segments_dir / "not-a-dir.txt").write_text("", encoding="utf-8")

    cache_map = _build_segment_cache_map(segments_dir)
    assert len(cache_map) == 0


def test_chain_repair_hints_includes_non_actionable_warnings() -> None:
    from tilusion.extraction_pipeline import _build_non_actionable_warning_summary
    from tilusion.extraction import MockExtractionBackend
    import copy

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
                "summary": "Unsupported by cited evidence",
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
    result = LocalBundleResult(
        task="segment_extraction",
        prompt_version="test",
        schema_version="test",
        unit_id="unit-0001",
        source_text_hash=sha256_text(text),
        context_hash=sha256_text("{}"),
        model="mock",
        raw_response="{}",
        data=data,
    )
    record = ExtractionPassRecord(
        pass_name="segment-extraction",
        cache_key="test-key",
        cache_dir=str(Path("/tmp/test")),
        cache_hit=False,
        result=result,
        validation_report=report,
        artifact_paths={},
    )
    summary = _build_non_actionable_warning_summary([record])
    assert summary["total"] == 1
    assert summary["by_code"]["surface_not_in_evidence_context"] == 1
    assert summary["affected_segments"] == ["unit-0001"]


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


def run_empty_context():
    return ExtractionContext(frontier="unit-0001")


class FailingBackend:
    model_identity = "mock-local-bundle-v0"

    def complete_json(self, system_prompt, user_payload):
        raise AssertionError("cache hit should not call backend")
