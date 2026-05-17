from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import resources
import json
from pathlib import Path
import sys
import time
from typing import Any

from .book_reader import build_book_index, extract_unit_text
from .extraction import (
    DEFAULT_MAX_TOKENS,
    LLMBackend,
    LOCAL_BUNDLE_SYSTEM_PROMPT,
    PROMPT_RESOURCE,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractionContext,
    LocalBundleResult,
    MockExtractionBackend,
    build_local_bundle_prompt,
    check_extraction_budget,
    parse_json_response,
    result_from_json,
    sha256_json,
    sha256_text,
    validate_local_bundle,
)
from .extraction_quality import (
    EvidenceLocation,
    ExtractionQualityReport,
    relocate_evidence_quote,
    validate_extraction_quality,
    is_llm_actionable_issue,
)


OVERVIEW_PROMPT_VERSION = "overview-segmentation-v0.1"
OVERVIEW_PROMPT_RESOURCE = "overview_segmentation_v0.1.md"
UNIT_FINALIZATION_PROMPT_VERSION = "unit-finalization-v0.1"
UNIT_FINALIZATION_PROMPT_RESOURCE = "unit_finalization_v0.1.md"
UNIT_REPAIR_PROMPT_VERSION = "unit-repair-v0.1"
UNIT_REPAIR_PROMPT_RESOURCE = "unit_repair_v0.1.md"
UNIT_TIMELINE_PROMPT_VERSION = "unit-timeline-v0.1"
UNIT_TIMELINE_PROMPT_RESOURCE = "unit_timeline_v0.1.md"
UNIT_TIMELINE_REPAIR_PROMPT_VERSION = "unit-timeline-repair-v0.1"
UNIT_TIMELINE_REPAIR_PROMPT_RESOURCE = "unit_timeline_repair_v0.1.md"


@dataclass(slots=True)
class PromptPart:
    part_id: str
    role: str
    source: str
    content: str
    generated_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["content_hash"] = self.content_hash
        return data


@dataclass(slots=True)
class PromptComposition:
    composition_id: str
    parts: list[PromptPart]

    @property
    def content(self) -> str:
        if len(self.parts) == 1:
            return self.parts[0].content
        sections = []
        for part in self.parts:
            sections.append(f"<!-- prompt-part:{part.part_id} role:{part.role} -->\n{part.content}")
        return "\n\n".join(sections)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "content_hash": self.content_hash,
            "parts": [part.to_dict() for part in self.parts],
        }


@dataclass(slots=True)
class ExtractionPassRecord:
    pass_name: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    result: LocalBundleResult
    validation_report: ExtractionQualityReport
    artifact_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_name": self.pass_name,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "result": self.result.to_dict(),
            "validation_report": self.validation_report.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class JsonPassRecord:
    pass_name: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    raw_response: str
    data: dict[str, Any]
    validation_report: dict[str, Any]
    artifact_paths: dict[str, str]
    anchor_locations: dict[str, EvidenceLocation] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_name": self.pass_name,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "raw_response": self.raw_response,
            "data": self.data,
            "validation_report": self.validation_report,
        }


@dataclass(slots=True)
class ResolvedOverviewSegment:
    segment_id: str
    title: str
    summary: str
    start: int
    end: int
    text: str
    source: dict[str, Any]
    start_location: EvidenceLocation
    end_location: EvidenceLocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "title": self.title,
            "summary": self.summary,
            "start": self.start,
            "end": self.end,
            "length": text_length_stats(self.text),
            "source": self.source,
            "start_location": self.start_location.to_dict(),
            "end_location": self.end_location.to_dict(),
            "text_hash": sha256_text(self.text),
            "char_count": len(self.text),
        }


@dataclass(slots=True)
class ChainedExtractionRecord:
    unit_id: str
    cache_dir: str
    source_length: dict[str, int]
    overview: JsonPassRecord
    resolved_segments: list[ResolvedOverviewSegment]
    segment_passes: list[ExtractionPassRecord]
    validation_report: dict[str, Any]
    repair_hints: dict[str, Any]
    artifact_paths: dict[str, str]
    overview_result_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "cache_dir": self.cache_dir,
            "source_length": self.source_length,
            "artifact_paths": self.artifact_paths,
            "overview": self.overview.to_dict(),
            "overview_result_hash": self.overview_result_hash,
            "resolved_segments": [segment.to_dict() for segment in self.resolved_segments],
            "segment_passes": [record.to_dict() for record in self.segment_passes],
            "validation_report": self.validation_report,
            "repair_hints": self.repair_hints,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class UnitFinalizationRecord:
    unit_id: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    raw_response: str
    data: dict[str, Any]
    validation_report: dict[str, Any]
    artifact_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "raw_response": self.raw_response,
            "data": self.data,
            "validation_report": self.validation_report,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class UnitTimelineRecord:
    unit_id: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    raw_response: str
    data: dict[str, Any]
    validation_report: dict[str, Any]
    artifact_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "raw_response": self.raw_response,
            "data": self.data,
            "validation_report": self.validation_report,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class RunAllRecord:
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

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def run_segment_extraction_pass(
    book_path: str | Path,
    unit_id: str,
    *,
    context: ExtractionContext | None = None,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache/extraction_passes",
    use_cache: bool = True,
    generated_prompt_parts: list[PromptPart] | None = None,
) -> ExtractionPassRecord:
    index = build_book_index(book_path)
    unit = index.unit_map().get(unit_id)
    if unit is None:
        raise ValueError(f"unknown unit_id: {unit_id}")
    text = extract_unit_text(book_path, unit)
    extraction_context = context or ExtractionContext(frontier=unit_id)
    llm = backend or MockExtractionBackend()
    envelope = build_local_bundle_prompt(unit, text, extraction_context)
    prompt = build_segment_extraction_composition(generated_prompt_parts or [])
    payload = envelope.to_model_payload()
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )

    cache_key = build_pass_cache_key(
        pass_name="segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity=llm.model_identity,
    )
    pass_dir = Path(cache_dir) / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        result = result_from_json(result_path.read_text(encoding="utf-8"))
    else:
        raw_response = llm.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
        validate_local_bundle(data)
        result = LocalBundleResult(
            task=envelope.task,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            unit_id=unit.id,
            source_text_hash=sha256_text(text),
            context_hash=sha256_json(extraction_context.to_dict()),
            model=llm.model_identity,
            raw_response=raw_response,
            data=data,
        )

    validation_report = validate_extraction_quality(result.data, text, expected_unit_id=unit.id)
    record = ExtractionPassRecord(
        pass_name="segment-extraction",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        result=result,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=result.raw_response,
            result=result,
            validation_report=validation_report,
            record=record,
        )
    return record


def _build_segment_cache_map(segments_dir: Path) -> dict[str, Path]:
    cache_map: dict[str, Path] = {}
    if not segments_dir.exists():
        return cache_map
    for segment_dir in segments_dir.iterdir():
        if not segment_dir.is_dir():
            continue
        for pass_dir in segment_dir.iterdir():
            if not pass_dir.is_dir():
                continue
            result_path = pass_dir / "result.json"
            if not result_path.exists():
                continue
            try:
                result_data = json.loads(result_path.read_text(encoding="utf-8"))
                text_hash = result_data.get("source_text_hash")
                if text_hash and text_hash not in cache_map:
                    cache_map[text_hash] = result_path
            except (json.JSONDecodeError, OSError):
                continue
    return cache_map


def run_chained_extraction(
    book_path: str | Path,
    unit_id: str,
    *,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache/extraction_chains",
    use_cache: bool = True,
) -> ChainedExtractionRecord:
    index = build_book_index(book_path)
    unit = index.unit_map().get(unit_id)
    if unit is None:
        raise ValueError(f"unknown unit_id: {unit_id}")
    text = extract_unit_text(book_path, unit)
    llm = backend or MockExtractionBackend()
    root_dir = Path(cache_dir) / chain_cache_key(unit_id=unit_id, text=text, model_identity=llm.model_identity)
    overview = run_overview_segmentation_pass(
        unit=unit,
        text=text,
        backend=llm,
        cache_dir=root_dir / "overview",
        use_cache=use_cache,
    )
    resolved_segments, overview_repairs = resolve_overview_segments(
        overview.data, text, anchor_locations=overview.anchor_locations
    )
    segments_dir = root_dir / "segments"
    cache_map = _build_segment_cache_map(segments_dir) if use_cache else {}
    segment_passes = []
    for segment in resolved_segments:
        segment_context = ExtractionContext(
            frontier=segment.segment_id,
            confirmed_entities=[
                {"surface": value, "source": "overview"}
                for value in segment.source.get("key_entities", [])
            ],
            confirmed_locations=[
                {"surface": value, "source": "overview"}
                for value in segment.source.get("key_locations", [])
            ],
            recent_events=[
                {"summary": value, "source": "overview"}
                for value in segment.source.get("event_hints", [])
            ],
            temporal_constraints=[
                {"surface": value, "source": "overview"}
                for value in segment.source.get("time_hints", [])
            ],
        )
        generated_parts = [
            generated_prompt_part(
                f"{segment.segment_id}-overview-hints",
                role="overview_extraction_hints",
                content=json.dumps(segment_hint_payload(segment), ensure_ascii=False, indent=2),
                generated_by="overview_segmentation",
                metadata={"segment_id": segment.segment_id},
            )
        ]
        segment_text_hash = sha256_text(segment.text)
        cached_result_path = cache_map.get(segment_text_hash)
        cached_result = None
        if cached_result_path and cached_result_path.exists():
            cached_result = result_from_json(cached_result_path.read_text(encoding="utf-8"))
        segment_passes.append(
            run_text_segment_extraction_pass(
                parent_unit=unit,
                segment=segment,
                context=segment_context,
                backend=llm,
                cache_dir=segments_dir,
                use_cache=use_cache,
                generated_prompt_parts=generated_parts,
                cached_result=cached_result,
            )
        )
    validation_report = build_chain_validation_report(
        overview, resolved_segments, segment_passes, overview_repairs=overview_repairs
    )
    repair_hints = build_chain_repair_hints(overview_repairs, segment_passes, validation_report)
    paths = chain_artifact_paths(root_dir)
    record = ChainedExtractionRecord(
        unit_id=unit_id,
        cache_dir=str(root_dir),
        source_length=text_length_stats(text),
        overview=overview,
        resolved_segments=resolved_segments,
        segment_passes=segment_passes,
        validation_report=validation_report,
        repair_hints=repair_hints,
        artifact_paths=paths,
        overview_result_hash=sha256_json(overview.data),
    )
    if use_cache:
        write_chain_artifacts(root_dir, paths, record)
    return record


def refresh_chain_validation_cache(chain_dir: str | Path) -> dict[str, Any]:
    root_dir = Path(chain_dir)
    manifest_path = root_dir / "chain_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing chain manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    overview = refresh_cached_overview_record(manifest["overview"])
    segment_passes = [
        refresh_cached_segment_record(record)
        for record in manifest.get("segment_passes", [])
    ]
    resolved_segments = manifest.get("resolved_segments", [])
    total_overview = len(overview.data.get("overview_segments") or [])
    cached_hints = manifest.get("repair_hints") if isinstance(manifest.get("repair_hints"), dict) else {}
    cached_overview_repairs = cached_hints.get("overview_repairs", [])
    validation_report = build_cached_chain_validation_report(
        overview.validation_report,
        resolved_segments,
        [record.validation_report.to_dict() for record in segment_passes],
        total_overview_segments=total_overview,
        overview_repairs=cached_overview_repairs,
    )
    repair_hints = build_chain_repair_hints(cached_overview_repairs, segment_passes, validation_report)
    paths = chain_artifact_paths(root_dir)
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["repair_hints"]).write_text(
        json.dumps(repair_hints, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    refreshed_manifest = {
        **manifest,
        "overview": overview.to_dict(),
        "validation_report": validation_report,
        "repair_hints": repair_hints,
        "segment_passes": [record.to_dict() for record in segment_passes],
    }
    Path(paths["manifest"]).write_text(
        json.dumps(refreshed_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return refreshed_manifest


def run_unit_finalization_pass(
    chain_dir: str | Path,
    *,
    backend: LLMBackend | None = None,
    use_cache: bool = True,
) -> UnitFinalizationRecord:
    root_dir = Path(chain_dir)
    manifest_path = root_dir / "chain_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing chain manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    llm = backend or MockExtractionBackend()
    prompt = build_unit_finalization_composition()
    payload = build_unit_finalization_payload(manifest)
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )
    cache_key = build_pass_cache_key(
        pass_name="unit-finalization",
        prompt=prompt,
        user_payload=payload,
        model_identity=llm.model_identity,
    )
    pass_dir = root_dir / "unit_finalization" / cache_key
    paths = unit_finalization_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()
    if cache_hit:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
    else:
        raw_response = llm.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
    validation_report = validate_unit_finalization_result(data, expected_unit_id=manifest["unit_id"])
    record = UnitFinalizationRecord(
        unit_id=manifest["unit_id"],
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_unit_finalization_artifacts(
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


def run_unit_repair_pass(
    finalization_pass_dir: str | Path,
    *,
    backend: LLMBackend | None = None,
    use_cache: bool = True,
) -> UnitFinalizationRecord:
    pass_dir = Path(finalization_pass_dir)
    result_path = pass_dir / "result.json"
    if not result_path.exists():
        raise ValueError(f"missing finalization result: {result_path}")
    finalization_data = json.loads(result_path.read_text(encoding="utf-8"))
    chain_dir = pass_dir.parent.parent
    manifest_path = chain_dir / "chain_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing chain manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    llm = backend or MockExtractionBackend()
    prompt = build_unit_repair_composition()
    payload = build_unit_repair_payload(manifest, finalization_data)
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )
    cache_key = build_pass_cache_key(
        pass_name="unit-repair",
        prompt=prompt,
        user_payload=payload,
        model_identity=llm.model_identity,
    )
    repair_pass_dir = chain_dir / "unit_repair" / cache_key
    paths = unit_repair_artifact_paths(repair_pass_dir)
    repair_result_path = Path(paths["result"])
    cache_hit = use_cache and repair_result_path.exists()
    if cache_hit:
        data = json.loads(repair_result_path.read_text(encoding="utf-8"))
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
    else:
        raw_response = llm.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
    validation_report = validate_unit_finalization_result(
        data, expected_unit_id=manifest["unit_id"]
    )
    record = UnitFinalizationRecord(
        unit_id=manifest["unit_id"],
        cache_key=cache_key,
        cache_dir=str(repair_pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_unit_repair_artifacts(
            pass_dir=repair_pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=data,
            validation_report=validation_report,
            record=record,
        )
    return record


def run_unit_timeline_pass(
    repair_pass_dir: str | Path,
    *,
    backend: LLMBackend | None = None,
    use_cache: bool = True,
) -> UnitTimelineRecord:
    pass_dir = Path(repair_pass_dir)
    result_path = pass_dir / "result.json"
    if not result_path.exists():
        raise ValueError(f"missing repair result: {result_path}")
    repaired_data = json.loads(result_path.read_text(encoding="utf-8"))
    chain_dir = pass_dir.parent.parent
    manifest_path = chain_dir / "chain_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing chain manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    llm = backend or MockExtractionBackend()
    prompt = build_unit_timeline_composition()
    payload = build_unit_timeline_payload(manifest, repaired_data)
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )
    cache_key = build_pass_cache_key(
        pass_name="unit-timeline",
        prompt=prompt,
        user_payload=payload,
        model_identity=llm.model_identity,
    )
    timeline_pass_dir = chain_dir / "unit_timeline" / cache_key
    paths = unit_timeline_artifact_paths(timeline_pass_dir)
    timeline_result_path = Path(paths["result"])
    cache_hit = use_cache and timeline_result_path.exists()
    if cache_hit:
        data = json.loads(timeline_result_path.read_text(encoding="utf-8"))
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
    else:
        raw_response = llm.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
        data = _restore_missing_records(data, repaired_data)
    validation_report = validate_unit_timeline_result(
        data, expected_unit_id=manifest["unit_id"]
    )
    record = UnitTimelineRecord(
        unit_id=manifest["unit_id"],
        cache_key=cache_key,
        cache_dir=str(timeline_pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_unit_timeline_artifacts(
            pass_dir=timeline_pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=data,
            validation_report=validation_report,
            record=record,
        )
    return record


def run_unit_timeline_repair_pass(
    timeline_pass_dir: str | Path,
    *,
    backend: LLMBackend | None = None,
    use_cache: bool = True,
) -> UnitTimelineRecord:
    pass_dir = Path(timeline_pass_dir)
    result_path = pass_dir / "result.json"
    if not result_path.exists():
        raise ValueError(f"missing timeline result: {result_path}")
    timeline_data = json.loads(result_path.read_text(encoding="utf-8"))
    chain_dir = pass_dir.parent.parent
    manifest_path = chain_dir / "chain_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing chain manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Run validation to collect repair targets
    prerepair_validation = validate_unit_timeline_result(
        timeline_data, expected_unit_id=manifest["unit_id"]
    )
    missing_events = []
    for issue in prerepair_validation.get("issues", []):
        if issue.get("code") == "events_missing_from_timelines":
            msg = issue.get("message", "")
            import re
            found = re.findall(r"unit-event-\d+", msg)
            missing_events.extend(found)
    timeline_data["_validation_issues"] = prerepair_validation.get("issues", [])
    timeline_data["_missing_events"] = list(dict.fromkeys(missing_events))

    llm = backend or MockExtractionBackend()
    prompt = build_unit_timeline_repair_composition()
    payload = build_unit_timeline_repair_payload(manifest, timeline_data)
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )
    cache_key = build_pass_cache_key(
        pass_name="unit-timeline-repair",
        prompt=prompt,
        user_payload=payload,
        model_identity=llm.model_identity,
    )
    repair_pass_dir = chain_dir / "unit_timeline_repair" / cache_key
    paths = unit_timeline_repair_artifact_paths(repair_pass_dir)
    repair_result_path = Path(paths["result"])
    cache_hit = use_cache and repair_result_path.exists()
    if cache_hit:
        data = json.loads(repair_result_path.read_text(encoding="utf-8"))
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
    else:
        raw_response = llm.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
        # Strip internal helper fields from LLM output
        data.pop("_validation_issues", None)
        data.pop("_missing_events", None)
        data = _restore_missing_records(data, timeline_data)
    validation_report = validate_unit_timeline_result(
        data, expected_unit_id=manifest["unit_id"]
    )
    record = UnitTimelineRecord(
        unit_id=manifest["unit_id"],
        cache_key=cache_key,
        cache_dir=str(repair_pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_unit_timeline_repair_artifacts(
            pass_dir=repair_pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=data,
            validation_report=validation_report,
            record=record,
        )
    return record


def run_overview_segmentation_pass(
    *,
    unit,
    text: str,
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool,
) -> JsonPassRecord:
    prompt = build_overview_composition()
    payload = {
        "task": "overview_segmentation",
        "unit_id": unit.id,
        "unit": {
            "id": unit.id,
            "label": unit.label,
            "kind": unit.kind,
            "title_path": unit.title_path,
            "content_kind": unit.content_kind,
            "source_kind": unit.source_kind,
            "source_range": unit.source_range,
        },
        "text": text,
    }
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(backend, "max_tokens", DEFAULT_MAX_TOKENS),
    )
    cache_key = build_pass_cache_key(
        pass_name="overview-segmentation",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = json_pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()
    if cache_hit:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
    else:
        raw_response = backend.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
        validate_overview_result(data)
    validation_report, anchor_locations = validate_overview_structure(data, text)
    record = JsonPassRecord(
        pass_name="overview-segmentation",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
        anchor_locations=anchor_locations,
    )
    if use_cache:
        write_json_pass_artifacts(
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


def refresh_cached_overview_record(record_data: dict[str, Any]) -> JsonPassRecord:
    paths = record_data["artifact_paths"]
    payload = json.loads(Path(paths["request_payload"]).read_text(encoding="utf-8"))
    data = json.loads(Path(paths["result"]).read_text(encoding="utf-8"))
    raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
    text = payload["text"]
    validation_report, anchor_locations = validate_overview_structure(data, text)
    record = JsonPassRecord(
        pass_name=record_data["pass_name"],
        cache_key=record_data["cache_key"],
        cache_dir=record_data["cache_dir"],
        cache_hit=True,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
        anchor_locations=anchor_locations,
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def run_text_segment_extraction_pass(
    *,
    parent_unit,
    segment: ResolvedOverviewSegment,
    context: ExtractionContext,
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool,
    generated_prompt_parts: list[PromptPart],
    cached_result: LocalBundleResult | None = None,
) -> ExtractionPassRecord:
    segment_unit = {
        "id": segment.segment_id,
        "label": segment.title,
        "kind": "overview-segment",
        "title_path": [*parent_unit.title_path, segment.title],
        "content_kind": parent_unit.content_kind,
        "source_kind": parent_unit.source_kind,
        "source_range": {
            "kind": "unit-char-span",
            "parent_unit_id": parent_unit.id,
            "start": segment.start,
            "end": segment.end,
        },
    }
    payload = {
        "unit": segment_unit,
        "prior_context": context.to_dict(),
        "text": segment.text,
    }
    prompt = build_segment_extraction_composition(generated_prompt_parts)
    if cached_result is None:
        check_extraction_budget(
            prompt.content,
            payload,
            max_output_tokens=getattr(backend, "max_tokens", DEFAULT_MAX_TOKENS),
        )
    cache_key = build_pass_cache_key(
        pass_name="segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / segment.segment_id / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()
    if cached_result is not None:
        result = cached_result
        if result.data.get("unit_id") != segment.segment_id:
            result.data["unit_id"] = segment.segment_id
        cache_hit = True
    elif cache_hit:
        result = result_from_json(result_path.read_text(encoding="utf-8"))
    else:
        raw_response = backend.complete_json(prompt.content, payload)
        data = parse_json_response(raw_response)
        validate_local_bundle(data)
        result = LocalBundleResult(
            task="segment_extraction",
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            unit_id=segment.segment_id,
            source_text_hash=sha256_text(segment.text),
            context_hash=sha256_json(context.to_dict()),
            model=backend.model_identity,
            raw_response=raw_response,
            data=data,
        )
    validation_report = validate_extraction_quality(
        result.data, segment.text, expected_unit_id=segment.segment_id
    )
    record = ExtractionPassRecord(
        pass_name="segment-extraction",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        result=result,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=result.raw_response,
            result=result,
            validation_report=validation_report,
            record=record,
        )
    return record


def refresh_cached_segment_record(record_data: dict[str, Any]) -> ExtractionPassRecord:
    paths = ensure_cached_pass_artifact_paths(record_data["artifact_paths"])
    payload = json.loads(Path(paths["request_payload"]).read_text(encoding="utf-8"))
    result = result_from_json(Path(paths["result"]).read_text(encoding="utf-8"))
    text = payload["text"]
    validation_report = validate_extraction_quality(
        result.data,
        text,
        expected_unit_id=result.unit_id,
    )
    record = ExtractionPassRecord(
        pass_name=record_data["pass_name"],
        cache_key=record_data["cache_key"],
        cache_dir=record_data["cache_dir"],
        cache_hit=True,
        result=result,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["validated_result"]).write_text(
        json.dumps(validation_report.to_validated_result(result.data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def ensure_cached_pass_artifact_paths(paths: dict[str, str]) -> dict[str, str]:
    if "validated_result" in paths:
        return paths
    result_path = Path(paths["result"])
    return {
        **paths,
        "validated_result": str(result_path.with_name("validated_result.json")),
    }


def build_segment_extraction_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        PromptPart(
            part_id="segment-extraction-contract",
            role="static_task_contract",
            source=f"resource:tilusion.prompts/{PROMPT_RESOURCE}",
            content=LOCAL_BUNDLE_SYSTEM_PROMPT,
            metadata={"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=PROMPT_VERSION, parts=parts)


def build_overview_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "overview-segmentation-contract",
            role="static_task_contract",
            resource_name=OVERVIEW_PROMPT_RESOURCE,
            metadata={"prompt_version": OVERVIEW_PROMPT_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=OVERVIEW_PROMPT_VERSION, parts=parts)


def build_unit_finalization_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_FINALIZATION_PROMPT_VERSION, parts=parts)


def build_unit_repair_composition() -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-repair-instructions",
            role="generated_repair_instructions",
            resource_name=UNIT_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_REPAIR_PROMPT_VERSION},
        ),
    ]
    return PromptComposition(composition_id=UNIT_REPAIR_PROMPT_VERSION, parts=parts)


def build_unit_timeline_composition() -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-repair-instructions",
            role="generated_repair_instructions",
            resource_name=UNIT_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_REPAIR_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-timeline-instructions",
            role="generated_timeline_instructions",
            resource_name=UNIT_TIMELINE_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_TIMELINE_PROMPT_VERSION},
        ),
    ]
    return PromptComposition(composition_id=UNIT_TIMELINE_PROMPT_VERSION, parts=parts)


def build_unit_timeline_repair_composition() -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-repair-instructions",
            role="generated_repair_instructions",
            resource_name=UNIT_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_REPAIR_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-timeline-instructions",
            role="generated_timeline_instructions",
            resource_name=UNIT_TIMELINE_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_TIMELINE_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-timeline-repair-instructions",
            role="generated_timeline_repair_instructions",
            resource_name=UNIT_TIMELINE_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_TIMELINE_REPAIR_PROMPT_VERSION},
        ),
    ]
    return PromptComposition(composition_id=UNIT_TIMELINE_REPAIR_PROMPT_VERSION, parts=parts)


def generated_prompt_part(
    part_id: str,
    *,
    role: str,
    content: str,
    generated_by: str,
    metadata: dict[str, Any] | None = None,
) -> PromptPart:
    return PromptPart(
        part_id=part_id,
        role=role,
        source="generated",
        content=content,
        generated_by=generated_by,
        metadata=metadata or {},
    )


def load_static_prompt_part(
    part_id: str,
    *,
    role: str,
    resource_name: str,
    metadata: dict[str, Any] | None = None,
) -> PromptPart:
    content = resources.files("tilusion.prompts").joinpath(resource_name).read_text(encoding="utf-8")
    return PromptPart(
        part_id=part_id,
        role=role,
        source=f"resource:tilusion.prompts/{resource_name}",
        content=content,
        metadata=metadata or {},
    )


def build_pass_cache_key(
    *,
    pass_name: str,
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    model_identity: str,
) -> str:
    return sha256_json(
        {
            "pass_name": pass_name,
            "prompt_composition": prompt.to_dict(),
            "user_payload_hash": sha256_json(user_payload),
            "model_identity": model_identity,
        }
    )


def pass_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
        "validated_result": str(pass_dir / "validated_result.json"),
    }


def json_pass_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
    }


def chain_artifact_paths(chain_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(chain_dir / "chain_manifest.json"),
        "resolved_segments": str(chain_dir / "resolved_segments.json"),
        "validation_report": str(chain_dir / "chain_validation_report.json"),
        "repair_hints": str(chain_dir / "repair_hints.json"),
    }


def unit_finalization_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
        "unit_extraction": str(pass_dir / "unit_extraction.json"),
        "unit_qc_report": str(pass_dir / "unit_qc_report.json"),
        "unit_reader_view": str(pass_dir / "unit_reader_view.md"),
    }


def unit_repair_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
        "unit_extraction": str(pass_dir / "unit_extraction.json"),
        "unit_qc_report": str(pass_dir / "unit_qc_report.json"),
        "unit_reader_view": str(pass_dir / "unit_reader_view.md"),
    }


def unit_timeline_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
        "unit_extraction": str(pass_dir / "unit_extraction.json"),
        "timeline_view": str(pass_dir / "timeline_view.md"),
    }


def unit_timeline_repair_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
        "unit_extraction": str(pass_dir / "unit_extraction.json"),
        "timeline_view": str(pass_dir / "timeline_view.md"),
    }


def chain_cache_key(*, unit_id: str, text: str, model_identity: str) -> str:
    return sha256_json(
        {
            "pipeline": "overview-plus-segment-extraction-v0.1",
            "unit_id": unit_id,
            "source_text_hash": sha256_text(text),
            "model_identity": model_identity,
            "overview_prompt_version": OVERVIEW_PROMPT_VERSION,
            "segment_prompt_version": PROMPT_VERSION,
        }
    )


def build_unit_finalization_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "unit_finalization",
        "unit_id": manifest["unit_id"],
        "source_length": manifest.get("source_length", {}),
        "resolved_segments": [
            compact_resolved_segment(segment)
            for segment in manifest.get("resolved_segments", [])
        ],
        "chain_validation": compact_chain_validation(manifest.get("validation_report", {})),
        "repair_hints": manifest.get("repair_hints", {}),
        "segment_results": [
            compact_segment_result(record)
            for record in manifest.get("segment_passes", [])
        ],
    }


def build_unit_repair_payload(
    manifest: dict[str, Any],
    finalization_data: dict[str, Any],
) -> dict[str, Any]:
    payload = build_unit_finalization_payload(manifest)
    payload["repair_targets"] = {
        "unresolved_items": finalization_data.get("unresolved_items", []),
        "blocking_concerns": (
            finalization_data.get("quality_notes", {}).get("blocking_concerns", [])
            if isinstance(finalization_data.get("quality_notes"), dict)
            else []
        ),
        "warnings": finalization_data.get("warnings", []),
    }
    return payload


def build_unit_timeline_payload(
    manifest: dict[str, Any],
    repaired_data: dict[str, Any],
) -> dict[str, Any]:
    payload = build_unit_finalization_payload(manifest)
    payload["unit_records"] = {
        "entity_records": repaired_data.get("entity_records", []),
        "location_records": repaired_data.get("location_records", []),
        "event_records": repaired_data.get("event_records", []),
        "thread_records": repaired_data.get("thread_records", []),
    }
    if repaired_data.get("unresolved_items"):
        payload["quality_context"] = {
            "unresolved_items": repaired_data["unresolved_items"],
            "warnings": repaired_data.get("warnings", []),
        }
    payload["task"] = "unit_timeline"
    return payload


def build_unit_timeline_repair_payload(
    manifest: dict[str, Any],
    timeline_data: dict[str, Any],
) -> dict[str, Any]:
    payload = build_unit_finalization_payload(manifest)
    payload["unit_records"] = {
        "entity_records": timeline_data.get("entity_records", []),
        "location_records": timeline_data.get("location_records", []),
        "event_records": timeline_data.get("event_records", []),
        "thread_records": timeline_data.get("thread_records", []),
    }
    payload["timelines"] = timeline_data.get("timelines", [])
    payload["repair_targets"] = {
        "validation_issues": timeline_data.get("_validation_issues", []),
        "missing_events": timeline_data.get("_missing_events", []),
    }
    payload["task"] = "unit_timeline_repair"
    return payload


def compact_resolved_segment(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "segment_id": segment.get("segment_id"),
        "title": segment.get("title"),
        "summary": segment.get("summary"),
        "start": segment.get("start"),
        "end": segment.get("end"),
        "length": segment.get("length", {}),
        "source": {
            "start_quote": (segment.get("source") or {}).get("start_quote"),
            "end_quote": (segment.get("source") or {}).get("end_quote"),
        },
    }


def compact_chain_validation(validation_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "segment_pass_count": validation_report.get("segment_pass_count", 0),
        "resolved_segment_count": validation_report.get("resolved_segment_count", 0),
        "segment_quality_overview": validation_report.get("segment_quality_overview", {}),
    }


def compact_segment_result(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result", {})
    data = result.get("data", {})
    validation = record.get("validation_report", {})
    return {
        "segment_id": result.get("unit_id"),
        "validation": {
            "passed": validation.get("passed"),
            "error_count": validation.get("error_count", 0),
            "warning_count": validation.get("warning_count", 0),
            "issue_codes": [
                issue.get("code")
                for issue in validation.get("issues", [])
                if isinstance(issue, dict)
            ],
        },
        "evidence_spans": data.get("evidence_spans", []),
        "entity_mentions": data.get("entity_mentions", []),
        "location_mentions": data.get("location_mentions", []),
        "event_mentions": data.get("event_mentions", []),
        "time_expressions": data.get("time_expressions", []),
        "thread_candidates": data.get("thread_candidates", []),
        "warnings": data.get("warnings", []),
    }


def validate_unit_finalization_result(
    data: dict[str, Any],
    *,
    expected_unit_id: str,
) -> dict[str, Any]:
    issues = []
    required = {
        "unit_id": str,
        "entity_records": list,
        "location_records": list,
        "event_records": list,
        "thread_records": list,
        "unresolved_items": list,
        "quality_notes": dict,
        "warnings": list,
    }
    for key, expected_type in required.items():
        if key not in data:
            issues.append(unit_finalization_issue("error", "missing_required_field", key))
        elif not isinstance(data[key], expected_type):
            issues.append(unit_finalization_issue("error", "wrong_field_type", key))
    if data.get("unit_id") != expected_unit_id:
        issues.append(
            {
                "severity": "error",
                "code": "unit_id_mismatch",
                "path": "unit_id",
                "message": f"Expected `{expected_unit_id}` but got `{data.get('unit_id')}`.",
            }
        )
    quality_notes = data.get("quality_notes")
    if isinstance(quality_notes, dict):
        if not isinstance(quality_notes.get("summary"), str):
            issues.append(unit_finalization_issue("error", "wrong_field_type", "quality_notes.summary"))
        for key in ["blocking_concerns"]:
            if not isinstance(quality_notes.get(key), list):
                issues.append(unit_finalization_issue("error", "wrong_field_type", f"quality_notes.{key}"))
    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "passed": error_count == 0,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }


def unit_finalization_issue(severity: str, code: str, path: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": f"{path} failed unit finalization validation.",
    }


def validate_unit_timeline_result(
    data: dict[str, Any],
    *,
    expected_unit_id: str,
) -> dict[str, Any]:
    issues = []
    if data.get("unit_id") != expected_unit_id:
        issues.append(unit_finalization_issue("error", "unit_id_mismatch", "unit_id"))

    timelines = data.get("timelines")
    if not isinstance(timelines, list) or len(timelines) == 0:
        issues.append(unit_finalization_issue("error", "missing_required_field", "timelines"))
        error_count = sum(1 for i in issues if i["severity"] == "error")
        return {
            "passed": False,
            "issue_count": len(issues),
            "error_count": error_count,
            "warning_count": 0,
            "issues": issues,
        }

    input_event_ids = {e.get("event_id") for e in data.get("event_records", []) if isinstance(e, dict)}
    all_event_ids: set[str] = set()
    timeline_event_sets: list[set[str]] = []
    for i, timeline in enumerate(timelines):
        prefix = f"timelines[{i}]"
        for field in ["timeline_id", "summary", "ordered_events", "confidence"]:
            if field not in timeline:
                issues.append(unit_finalization_issue("error", "missing_required_field", f"{prefix}.{field}"))
        tid = timeline.get("timeline_id", "")
        if not isinstance(tid, str) or not tid.startswith("unit-timeline-"):
            issues.append(unit_finalization_issue("error", "wrong_field_type", f"{prefix}.timeline_id"))
        conf = timeline.get("confidence")
        if conf not in ("high", "medium", "low"):
            issues.append(unit_finalization_issue("warning", "wrong_field_type", f"{prefix}.confidence"))

        ordered = timeline.get("ordered_events")
        tl_event_ids: set[str] = set()
        if isinstance(ordered, list):
            for j, entry in enumerate(ordered):
                if not isinstance(entry, dict):
                    issues.append(unit_finalization_issue("error", "wrong_field_type", f"{prefix}.ordered_events[{j}]"))
                    continue
                eid = entry.get("event_id")
                if not isinstance(eid, str):
                    issues.append(unit_finalization_issue("error", "missing_required_field", f"{prefix}.ordered_events[{j}].event_id"))
                else:
                    all_event_ids.add(eid)
                    tl_event_ids.add(eid)
                before = entry.get("before_events")
                if before is not None and not isinstance(before, list):
                    issues.append(unit_finalization_issue("error", "wrong_field_type", f"{prefix}.ordered_events[{j}].before_events"))
                has_edges = bool(before)
                if has_edges and not isinstance(entry.get("rationale"), str):
                    issues.append(unit_finalization_issue("warning", "missing_required_field", f"{prefix}.ordered_events[{j}].rationale"))
                # Self-loop check
                if isinstance(eid, str) and isinstance(before, list) and eid in before:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "timeline_self_loop",
                            "path": f"{prefix}.ordered_events[{j}]",
                            "message": f"Event {eid} lists itself in before_events.",
                        }
                    )
                # Phantom ref check
                if isinstance(before, list):
                    for ref_id in before:
                        if isinstance(ref_id, str) and ref_id not in input_event_ids:
                            issues.append(
                                {
                                    "severity": "error",
                                    "code": "timeline_phantom_ref",
                                    "path": f"{prefix}.ordered_events[{j}].before_events",
                                    "message": f"before_events references unknown event '{ref_id}'.",
                                }
                            )

        timeline_event_sets.append(tl_event_ids)
        cycles = _detect_timeline_cycles(ordered if isinstance(ordered, list) else [])
        for cycle in cycles:
            issues.append(
                {
                    "severity": "error",
                    "code": "timeline_cycle_detected",
                    "path": f"{prefix}.ordered_events",
                    "message": f"Cycle detected: {' -> '.join(cycle)}",
                }
            )

    # Duplicate event across timelines check
    for i in range(len(timeline_event_sets)):
        for j in range(i + 1, len(timeline_event_sets)):
            overlap = timeline_event_sets[i] & timeline_event_sets[j]
            for eid in overlap:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "timeline_shared_event",
                        "path": f"timelines[{i}],timelines[{j}]",
                        "message": f"Event {eid} appears in multiple timelines. Allowed as intersection point but should be intentional.",
                    }
                )

    missing = input_event_ids - all_event_ids
    extra = all_event_ids - input_event_ids
    if missing:
        issues.append(
            {
                "severity": "error",
                "code": "events_missing_from_timelines",
                "path": "timelines",
                "message": f"Events not covered by any timeline: {sorted(missing)}",
            }
        )
    if extra:
        issues.append(unit_finalization_issue("error", "unknown_events_in_timelines", f"extra: {sorted(extra)}"))

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")
    return {
        "passed": error_count == 0,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }


def _detect_timeline_cycles(ordered_events: list[dict]) -> list[list[str]]:
    adj: dict[str, list[str]] = {}
    for entry in ordered_events:
        eid = entry.get("event_id")
        if not isinstance(eid, str):
            continue
        adj.setdefault(eid, [])
        for before_id in (entry.get("before_events") or []):
            if isinstance(before_id, str) and before_id in {e.get("event_id") for e in ordered_events if isinstance(e.get("event_id"), str)}:
                adj.setdefault(eid, []).append(before_id)

    WHITE, GRAY, BLACK = 0, 1, 2
    all_nodes = {e.get("event_id") for e in ordered_events if isinstance(e.get("event_id"), str)}
    color: dict[str, int] = {node: WHITE for node in all_nodes}
    cycles: list[list[str]] = []
    parent: dict[str, str | None] = {}

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycle = [neighbor, node]
                cur = node
                while parent.get(cur) and parent[cur] != neighbor:
                    cur = parent[cur]
                    cycle.append(cur)
                cycles.append(cycle)
            elif color[neighbor] == WHITE:
                parent[neighbor] = node
                dfs(neighbor)
        color[node] = BLACK

    for node in all_nodes:
        if color[node] == WHITE:
            parent[node] = None
            dfs(node)
    return cycles


def validate_overview_result(data: dict[str, Any]) -> None:
    required = {
        "unit_id": str,
        "overview_segments": list,
        "warnings": list,
    }
    for key, expected_type in required.items():
        if key not in data:
            raise ValueError(f"missing overview field: {key}")
        if not isinstance(data[key], expected_type):
            raise ValueError(f"overview field {key} must be {expected_type.__name__}")


def validate_overview_structure(
    data: dict[str, Any], text: str
) -> tuple[dict[str, Any], dict[str, EvidenceLocation]]:
    issues = []
    anchor_locations: dict[str, EvidenceLocation] = {}
    segments = data.get("overview_segments") if isinstance(data.get("overview_segments"), list) else []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            issues.append(
                overview_issue(
                    "error",
                    "wrong_segment_type",
                    f"overview_segments[{index}]",
                    "Overview segment must be an object.",
                    "Replace this item with a segment object or remove it.",
                )
            )
            continue
        for field_name in ["segment_id", "title", "summary", "start_quote", "end_quote"]:
            if not isinstance(segment.get(field_name), str) or not segment.get(field_name):
                issues.append(
                    overview_issue(
                        "error",
                        "missing_segment_field",
                        f"overview_segments[{index}].{field_name}",
                        f"Segment is missing `{field_name}`.",
                        "Add a non-empty string value.",
                    )
                )
        for field_name in ["start_quote", "end_quote"]:
            quote = segment.get(field_name)
            if isinstance(quote, str) and quote:
                location_key = f"{segment.get('segment_id') or index}:{field_name}"
                location = relocate_evidence_quote(
                    text,
                    quote,
                    evidence_id=location_key,
                )
                anchor_locations[location_key] = location
                if location.status == "missing":
                    issues.append(
                        overview_issue(
                            "error",
                            "segment_anchor_missing",
                            f"overview_segments[{index}].{field_name}",
                            "Segment anchor could not be relocated in the source text.",
                            "Replace the anchor with a source substring from the unit.",
                            relocation=location.to_dict(),
                        )
                    )
                elif location.status == "ambiguous":
                    issues.append(
                        overview_issue(
                            "warning",
                            "segment_anchor_ambiguous",
                            f"overview_segments[{index}].{field_name}",
                            "Segment anchor has multiple possible source locations.",
                            "Use a longer or more distinctive anchor quote.",
                            relocation=location.to_dict(),
                        )
                    )
    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "passed": error_count == 0,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }, anchor_locations


def overview_issue(
    severity: str,
    code: str,
    path: str,
    message: str,
    repair_hint: str,
    *,
    relocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
        "repair_hint": repair_hint,
        "relocation": relocation,
    }


def resolve_overview_segments(
    overview_data: dict[str, Any],
    text: str,
    *,
    anchor_locations: dict[str, EvidenceLocation] | None = None,
) -> tuple[list[ResolvedOverviewSegment], list[dict[str, Any]]]:
    resolved = []
    repair_hints = []
    precomputed = anchor_locations or {}
    segments = overview_data.get("overview_segments") or []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id") or f"overview-segment-{index + 1:04d}")
        start_key = f"{segment_id}:start_quote"
        end_key = f"{segment_id}:end_quote"
        start_location = precomputed.get(start_key) or relocate_evidence_quote(
            text,
            str(segment.get("start_quote") or ""),
            evidence_id=start_key,
        )
        end_location = precomputed.get(end_key) or relocate_evidence_quote(
            text,
            str(segment.get("end_quote") or ""),
            evidence_id=end_key,
        )
        if (
            start_location.start is None
            or end_location.end is None
            or start_location.status == "ambiguous"
            or end_location.status == "ambiguous"
            or end_location.end < start_location.start
        ):
            repair_hints.append(
                {
                    "segment_id": segment_id,
                    "code": "segment_span_unresolved",
                    "repair_hint": "Provide distinctive start_quote and end_quote anchors in source order.",
                    "start_location": start_location.to_dict(),
                    "end_location": end_location.to_dict(),
                }
            )
            continue
        resolved.append(
            ResolvedOverviewSegment(
                segment_id=segment_id,
                title=str(segment.get("title") or segment_id),
                summary=str(segment.get("summary") or ""),
                start=start_location.start,
                end=end_location.end,
                text=text[start_location.start : end_location.end],
                source=segment,
                start_location=start_location,
                end_location=end_location,
            )
        )
    return resolved, repair_hints


def segment_hint_payload(segment: ResolvedOverviewSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "overview_title": segment.title,
        "overview_summary": segment.summary,
        "key_entities": segment.source.get("key_entities", []),
        "key_locations": segment.source.get("key_locations", []),
        "time_hints": segment.source.get("time_hints", []),
        "event_hints": segment.source.get("event_hints", []),
        "extraction_hints": segment.source.get("extraction_hints", []),
    }


def _derive_unresolved_detail(repair: dict[str, Any]) -> str:
    start = repair.get("start_location", {}) if isinstance(repair.get("start_location"), dict) else {}
    end = repair.get("end_location", {}) if isinstance(repair.get("end_location"), dict) else {}
    start_status = start.get("status", "unknown")
    end_status = end.get("status", "unknown")
    if start_status == "missing" or end_status == "missing":
        return "unrelocatable anchor(s)"
    if start_status == "ambiguous" or end_status == "ambiguous":
        parts = []
        if start_status == "ambiguous":
            parts.append(f"start_quote ({start.get('candidate_count', 0)} candidates)")
        if end_status == "ambiguous":
            parts.append(f"end_quote ({end.get('candidate_count', 0)} candidates)")
        return f"ambiguous: {', '.join(parts)}"
    if isinstance(start.get("start"), int) and isinstance(end.get("end"), int):
        if end["end"] < start["start"]:
            return "inverted span (end before start)"
    return repair.get("repair_hint", "unresolved")


def _dominant_issue_codes(segment_reports: list[dict[str, Any]]) -> list[str]:
    code_counts: dict[str, int] = {}
    for report in segment_reports:
        for issue in report.get("issues", []):
            code = issue.get("code", "unknown")
            code_counts[code] = code_counts.get(code, 0) + 1
    return sorted(code_counts, key=code_counts.get, reverse=True)


def _build_segment_quality_overview(
    total_overview_segments: int,
    overview_repairs: list[dict[str, Any]],
    segment_reports: list[dict[str, Any]],
    segment_lengths: list[dict[str, Any]],
) -> dict[str, Any]:
    resolved_count = len(segment_reports)
    unresolved_count = max(0, total_overview_segments - resolved_count)
    unresolved_reasons = []
    for repair in overview_repairs:
        unresolved_reasons.append(
            {
                "segment_id": repair.get("segment_id"),
                "code": repair.get("code"),
                "detail": _derive_unresolved_detail(repair),
            }
        )
    per_segment = []
    for lengths_entry, report in zip(segment_lengths, segment_reports):
        issue_codes: dict[str, int] = {}
        for issue in report.get("issues", []):
            code = issue.get("code", "unknown")
            issue_codes[code] = issue_codes.get(code, 0) + 1
        per_segment.append(
            {
                "segment_id": lengths_entry.get("segment_id"),
                "chars": lengths_entry.get("chars"),
                "passed": report.get("passed"),
                "issue_codes": issue_codes,
                "evidence": report.get("evidence_location_summary", {}),
            }
        )
    return {
        "total_overview_segments": total_overview_segments,
        "resolved_segments": resolved_count,
        "unresolved_segments": unresolved_count,
        "unresolved_reasons": unresolved_reasons,
        "per_segment": per_segment,
        "dominant_issues": _dominant_issue_codes(segment_reports),
    }


def build_chain_validation_report(
    overview: JsonPassRecord,
    resolved_segments: list[ResolvedOverviewSegment],
    segment_passes: list[ExtractionPassRecord],
    *,
    overview_repairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    segment_reports = [record.validation_report.to_dict() for record in segment_passes]
    error_count = overview.validation_report["error_count"] + sum(
        report["error_count"] for report in segment_reports
    )
    warning_count = overview.validation_report["warning_count"] + sum(
        report["warning_count"] for report in segment_reports
    )
    total_overview = len(overview.data.get("overview_segments") or [])
    segment_lengths = [
        {
            "segment_id": segment.segment_id,
            "start": segment.start,
            "end": segment.end,
            **text_length_stats(segment.text),
        }
        for segment in resolved_segments
    ]
    return {
        "passed": error_count == 0,
        "overview": overview.validation_report,
        "resolved_segment_count": len(resolved_segments),
        "segment_pass_count": len(segment_passes),
        "segment_lengths": segment_lengths,
        "segment_reports": segment_reports,
        "segment_quality_overview": _build_segment_quality_overview(
            total_overview_segments=total_overview,
            overview_repairs=overview_repairs or [],
            segment_reports=segment_reports,
            segment_lengths=segment_lengths,
        ),
        "error_count": error_count,
        "warning_count": warning_count,
    }


def build_cached_chain_validation_report(
    overview_report: dict[str, Any],
    resolved_segments: list[dict[str, Any]],
    segment_reports: list[dict[str, Any]],
    *,
    total_overview_segments: int = 0,
    overview_repairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    error_count = overview_report["error_count"] + sum(
        report["error_count"] for report in segment_reports
    )
    warning_count = overview_report["warning_count"] + sum(
        report["warning_count"] for report in segment_reports
    )
    segment_lengths = [
        {
            "segment_id": segment["segment_id"],
            "start": segment["start"],
            "end": segment["end"],
            **segment["length"],
        }
        for segment in resolved_segments
    ]
    return {
        "passed": error_count == 0,
        "overview": overview_report,
        "resolved_segment_count": len(resolved_segments),
        "segment_pass_count": len(segment_reports),
        "segment_lengths": segment_lengths,
        "segment_reports": segment_reports,
        "segment_quality_overview": _build_segment_quality_overview(
            total_overview_segments=total_overview_segments,
            overview_repairs=overview_repairs or [],
            segment_reports=segment_reports,
            segment_lengths=segment_lengths,
        ),
        "error_count": error_count,
        "warning_count": warning_count,
    }


def _build_non_actionable_warning_summary(
    segment_passes: list[ExtractionPassRecord],
) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    affected_segments: list[str] = []
    total = 0
    for record in segment_passes:
        segment_id = record.result.unit_id
        segment_has_non_actionable = False
        for issue in record.validation_report.issues:
            if not is_llm_actionable_issue(issue):
                code = issue.code
                by_code[code] = by_code.get(code, 0) + 1
                total += 1
                segment_has_non_actionable = True
        if segment_has_non_actionable:
            affected_segments.append(segment_id)
    return {
        "total": total,
        "by_code": by_code,
        "affected_segments": affected_segments,
    }


def build_chain_repair_hints(
    overview_repairs: list[dict[str, Any]],
    segment_passes: list[ExtractionPassRecord],
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    segment_repairs = []
    for record in segment_passes:
        payload = record.validation_report.to_llm_repair_payload()
        if payload["quality_summary"]["llm_actionable_issue_count"]:
            segment_repairs.append(
                {
                    "segment_id": record.result.unit_id,
                    "repair_payload": payload,
                    "result_path": record.artifact_paths["result"],
                    "validation_report_path": record.artifact_paths["validation_report"],
                    "validated_result_path": record.artifact_paths["validated_result"],
                }
            )
    non_actionable = _build_non_actionable_warning_summary(segment_passes)
    return {
        "ready_for_llm_repair": bool(overview_repairs or segment_repairs),
        "overview_repairs": overview_repairs,
        "segment_repairs": segment_repairs,
        "non_actionable_warnings": non_actionable,
        "summary": {
            "passed": validation_report["passed"],
            "error_count": validation_report["error_count"],
            "warning_count": validation_report["warning_count"],
        },
    }


def write_pass_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    result: LocalBundleResult,
    validation_report: ExtractionQualityReport,
    record: ExtractionPassRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(result.to_json(), encoding="utf-8")
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["validated_result"]).write_text(
        json.dumps(validation_report.to_validated_result(result.data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_json_pass_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: dict[str, Any],
    record: JsonPassRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_chain_artifacts(
    chain_dir: Path,
    paths: dict[str, str],
    record: ChainedExtractionRecord,
) -> None:
    chain_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["resolved_segments"]).write_text(
        json.dumps(
            [segment.to_dict() for segment in record.resolved_segments],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(record.validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["repair_hints"]).write_text(
        json.dumps(record.repair_hints, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")


def write_unit_finalization_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: dict[str, Any],
    record: UnitFinalizationRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_extraction"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_qc_report"]).write_text(
        json.dumps(build_unit_qc_report(data, validation_report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_reader_view"]).write_text(
        format_unit_reader_view(data, validation_report),
        encoding="utf-8",
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")


def write_unit_repair_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: dict[str, Any],
    record: UnitFinalizationRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_extraction"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_qc_report"]).write_text(
        json.dumps(build_unit_qc_report(data, validation_report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_reader_view"]).write_text(
        format_unit_reader_view(data, validation_report),
        encoding="utf-8",
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")


def write_unit_timeline_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: dict[str, Any],
    record: UnitTimelineRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_extraction"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["timeline_view"]).write_text(
        format_unit_timeline_view(data, validation_report),
        encoding="utf-8",
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")


def write_unit_timeline_repair_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: dict[str, Any],
    record: UnitTimelineRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["unit_extraction"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["timeline_view"]).write_text(
        format_unit_timeline_view(data, validation_report),
        encoding="utf-8",
    )
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")


def build_unit_qc_report(data: dict[str, Any], validation_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": data.get("unit_id"),
        "validation_report": validation_report,
        "quality_notes": data.get("quality_notes", {}),
        "unresolved_items": data.get("unresolved_items", []),
        "warnings": data.get("warnings", []),
    }


def format_unit_reader_view(data: dict[str, Any], validation_report: dict[str, Any]) -> str:
    quality_notes = data.get("quality_notes", {}) if isinstance(data.get("quality_notes"), dict) else {}
    lines = [
        f"# Unit Extraction: {data.get('unit_id', '')}",
        "",
        f"- validation_passed: {str(validation_report.get('passed')).lower()}",
        f"- entities: {len(data.get('entity_records', []) or [])}",
        f"- locations: {len(data.get('location_records', []) or [])}",
        f"- events: {len(data.get('event_records', []) or [])}",
        f"- threads: {len(data.get('thread_records', []) or [])}",
        f"- unresolved_items: {len(data.get('unresolved_items', []) or [])}",
        "",
        "## Quality Notes",
        "",
        quality_notes.get("summary") or "No model quality summary provided.",
        "",
        "## Blocking Concerns",
    ]
    concerns = quality_notes.get("blocking_concerns") or []
    if concerns:
        lines.extend(f"- {concern}" for concern in concerns)
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Warnings",
    ])
    warnings = data.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def format_unit_timeline_view(data: dict[str, Any], validation_report: dict[str, Any]) -> str:
    lines = [
        f"# Timeline View: {data.get('unit_id', '')}",
        "",
        f"- validation_passed: {str(validation_report.get('passed')).lower()}",
        f"- timelines: {len(data.get('timelines', []) or [])}",
        f"- events: {len(data.get('event_records', []) or [])}",
    ]
    event_map = {
        e.get("event_id"): e.get("summary", "")
        for e in (data.get("event_records") or [])
        if isinstance(e, dict)
    }
    for timeline in data.get("timelines", []) or []:
        lines.extend([
            "",
            f"## {timeline.get('timeline_id')}: {timeline.get('summary', '')}",
            f"  confidence: {timeline.get('confidence', 'unknown')}",
            "",
            "### Event Order",
            "",
        ])
        for entry in timeline.get("ordered_events", []) or []:
            eid = entry.get("event_id", "?")
            before = entry.get("before_events") or []
            summary = event_map.get(eid, "")
            lines.append(f"- **{eid}**: {summary}")
            if before:
                lines.append(f"  before: {', '.join(before)}")
            rationale = entry.get("rationale")
            if rationale:
                lines.append(f"  rationale: {rationale}")
    return "\n".join(lines) + "\n"


def _log_progress(
    step: int,
    total: int,
    description: str,
    status: str,
    elapsed_ms: int,
) -> None:
    elapsed_s = elapsed_ms / 1000
    print(
        f"[{step}/{total}] {description}... {status} ({elapsed_s:.1f}s)",
        file=sys.stderr,
    )


def run_all_passes(
    book_path: str | Path,
    unit_id: str,
    *,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache",
    use_cache: bool = True,
    skip_repair: bool = False,
) -> RunAllRecord:
    llm = backend or MockExtractionBackend()
    root = Path(cache_dir)
    total_start = time.monotonic()
    pass_summaries: dict[str, dict[str, Any]] = {}
    final_data: dict[str, Any] = {}

    chain_dir: str | None = None
    finalization_dir: str | None = None
    repair_dir: str | None = None
    timeline_dir: str | None = None

    # ---- Step 1: Chain (overview + segments) ----
    t0 = time.monotonic()
    try:
        chain_record = run_chained_extraction(
            book_path, unit_id, backend=llm,
            cache_dir=root / "extraction_chains", use_cache=use_cache,
        )
    except Exception:
        _log_progress(1, 5, "overview+segments", "FAILED", _elapsed_ms(t0))
        raise
    chain_dir = chain_record.cache_dir
    pass_summaries["chain"] = {
        "cache_key": chain_record.overview.cache_key,
        "cache_hit": chain_record.overview.cache_hit,
        "elapsed_ms": _elapsed_ms(t0),
        "segments_resolved": len(chain_record.resolved_segments),
        "segments_total": len(chain_record.overview.data.get("overview_segments", [])),
    }
    _log_progress(
        1, 5, "overview+segments",
        "cache hit" if chain_record.overview.cache_hit else "LLM call",
        pass_summaries["chain"]["elapsed_ms"],
    )

    # ---- Step 2: Unit finalization ----
    t0 = time.monotonic()
    try:
        finalization_record = run_unit_finalization_pass(
            chain_dir, backend=llm, use_cache=use_cache,
        )
    except Exception:
        _log_progress(2, 5, "unit finalization", "FAILED", _elapsed_ms(t0))
        raise
    finalization_dir = finalization_record.cache_dir
    pass_summaries["finalization"] = {
        "cache_key": finalization_record.cache_key,
        "cache_hit": finalization_record.cache_hit,
        "elapsed_ms": _elapsed_ms(t0),
    }
    _log_progress(
        2, 5, "unit finalization",
        "cache hit" if finalization_record.cache_hit else "LLM call",
        pass_summaries["finalization"]["elapsed_ms"],
    )

    # ---- Step 3: Unit repair (conditional) ----
    t0 = time.monotonic()
    repair_record = None
    repair_hints = chain_record.repair_hints
    repair_needed = (
        not skip_repair
        and isinstance(repair_hints, dict)
        and repair_hints.get("ready_for_llm_repair") is True
    )
    if repair_needed:
        try:
            repair_record = run_unit_repair_pass(
                finalization_dir, backend=llm, use_cache=use_cache,
            )
        except Exception:
            _log_progress(3, 5, "unit repair", "FAILED (continuing)", _elapsed_ms(t0))
        else:
            repair_dir = repair_record.cache_dir
            pass_summaries["repair"] = {
                "cache_key": repair_record.cache_key,
                "cache_hit": repair_record.cache_hit,
                "elapsed_ms": _elapsed_ms(t0),
                "skipped": False,
            }
            _log_progress(
                3, 5, "unit repair",
                "cache hit" if repair_record.cache_hit else "LLM call",
                pass_summaries["repair"]["elapsed_ms"],
            )
    if "repair" not in pass_summaries:
        reason = "nothing actionable" if not skip_repair else "repair skipped by flag"
        pass_summaries["repair"] = {"skipped": True, "reason": reason}
        _log_progress(3, 5, "unit repair", f"skipped ({reason})", _elapsed_ms(t0))

    # The input to timeline is the repaired data if repair ran, else finalization
    timeline_input_dir = repair_dir or finalization_dir  # type: ignore[assignment]

    # ---- Step 4: Timeline construction ----
    t0 = time.monotonic()
    try:
        timeline_record = run_unit_timeline_pass(
            timeline_input_dir, backend=llm, use_cache=use_cache,
        )
    except Exception:
        _log_progress(4, 5, "timeline construction", "FAILED", _elapsed_ms(t0))
        raise
    timeline_dir = timeline_record.cache_dir
    pass_summaries["timeline"] = {
        "cache_key": timeline_record.cache_key,
        "cache_hit": timeline_record.cache_hit,
        "elapsed_ms": _elapsed_ms(t0),
    }
    _log_progress(
        4, 5, "timeline construction",
        "cache hit" if timeline_record.cache_hit else "LLM call",
        pass_summaries["timeline"]["elapsed_ms"],
    )
    final_data = timeline_record.data

    # ---- Step 5: Timeline repair (conditional) ----
    t0 = time.monotonic()
    tl_validation = timeline_record.validation_report
    tl_errors = tl_validation.get("error_count", 0) if isinstance(tl_validation, dict) else 0
    if not skip_repair and isinstance(tl_errors, int) and tl_errors > 0:
        try:
            tl_repair_record = run_unit_timeline_repair_pass(
                timeline_dir, backend=llm, use_cache=use_cache,
            )
        except Exception:
            _log_progress(5, 5, "timeline repair", "FAILED (continuing)", _elapsed_ms(t0))
        else:
            pass_summaries["timeline_repair"] = {
                "cache_key": tl_repair_record.cache_key,
                "cache_hit": tl_repair_record.cache_hit,
                "elapsed_ms": _elapsed_ms(t0),
                "skipped": False,
            }
            final_data = tl_repair_record.data
            _log_progress(
                5, 5, "timeline repair",
                "cache hit" if tl_repair_record.cache_hit else "LLM call",
                pass_summaries["timeline_repair"]["elapsed_ms"],
            )
    if "timeline_repair" not in pass_summaries:
        reason = "no timeline errors" if tl_errors == 0 else "repair skipped by flag"
        pass_summaries["timeline_repair"] = {"skipped": True, "reason": reason}
        _log_progress(5, 5, "timeline repair", f"skipped ({reason})", _elapsed_ms(t0))

    total_elapsed = _elapsed_ms(total_start)

    # Validation summary from the final pass
    validation_summary = _validation_summary(final_data)

    # Write unit package
    package_path = write_unit_package(
        unit_id=unit_id,
        book_path=str(book_path),
        passes=pass_summaries,
        data=final_data,
        validation=validation_summary,
        cache_root=root,
    )

    return RunAllRecord(
        unit_id=unit_id,
        elapsed_ms=total_elapsed,
        unit_package_path=package_path,
        passes=pass_summaries,
        data=final_data,
        validation=validation_summary,
    )


def write_unit_package(
    *,
    unit_id: str,
    book_path: str,
    passes: dict[str, dict[str, Any]],
    data: dict[str, Any],
    validation: dict[str, Any],
    cache_root: Path,
) -> str:
    package_dir = cache_root / "units" / unit_id
    package_dir.mkdir(parents=True, exist_ok=True)
    package_path = package_dir / "unit_package.json"
    source_length = data.get("source_length", {})
    package = {
        "unit_id": unit_id,
        "source": {
            "book_path": book_path,
            "char_count": source_length.get("char_count") if isinstance(source_length, dict) else None,
            "line_count": source_length.get("line_count") if isinstance(source_length, dict) else None,
        },
        "passes": passes,
        "data": data,
        "validation": validation,
    }
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(package_path)


def _elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _validation_summary(data: dict[str, Any]) -> dict[str, Any]:
    event_count = len(data.get("event_records", []) or [])
    entity_count = len(data.get("entity_records", []) or [])
    location_count = len(data.get("location_records", []) or [])
    thread_count = len(data.get("thread_records", []) or [])
    timeline_count = len(data.get("timelines", []) or [])
    return {
        "event_count": event_count,
        "entity_count": entity_count,
        "location_count": location_count,
        "thread_count": thread_count,
        "timeline_count": timeline_count,
    }


_RECORD_KEYS = (
    "entity_records",
    "location_records",
    "event_records",
    "thread_records",
)


def _restore_missing_records(data: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Copy record arrays from source if missing or empty in data (LLM safety net)."""
    for key in _RECORD_KEYS:
        if not data.get(key):
            source_val = source.get(key)
            if source_val:
                data[key] = source_val
    return data


def text_length_stats(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "nonempty_lines": sum(1 for line in text.splitlines() if line.strip()),
    }
