from __future__ import annotations

from typing import Any


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
    event_records = repaired_data.get("event_records", [])
    segment_results = payload.get("segment_results", [])
    enriched_events = _enrich_atom_time_refs(event_records, segment_results)
    payload["unit_records"] = {
        "entity_records": repaired_data.get("entity_records", []),
        "location_records": repaired_data.get("location_records", []),
        "event_records": enriched_events,
        "thread_records": repaired_data.get("thread_records", []),
    }
    if repaired_data.get("unresolved_items"):
        payload["quality_context"] = {
            "unresolved_items": repaired_data["unresolved_items"],
            "warnings": repaired_data.get("warnings", []),
        }
    payload["task"] = "unit_timeline"
    return payload


def _enrich_atom_time_refs(
    event_records: list[dict[str, Any]],
    segment_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve time_refs into inline time expression data on each atom record.

    Builds a lookup of (segment_id, time_expression_id) → {surface, normalized_hint}
    from segment_results, then enriches each atom's time_refs inline so the
    timeline LLM sees the actual temporal content without cross-referencing.

    Atoms with null time_refs (atemporal) pass through unchanged.
    """
    time_expr_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for seg in segment_results:
        sid = seg.get("segment_id", "")
        for te in seg.get("time_expressions", []) or []:
            te_id = te.get("time_expression_id")
            if te_id:
                time_expr_lookup[(sid, te_id)] = {
                    "surface": te.get("surface"),
                    "normalized_hint": te.get("normalized_hint"),
                }

    enriched: list[dict[str, Any]] = []
    for ev in event_records:
        ev = dict(ev)
        time_refs = ev.get("time_refs")
        if time_refs is None:
            enriched.append(ev)
            continue
        resolved = []
        for tr in time_refs or []:
            key = (tr.get("segment_id", ""), tr.get("time_expression_id", ""))
            te_data = time_expr_lookup.get(key)
            if te_data:
                resolved.append({**tr, **te_data})
            else:
                resolved.append(dict(tr))
        ev["time_refs"] = resolved
        enriched.append(ev)
    return enriched


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
        "atom_mentions": data.get("atom_mentions", []),
        "time_expressions": data.get("time_expressions", []),
        "thread_candidates": data.get("thread_candidates", []),
        "warnings": data.get("warnings", []),
    }
