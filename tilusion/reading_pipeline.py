from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extraction import (
    LLMBackend,
    MockExtractionBackend,
    parse_json_response,
)
from .extraction_pipeline import (
    ResolvedOverviewSegment,
    build_pass_cache_key,
    pass_artifact_paths,
    resolve_overview_segments,
    run_overview_segmentation_pass,
)
from .reading_payloads import (
    build_per_segment_extraction_payload,
    build_unit_logical_grouping_payload,
    flatten_and_stabilize_segment_results,
)
from .reading_prompts import (
    build_per_segment_extraction_composition,
    build_unit_logical_grouping_composition,
)
from .reading_schema import READING_UNIT_SCHEMA_VERSION
from .source_blocks import split_source_blocks
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


def _raise_on_validation_errors(pass_name: str, report: ReadingValidationReport) -> None:
    if report.passed:
        return
    issue_summary = "; ".join(
        f"{issue.code} at {issue.path}: {issue.message}"
        for issue in report.issues
        if issue.severity == "error"
    )
    raise ValueError(f"{pass_name} validation failed: {issue_summary}")


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
    blocks = user_payload.get("source_blocks", [])
    first_block_id = blocks[0]["block_id"] if blocks else f"{segment_id}-block-0000"

    concepts = [
        {
            "concept_id": "concept-0001",
            "surface": "mock surface",
            "concept_type": "other",
            "source_block_refs": [first_block_id],
            "canonical_name": "",
            "summary": f"Mock concept from {segment_id}.",
            "aliases": [],
            "observed_surfaces": ["mock surface"],
            "facets": [],
            "uncertainty": [],
            "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"},
        }
    ] if blocks else []
    atomic_items = [
        {
            "item_id": "item-0001",
            "item_type": "observation",
            "summary": f"Mock item from {segment_id}.",
            "source_block_refs": [first_block_id],
            "concept_refs": ["concept-0001"],
            "temporal_attributes": [],
            "attributes": {},
            "uncertainty": [],
            "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"},
        }
    ] if blocks else []

    return {
        "unit_id": unit_id,
        "segment_id": segment_id,
        "concepts": concepts,
        "atomic_items": atomic_items,
        "warnings": ["mock per-segment extraction: placeholder records"],
    }


def mock_unit_logical_grouping_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    concepts = user_payload.get("concepts", [])
    items = user_payload.get("atomic_items", [])
    concept_ids = [c["concept_id"] for c in concepts if isinstance(c, dict)]
    item_ids = [it["item_id"] for it in items if isinstance(it, dict)]

    concept_deltas: list[dict[str, Any]] = []
    logical_groups: list[dict[str, Any]] = []

    if items:
        logical_groups.append(
            {
                "group_id": "group-0001",
                "group_type": "other",
                "summary": "Mock logical group from all items.",
                "item_refs": item_ids[:],
                "concept_refs": concept_ids[:],
                "graph": {},
                "uncertainty": [],
                "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            }
        )

    return {
        "unit_id": unit_id,
        "concept_deltas": concept_deltas,
        "logical_groups": logical_groups,
        "unresolved_items": [],
        "warnings": ["mock unit logical grouping: placeholder records"],
    }



# ── Mock backend ─────────────────────────────────────────────────────────────


class MockReadingBackend:
    model_identity = "mock-reading-v0"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        task = user_payload.get("task", "")

        if task == "per_segment_extraction":
            return json.dumps(mock_per_segment_extraction_response(user_payload), ensure_ascii=False)
        if task == "unit_logical_grouping":
            return json.dumps(mock_unit_logical_grouping_response(user_payload), ensure_ascii=False)

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
    unit_text: str | None = None,
) -> ReadingPassRecord:
    """Run the reading per-segment extraction pass on one segment.

    Splits the segment text into deterministic source blocks, builds a
    v0.3 per-segment payload with inline block markers, calls the LLM,
    and validates the returned concepts and atomic_items against the
    authoritative source blocks.
    """
    # Deterministic source block splitting
    segment_text = segment.text
    block_unit_text = unit_text if unit_text is not None else segment_text
    block_unit_offset = segment.start if unit_text is not None else 0
    blocks, _block_metrics = split_source_blocks(
        segment_text,
        segment_id=segment.segment_id,
        unit_id=unit_id,
        unit_text=block_unit_text,
        unit_offset=block_unit_offset,
    )

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
        text=segment_text,
        source_blocks=blocks,
        segment_offset=block_unit_offset,
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

    # Build a v0.3 validation subject with authoritative source blocks
    validation_subject = {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": {"unit_text": block_unit_text} if unit_text is not None else {},
        "source_blocks": [b.to_dict() for b in blocks],
        "concepts": data.get("concepts", []),
        "atomic_items": data.get("atomic_items", []),
        "logical_groups": [],
        "unresolved_items": [],
        "validation": {},
        "context_metadata": {},
    }
    validation_report = validate_extraction_unit_package(validation_subject)
    _raise_on_validation_errors("per-segment-extraction", validation_report)

    enriched_data = {
        **data,
        "source_blocks": [b.to_dict() for b in blocks],
    }

    record = ReadingPassRecord(
        pass_name="per-segment-extraction",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=enriched_data,
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
            data=record.data,
            validation_report=validation_report,
            record=record,
        )

    return record


# ── Pass: unit logical grouping ────────────────────────────────────────────────


def _apply_concept_deltas(
    concepts: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
    *,
    unit_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Apply concept deltas to the concept list.

    Returns ``(updated_concepts, remap_dict)`` where *remap_dict* maps
    old concept IDs to new concept IDs for downstream ref rewriting.
    """
    if not deltas:
        return concepts, {}

    concept_by_id = {c["concept_id"]: c for c in concepts}
    remap: dict[str, str] = {}
    next_index = len(concepts)

    for delta in deltas:
        delta_type = delta.get("delta_type", "")
        target_refs = delta.get("target_refs", [])
        changes = delta.get("changes", {})

        if delta_type == "merge":
            known_refs = [ref for ref in target_refs if ref in concept_by_id]
            if not known_refs:
                continue
            primary_id = known_refs[0]
            primary = concept_by_id[primary_id]
            for ref in known_refs:
                remap[ref] = primary_id

            for field in ("canonical_name", "summary", "surface", "concept_type"):
                if changes.get(field):
                    primary[field] = changes[field]

            for field in ("aliases", "observed_surfaces", "source_block_refs", "facets", "uncertainty"):
                seen = set()
                values: list[Any] = []
                for value in _as_list(primary.get(field)):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
                for ref in known_refs[1:]:
                    for value in _as_list(concept_by_id[ref].get(field)):
                        if value not in seen:
                            seen.add(value)
                            values.append(value)
                for value in _as_list(changes.get(field)):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
                if values:
                    primary[field] = values

            merged_from = []
            for ref in known_refs:
                merged_from.extend(_as_list(concept_by_id[ref].get("merged_from")) or [ref])
            primary["merged_from"] = list(dict.fromkeys(merged_from))

            for ref in known_refs[1:]:
                del concept_by_id[ref]

        elif delta_type == "split":
            original_id = target_refs[0] if target_refs else ""
            split_into = changes.get("split_into", [])
            if original_id in concept_by_id:
                del concept_by_id[original_id]
            for i, new_concept in enumerate(split_into):
                next_index += 1
                new_id = f"concept-{next_index:04d}"
                new_concept["concept_id"] = new_id
                new_concept.setdefault("provenance", {"grounding": "synthesis", "created_by": "llm_inferred"})
                concept_by_id[new_id] = new_concept
                if i == 0 and original_id:
                    remap[original_id] = new_id

        elif delta_type == "refine":
            for ref in target_refs:
                if ref in concept_by_id:
                    c = concept_by_id[ref]
                    for field in ("canonical_name", "summary", "aliases", "observed_surfaces", "facets", "uncertainty"):
                        if field in changes:
                            c[field] = changes[field]

        elif delta_type == "reclassify":
            for ref in target_refs:
                if ref in concept_by_id and "concept_type" in changes:
                    concept_by_id[ref]["concept_type"] = changes["concept_type"]

    return list(concept_by_id.values()), remap


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def run_unit_logical_grouping_pass(
    *,
    unit_id: str,
    unit_text: str,
    source: dict[str, Any],
    segments: list[ResolvedOverviewSegment],
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    unresolved_items: list[dict[str, Any]],
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool = True,
    context: dict[str, Any] | None = None,
) -> ReadingPassRecord:
    """Run the unit-level logical grouping + concept delta pass.

    The LLM reviews merged concepts, emits optional corrections as
    concept deltas, and builds logical groups from atomic items.
    """
    prompt = build_unit_logical_grouping_composition()
    payload = build_unit_logical_grouping_payload(
        unit_id=unit_id,
        unit_text=unit_text,
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
        concepts=concepts,
        atomic_items=atomic_items,
        unresolved_items=unresolved_items,
        context=context,
    )

    cache_key = build_pass_cache_key(
        pass_name="unit-logical-grouping",
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

    # Apply concept deltas, then build validation subject
    deltas = data.get("concept_deltas", [])
    updated_concepts, concept_remap = _apply_concept_deltas(
        concepts, deltas, unit_id=unit_id
    )
    groups = data.get("logical_groups", [])

    # Remap item concept_refs for any concept IDs that were merged/renamed
    updated_items: list[dict[str, Any]] = []
    for item in atomic_items:
        updated = dict(item)
        updated["concept_refs"] = [
            concept_remap.get(ref, ref) for ref in item.get("concept_refs", [])
        ]
        updated_items.append(updated)

    # Remap concept_refs in logical groups
    updated_groups: list[dict[str, Any]] = []
    for group in groups:
        updated = dict(group)
        updated["concept_refs"] = [
            concept_remap.get(ref, ref) for ref in group.get("concept_refs", [])
        ]
        updated_groups.append(updated)

    validation_subject = {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": {**source, "unit_text": unit_text},
        "source_blocks": source_blocks,
        "concepts": updated_concepts,
        "atomic_items": updated_items,
        "logical_groups": updated_groups,
        "unresolved_items": data.get("unresolved_items", []),
        "validation": {},
        "context_metadata": {},
    }
    validation_report = validate_extraction_unit_package(validation_subject)
    _raise_on_validation_errors("unit-logical-grouping", validation_report)

    record = ReadingPassRecord(
        pass_name="unit-logical-grouping",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data={
            **data,
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": unit_id,
            "source": source,
            "source_blocks": source_blocks,
            "concepts": updated_concepts,
            "atomic_items": updated_items,
            "logical_groups": updated_groups,
            "validation": validation_report.to_dict(),
            "context_metadata": {"context_injection": context is not None},
        },
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
            data=record.data,
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
    """Run the full reading pipeline: overview → per-segment → logical grouping.

    Reuses the existing overview/segmentation pass, then runs deterministic
    source block splitting, per-segment concept/item extraction, deterministic
    concept merging, and unit-level logical grouping with optional concept deltas.
    """
    from .book_reader import build_book_index, extract_unit_text as unit_text

    total_start = time.monotonic()
    llm = backend or MockReadingBackend()
    overview_backend = backend or MockExtractionBackend()
    cache_root = Path(cache_dir)
    pass_summaries: dict[str, dict[str, Any]] = {}
    TOTAL_STEPS = 3

    book_path = Path(book_path)
    index = build_book_index(book_path)
    unit = index.unit_map()[unit_id]
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
        segments = resolve_overview_segments(overview_record.data, text)
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
                unit_text=text,
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

    # ── Stabilize: merge concepts and reindex items ──
    stabilized = flatten_and_stabilize_segment_results(
        [r.data for r in segment_records], unit_id=unit_id
    )

    # ── Step 3: Unit logical grouping ──
    step = 3
    t0 = time.monotonic()
    try:
        grouping_record = run_unit_logical_grouping_pass(
            unit_id=unit_id,
            unit_text=text,
            source=source,
            segments=segments,
            source_blocks=stabilized["source_blocks"],
            concepts=stabilized["concepts"],
            atomic_items=stabilized["atomic_items"],
            unresolved_items=stabilized.get("unresolved_items", []),
            backend=llm,
            cache_dir=cache_root / "logical_grouping",
            use_cache=use_cache,
            context=context,
        )
        pass_summaries["unit_logical_grouping"] = {
            "cache_key": grouping_record.cache_key,
            "cache_dir": grouping_record.cache_dir,
            "cache_hit": grouping_record.cache_hit,
            "artifact_paths": grouping_record.artifact_paths,
            "elapsed_ms": _elapsed_ms(t0),
        }
        _log_progress(step, TOTAL_STEPS, "Unit logical grouping", "OK", _elapsed_ms(t0))
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Unit logical grouping", "FAILED", _elapsed_ms(t0))
        raise

    final_data = {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": source,
        "source_blocks": grouping_record.data["source_blocks"],
        "concepts": grouping_record.data["concepts"],
        "atomic_items": grouping_record.data["atomic_items"],
        "logical_groups": grouping_record.data["logical_groups"],
        "unresolved_items": grouping_record.data.get("unresolved_items", []),
        "validation": grouping_record.validation_report.to_dict(),
        "context_metadata": {"context_injection": context is not None},
    }
    final_validation_report = validate_extraction_unit_package(final_data)
    _raise_on_validation_errors("reading-unit-package", final_validation_report)
    final_data["validation"] = final_validation_report.to_dict()
    final_validation = final_validation_report.to_dict()

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

    package = dict(data)
    package.setdefault("schema_version", READING_UNIT_SCHEMA_VERSION)
    package["unit_id"] = unit_id
    package["source"] = data.get("source") or source
    package["passes"] = passes
    package["validation"] = validation
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(package_path)
