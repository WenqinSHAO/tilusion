from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import resources
import json
from pathlib import Path
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
)


OVERVIEW_PROMPT_VERSION = "overview-segmentation-v0.1"
OVERVIEW_PROMPT_RESOURCE = "overview_segmentation_v0.1.md"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "cache_dir": self.cache_dir,
            "source_length": self.source_length,
            "artifact_paths": self.artifact_paths,
            "overview": self.overview.to_dict(),
            "resolved_segments": [segment.to_dict() for segment in self.resolved_segments],
            "segment_passes": [record.to_dict() for record in self.segment_passes],
            "validation_report": self.validation_report,
            "repair_hints": self.repair_hints,
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
    resolved_segments, overview_repairs = resolve_overview_segments(overview.data, text)
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
        segment_passes.append(
            run_text_segment_extraction_pass(
                parent_unit=unit,
                segment=segment,
                context=segment_context,
                backend=llm,
                cache_dir=root_dir / "segments",
                use_cache=use_cache,
                generated_prompt_parts=generated_parts,
            )
        )
    validation_report = build_chain_validation_report(overview, resolved_segments, segment_passes)
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
    )
    if use_cache:
        write_chain_artifacts(root_dir, paths, record)
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
    validation_report = validate_overview_structure(data, text)
    record = JsonPassRecord(
        pass_name="overview-segmentation",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=data,
        validation_report=validation_report,
        artifact_paths=paths,
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


def run_text_segment_extraction_pass(
    *,
    parent_unit,
    segment: ResolvedOverviewSegment,
    context: ExtractionContext,
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool,
    generated_prompt_parts: list[PromptPart],
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
    if cache_hit:
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


def validate_overview_structure(data: dict[str, Any], text: str) -> dict[str, Any]:
    issues = []
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
                location = relocate_evidence_quote(
                    text,
                    quote,
                    evidence_id=f"{segment.get('segment_id') or index}:{field_name}",
                )
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
    }


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
    overview_data: dict[str, Any], text: str
) -> tuple[list[ResolvedOverviewSegment], list[dict[str, Any]]]:
    resolved = []
    repair_hints = []
    segments = overview_data.get("overview_segments") or []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id") or f"overview-segment-{index + 1:04d}")
        start_location = relocate_evidence_quote(
            text,
            str(segment.get("start_quote") or ""),
            evidence_id=f"{segment_id}:start_quote",
        )
        end_location = relocate_evidence_quote(
            text,
            str(segment.get("end_quote") or ""),
            evidence_id=f"{segment_id}:end_quote",
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


def build_chain_validation_report(
    overview: JsonPassRecord,
    resolved_segments: list[ResolvedOverviewSegment],
    segment_passes: list[ExtractionPassRecord],
) -> dict[str, Any]:
    segment_reports = [record.validation_report.to_dict() for record in segment_passes]
    error_count = overview.validation_report["error_count"] + sum(
        report["error_count"] for report in segment_reports
    )
    warning_count = overview.validation_report["warning_count"] + sum(
        report["warning_count"] for report in segment_reports
    )
    return {
        "passed": error_count == 0,
        "overview": overview.validation_report,
        "resolved_segment_count": len(resolved_segments),
        "segment_pass_count": len(segment_passes),
        "segment_lengths": [
            {
                "segment_id": segment.segment_id,
                "start": segment.start,
                "end": segment.end,
                **text_length_stats(segment.text),
            }
            for segment in resolved_segments
        ],
        "segment_reports": segment_reports,
        "error_count": error_count,
        "warning_count": warning_count,
    }


def build_chain_repair_hints(
    overview_repairs: list[dict[str, Any]],
    segment_passes: list[ExtractionPassRecord],
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    segment_repairs = []
    for record in segment_passes:
        payload = record.validation_report.to_repair_payload()
        if payload["quality_summary"]["issue_count"]:
            segment_repairs.append(
                {
                    "segment_id": record.result.unit_id,
                    "repair_payload": payload,
                    "result_path": record.artifact_paths["result"],
                    "validation_report_path": record.artifact_paths["validation_report"],
                }
            )
    return {
        "ready_for_llm_repair": bool(overview_repairs or segment_repairs),
        "overview_repairs": overview_repairs,
        "segment_repairs": segment_repairs,
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


def text_length_stats(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "nonempty_lines": sum(1 for line in text.splitlines() if line.strip()),
    }
