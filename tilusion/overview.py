from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .backend import (
    DEFAULT_MAX_TOKENS,
    check_extraction_budget,
    parse_json_response,
    sha256_json,
    sha256_text,
)
from .extraction_prompts import build_overview_composition
from .extraction_quality import EvidenceLocation, relocate_evidence_quote
from .pass_utils import (
    PromptComposition,
    build_pass_cache_key,
    json_pass_artifact_paths,
    text_length_stats,
)


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


def run_overview_segmentation_pass(
    *,
    unit,
    text: str,
    backend,
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
    # De-overlap: ensure segments are disjoint.
    resolved.sort(key=lambda s: s.start)
    for i in range(len(resolved) - 1):
        if resolved[i].end > resolved[i + 1].start:
            resolved[i].end = resolved[i + 1].start
            resolved[i].text = text[resolved[i].start : resolved[i].end]

    # Extend last segment to end of text.
    if resolved:
        last = resolved[-1]
        text_end = len(text)
        if last.end < text_end:
            last.end = text_end
            last.text = text[last.start : text_end]

    # Drop segments that became empty after de-overlapping.
    resolved = [s for s in resolved if s.start < s.end]

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
