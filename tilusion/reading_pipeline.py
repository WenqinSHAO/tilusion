from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .extraction import (
    LLMBackend,
    MockExtractionBackend,
    parse_json_response,
    sha256_json,
)
from .extraction_pipeline import (
    ResolvedOverviewSegment,
    build_pass_cache_key,
    pass_artifact_paths,
    resolve_overview_segments,
    run_overview_segmentation_pass,
    write_pass_artifacts,
)
from .reading_payloads import (
    build_per_segment_extraction_payload,
    build_unit_reading_finalization_payload,
    flatten_segment_results,
)
from .reading_prompts import (
    build_per_segment_extraction_composition,
    build_unit_reading_finalization_composition,
)
from .reading_schema import READING_UNIT_SCHEMA_VERSION
from .reading_validation import (
    ReadingValidationReport,
    validate_extraction_unit_package,
)

# re-export for convenience
__all__ = [
    "MockReadingBackend",
    "ReadingPassRecord",
    "ReadingPipelineRecord",
    "run_per_segment_extraction_pass",
    "run_reading_finalization_pass",
    "run_reading_pipeline",
    "write_reading_unit_package",
]


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _last_nonempty_line(text: str) -> str:
    result = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            result = stripped
    return result


def _elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _log_progress(step: int, total: int, description: str, status: str, elapsed_ms: int) -> None:
    print(f"  [{step}/{total}] {description}: {status} ({elapsed_ms}ms)", file=sys.stderr)


# ── Pass record ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ReadingPassRecord:
    pass_name: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    raw_response: str
    data: dict[str, Any]
    validation_report: ReadingValidationReport
    artifact_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_name": self.pass_name,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "data": self.data,
            "validation_report": self.validation_report.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class ReadingPipelineRecord:
    unit_id: str
    elapsed_ms: int
    unit_package_path: str
    passes: dict[str, dict[str, Any]]
    data: dict[str, Any]
    validation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "elapsed_ms": self.elapsed_ms,
            "unit_package_path": self.unit_package_path,
            "passes": self.passes,
            "data": self.data,
            "validation": self.validation,
        }


# ── Mock response functions ──────────────────────────────────────────────────


def mock_per_segment_extraction_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    segment = user_payload.get("segment", {})
    segment_id = segment.get("segment_id", "seg-0001")
    text = user_payload.get("text", "")
    first = _first_nonempty_line(text)

    span_id = f"span-{segment_id}-0001"
    block_id = f"block-{segment_id}-0001"
    mention_id = f"mention-{segment_id}-0001"
    group_id = f"group-{segment_id}-0001"
    link_id = f"link-{segment_id}-0001"

    return {
        "unit_id": unit_id,
        "segment_id": segment_id,
        "source_spans": [
            {
                "span_id": span_id,
                "unit_id": unit_id,
                "source_range": {"kind": "segment-local-quote", "quote": first},
                "quote": first,
                "provenance": {"created_by": "llm", "pass": "per_segment_extraction"},
            }
        ],
        "source_blocks": [
            {
                "block_id": block_id,
                "block_type": "paragraph",
                "span_refs": [span_id],
                "source_order": 1,
                "confidence": "medium",
            }
        ],
        "concept_mentions": [
            {
                "mention_id": mention_id,
                "surface": first[:40] if first else "",
                "concept_type": "other",
                "local_summary": f"Mock concept from {segment_id}.",
                "source_block_refs": [block_id],
                "source_span_refs": [span_id],
                "confidence": "low",
                "facets": [],
                "uncertainty": [],
            }
        ],
        "logical_groups": [
            {
                "group_id": group_id,
                "group_type": "other",
                "summary": f"Mock group covering {segment_id}.",
                "source_block_refs": [block_id],
                "concept_refs": [mention_id],
                "source_order_hints": {"first_block": block_id, "last_block": block_id},
                "confidence": "low",
                "uncertainty": [],
                "provenance": {"grounding": "source_grounded", "created_by": "llm"},
            }
        ],
        "links": [
            {
                "link_id": link_id,
                "source_ref": group_id,
                "target_ref": mention_id,
                "link_type": "mentions",
                "evidence_block_refs": [block_id],
                "confidence": "low",
                "rationale": "Mock link.",
                "grounding": "source_grounded",
            }
        ],
        "warnings": ["mock per-segment extraction: placeholder records"],
    }


def mock_unit_reading_finalization_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    source = user_payload.get("source", {})
    source_spans = user_payload.get("source_spans", [])
    source_blocks = user_payload.get("source_blocks", [])
    concept_mentions = user_payload.get("concept_mentions", [])
    logical_groups = user_payload.get("logical_groups", [])
    links = user_payload.get("links", [])
    context_metadata = user_payload.get("context_metadata", {})

    def _reindex(records: list[dict[str, Any]], prefix: str, id_field: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i, rec in enumerate(records):
            updated = dict(rec)
            updated[id_field] = f"{prefix}{i + 1:04d}"
            if "confidence" not in updated:
                updated["confidence"] = "low"
            result.append(updated)
        return result

    final_spans = _reindex(source_spans, "span-", "span_id")
    final_blocks = _reindex(source_blocks, "block-", "block_id")
    final_concepts = _reindex(concept_mentions, "mention-", "mention_id")
    final_groups = _reindex(logical_groups, "group-", "group_id")

    span_map = _id_map(source_spans, final_spans, "span_id")
    block_map = _id_map(source_blocks, final_blocks, "block_id")
    mention_map = _id_map(concept_mentions, final_concepts, "mention_id")
    group_map = _id_map(logical_groups, final_groups, "group_id")

    final_links: list[dict[str, Any]] = []
    for i, link in enumerate(links):
        updated = dict(link)
        updated["link_id"] = f"link-{i + 1:04d}"
        updated["source_ref"] = group_map.get(link.get("source_ref", ""), link.get("source_ref", ""))
        updated["target_ref"] = (
            group_map.get(link.get("target_ref", ""))
            or mention_map.get(link.get("target_ref", ""))
            or block_map.get(link.get("target_ref", ""))
            or link.get("target_ref", "")
        )
        updated["evidence_block_refs"] = [block_map.get(ref, ref) for ref in link.get("evidence_block_refs", [])]
        if "confidence" not in updated:
            updated["confidence"] = "low"
        final_links.append(updated)

    for group in final_groups:
        group["concept_refs"] = [mention_map.get(ref, ref) for ref in group.get("concept_refs", [])]
        group["source_block_refs"] = [block_map.get(ref, ref) for ref in group.get("source_block_refs", [])]
        hints = group.get("source_order_hints", {})
        if isinstance(hints, dict):
            for key in ("first_block", "last_block"):
                if key in hints:
                    hints[key] = block_map.get(hints[key], hints[key])

    for concept in final_concepts:
        concept["source_block_refs"] = [block_map.get(ref, ref) for ref in concept.get("source_block_refs", [])]
        concept["source_span_refs"] = [span_map.get(ref, ref) for ref in concept.get("source_span_refs", [])]

    for block in final_blocks:
        block["span_refs"] = [span_map.get(ref, ref) for ref in block.get("span_refs", [])]

    return {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": source,
        "source_spans": final_spans,
        "source_blocks": final_blocks,
        "concept_mentions": final_concepts,
        "logical_groups": final_groups,
        "links": final_links,
        "derived_views": [],
        "unresolved_items": [],
        "validation": {"mock": True, "warnings": ["mock finalization: records re-indexed to unit-level IDs"]},
        "context_metadata": context_metadata,
    }


def _id_map(
    source_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
    id_field: str,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for src, tgt in zip(source_records, target_records):
        src_id = src.get(id_field)
        tgt_id = tgt.get(id_field)
        if isinstance(src_id, str) and isinstance(tgt_id, str):
            mapping[src_id] = tgt_id
    return mapping


# ── Mock backend ─────────────────────────────────────────────────────────────


class MockReadingBackend:
    model_identity = "mock-reading-v0"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        task = user_payload.get("task", "")

        if task == "per_segment_extraction":
            return json.dumps(mock_per_segment_extraction_response(user_payload), ensure_ascii=False)
        if task == "unit_reading_finalization":
            return json.dumps(mock_unit_reading_finalization_response(user_payload), ensure_ascii=False)

        raise ValueError(f"MockReadingBackend: unknown task {task!r}")


# ── Pass: per-segment extraction ─────────────────────────────────────────────


def run_per_segment_extraction_pass(
    *,
    unit_id: str,
    segment: ResolvedOverviewSegment,
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool = True,
    context: dict[str, Any] | None = None,
) -> ReadingPassRecord:
    """Run the reading per-segment extraction pass on one segment.

    Returns source spans, source blocks, concept mentions, logical groups,
    and links — all in a single LLM call.
    """
    prompt = build_per_segment_extraction_composition()
    payload = build_per_segment_extraction_payload(
        unit_id=unit_id,
        segment={
            "segment_id": segment.segment_id,
            "title": segment.title,
            "summary": segment.summary,
            "source_range": {
                "start": segment.start,
                "end": segment.end,
            },
        },
        text=segment.text,
        context=context,
    )

    cache_key = build_pass_cache_key(
        pass_name="per-segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
        data = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        raw_response = backend.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)

    # Build a temporary unit-package-like dict for validation
    validation_subject = {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": {},
        "source_spans": data.get("source_spans", []),
        "source_blocks": data.get("source_blocks", []),
        "concept_mentions": data.get("concept_mentions", []),
        "logical_groups": data.get("logical_groups", []),
        "links": data.get("links", []),
        "derived_views": [],
        "unresolved_items": [],
        "validation": {},
        "context_metadata": {},
    }
    validation_report = validate_extraction_unit_package(validation_subject)

    record = ReadingPassRecord(
        pass_name="per-segment-extraction",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
    )

    if use_cache:
        _write_reading_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=data,
            validation_report=validation_report,
            record=record,
        )

    return record


# ── Pass: unit reading finalization ──────────────────────────────────────────


def run_reading_finalization_pass(
    *,
    unit_id: str,
    source: dict[str, Any],
    segments: list[ResolvedOverviewSegment],
    segment_records: list[ReadingPassRecord],
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool = True,
    context: dict[str, Any] | None = None,
) -> ReadingPassRecord:
    """Run the reading unit finalization pass.

    Deduplicates and stabilizes records from all per-segment passes into
    a single ExtractionUnitPackage.
    """
    # Flatten all segment results
    flat = flatten_segment_results([r.data for r in segment_records])

    prompt = build_unit_reading_finalization_composition()
    payload = build_unit_reading_finalization_payload(
        unit_id=unit_id,
        source=source,
        segments=[
            {
                "segment_id": s.segment_id,
                "title": s.title,
                "summary": s.summary,
                "source_range": {"start": s.start, "end": s.end},
            }
            for s in segments
        ],
        source_spans=flat["source_spans"],
        source_blocks=flat["source_blocks"],
        concept_mentions=flat["concept_mentions"],
        logical_groups=flat["logical_groups"],
        links=flat["links"],
        validation_reports=[r.validation_report.to_dict() for r in segment_records],
        context_metadata={"context_injection": context is not None},
    )

    cache_key = build_pass_cache_key(
        pass_name="unit-reading-finalization",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
        data = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        raw_response = backend.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)

    validation_report = validate_extraction_unit_package(data)

    record = ReadingPassRecord(
        pass_name="unit-reading-finalization",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
    )

    if use_cache:
        _write_reading_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=data,
            validation_report=validation_report,
            record=record,
        )

    return record


# ── Orchestrator ─────────────────────────────────────────────────────────────


def run_reading_pipeline(
    book_path: str | Path,
    unit_id: str,
    *,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache/reading_passes",
    use_cache: bool = True,
    context: dict[str, Any] | None = None,
) -> ReadingPipelineRecord:
    """Run the full reading pipeline: overview → per-segment extraction → finalization.

    Reuses the existing overview/segmentation pass, then runs the new
    reading-model per-segment extraction and unit finalization passes.
    """
    from .extraction_pipeline import build_book_index, unit_text

    total_start = time.monotonic()
    llm = backend or MockReadingBackend()
    overview_backend = backend or MockExtractionBackend()
    cache_root = Path(cache_dir)
    pass_summaries: dict[str, dict[str, Any]] = {}
    TOTAL_STEPS = 3

    book_path = Path(book_path)
    index = build_book_index(book_path)
    unit = index.units_by_id[unit_id]
    text = unit_text(unit, book_path)
    source = {
        "book_path": str(book_path),
        "book_title": index.title or "",
        "unit_id": unit_id,
        "unit_label": unit.label,
        "unit_kind": unit.kind,
    }

    # ── Step 1: Overview segmentation (reuse existing) ──
    step = 1
    t0 = time.monotonic()
    try:
        overview_record = run_overview_segmentation_pass(
            unit=unit,
            text=text,
            backend=overview_backend,
            cache_dir=cache_root / "overview",
            use_cache=use_cache,
        )
        segments = resolve_overview_segments(overview_record, unit, text)
        pass_summaries["overview_segmentation"] = {
            "cache_key": overview_record.cache_key,
            "cache_dir": overview_record.cache_dir,
            "cache_hit": overview_record.cache_hit,
            "artifact_paths": overview_record.artifact_paths,
            "elapsed_ms": _elapsed_ms(t0),
        }
        _log_progress(step, TOTAL_STEPS, "Overview segmentation", "OK", _elapsed_ms(t0))
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Overview segmentation", "FAILED", _elapsed_ms(t0))
        raise

    # ── Step 2: Per-segment reading extraction ──
    step = 2
    t0 = time.monotonic()
    segment_records: list[ReadingPassRecord] = []
    try:
        for seg in segments:
            seg_record = run_per_segment_extraction_pass(
                unit_id=unit_id,
                segment=seg,
                backend=llm,
                cache_dir=cache_root / "per_segment",
                use_cache=use_cache,
                context=context,
            )
            segment_records.append(seg_record)
        pass_summaries["per_segment_extraction"] = {
            "segment_count": len(segments),
            "elapsed_ms": _elapsed_ms(t0),
        }
        _log_progress(step, TOTAL_STEPS, "Per-segment extraction", f"{len(segments)} segments OK", _elapsed_ms(t0))
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Per-segment extraction", "FAILED", _elapsed_ms(t0))
        raise

    # ── Step 3: Unit reading finalization ──
    step = 3
    t0 = time.monotonic()
    try:
        finalization_record = run_reading_finalization_pass(
            unit_id=unit_id,
            source=source,
            segments=segments,
            segment_records=segment_records,
            backend=llm,
            cache_dir=cache_root / "finalization",
            use_cache=use_cache,
            context=context,
        )
        pass_summaries["unit_reading_finalization"] = {
            "cache_key": finalization_record.cache_key,
            "cache_dir": finalization_record.cache_dir,
            "cache_hit": finalization_record.cache_hit,
            "artifact_paths": finalization_record.artifact_paths,
            "elapsed_ms": _elapsed_ms(t0),
        }
        _log_progress(step, TOTAL_STEPS, "Unit finalization", "OK", _elapsed_ms(t0))
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Unit finalization", "FAILED", _elapsed_ms(t0))
        raise

    final_data = finalization_record.data
    final_validation = finalization_record.validation_report.to_dict()

    # ── Write unit package ──
    package_path = write_reading_unit_package(
        unit_id=unit_id,
        source=source,
        data=final_data,
        validation=final_validation,
        passes=pass_summaries,
        cache_root=cache_root,
    )

    total_elapsed = _elapsed_ms(total_start)
    print(f"Reading pipeline complete: {package_path} ({total_elapsed}ms)", file=sys.stderr)

    return ReadingPipelineRecord(
        unit_id=unit_id,
        elapsed_ms=total_elapsed,
        unit_package_path=package_path,
        passes=pass_summaries,
        data=final_data,
        validation=final_validation,
    )


# ── Artifact writers ─────────────────────────────────────────────────────────


def _write_reading_pass_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: Any,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: ReadingValidationReport,
    record: ReadingPassRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)

    if hasattr(prompt, "to_dict"):
        Path(paths["prompt_composition"]).write_text(
            json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    Path(paths["system_prompt"]).write_text(
        prompt.content if hasattr(prompt, "content") else str(prompt), encoding="utf-8"
    )
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(paths["validated_result"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")


def write_reading_unit_package(
    *,
    unit_id: str,
    source: dict[str, Any],
    data: dict[str, Any],
    validation: dict[str, Any],
    passes: dict[str, dict[str, Any]],
    cache_root: Path,
) -> str:
    """Write the ExtractionUnitPackage to disk."""
    package_dir = cache_root / "units" / unit_id
    package_dir.mkdir(parents=True, exist_ok=True)
    package_path = package_dir / "unit_package.json"

    package = {
        "schema_version": data.get("schema_version", READING_UNIT_SCHEMA_VERSION),
        "unit_id": unit_id,
        "source": source,
        "passes": passes,
        "data": data,
        "validation": validation,
    }
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(package_path)
