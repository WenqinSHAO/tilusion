from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .extraction import sha256_json, sha256_text

# ── registry building ──────────────────────────────────────────────────


def build_registry_from_packages(
    package_paths: list[str | Path],
) -> dict[str, Any]:
    """Merge entity, location, thread, event, timeline records from unit packages into a book registry.

    Returns a registry dict keyed by type with canonical records.
    """
    registry: dict[str, Any] = {
        "entities": {},
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    for pp in package_paths:
        pkg = json.loads(Path(pp).read_text(encoding="utf-8"))
        data = pkg.get("data", {})
        _merge_entity_records(registry, data.get("entity_records", []))
        _merge_location_records(registry, data.get("location_records", []))
        _merge_thread_records(registry, data.get("thread_records", []))
        _merge_event_records(registry, data.get("event_records", []))
        _merge_timeline_records(registry, data.get("timelines", []))
    return registry


def _merge_entity_records(registry: dict[str, Any], records: list[dict[str, Any]]) -> None:
    for rec in records:
        eid = rec["entity_id"]
        if eid not in registry["entities"]:
            registry["entities"][eid] = {
                "entity_id": eid,
                "canonical_name": rec.get("canonical_name", ""),
                "surfaces": list(rec.get("surfaces", [])),
                "aliases": rec.get("aliases", []),
                "kind": rec.get("kind", ""),
                "summary": rec.get("summary", ""),
                "first_seen_unit": rec.get("_source_unit"),
            }


def _merge_location_records(registry: dict[str, Any], records: list[dict[str, Any]]) -> None:
    for rec in records:
        lid = rec["location_id"]
        if lid not in registry["locations"]:
            registry["locations"][lid] = {
                "location_id": lid,
                "canonical_name": rec.get("canonical_name", ""),
                "surfaces": list(rec.get("surfaces", [])),
                "aliases": rec.get("aliases", []),
                "kind": rec.get("kind", ""),
                "summary": rec.get("summary", ""),
                "first_seen_unit": rec.get("_source_unit"),
            }


def _merge_thread_records(registry: dict[str, Any], records: list[dict[str, Any]]) -> None:
    for rec in records:
        tid = rec["thread_id"]
        if tid not in registry["threads"]:
            registry["threads"][tid] = {
                "thread_id": tid,
                "summary": rec.get("summary", ""),
                "status": rec.get("status", ""),
                "related_entity_ids": list(rec.get("related_entity_ids", [])),
                "event_ids": list(rec.get("event_ids", [])),
                "first_seen_unit": rec.get("_source_unit"),
            }


def _merge_event_records(registry: dict[str, Any], records: list[dict[str, Any]]) -> None:
    for rec in records:
        eid = rec["event_id"]
        if eid not in registry["events"]:
            registry["events"][eid] = {
                "event_id": eid,
                "summary": rec.get("summary", ""),
                "participant_entity_ids": list(rec.get("participant_entity_ids", [])),
                "location_ids": list(rec.get("location_ids", [])),
                "thread_id": rec.get("thread_id"),
                "timeline_id": rec.get("timeline_id"),
                "source_order_hint": rec.get("source_order_hint"),
                "confidence": rec.get("confidence"),
                "first_seen_unit": rec.get("_source_unit"),
            }


def _merge_timeline_records(registry: dict[str, Any], records: list[dict[str, Any]]) -> None:
    for rec in records:
        tid = rec["timeline_id"]
        if tid not in registry["timelines"]:
            registry["timelines"][tid] = {
                "timeline_id": tid,
                "summary": rec.get("summary", ""),
                "confidence": rec.get("confidence"),
                "ordered_events": list(rec.get("ordered_events", [])),
                "time_anchors": list(rec.get("time_anchors", [])),
                "first_seen_unit": rec.get("_source_unit"),
            }


# ── surface scanner ────────────────────────────────────────────────────


def scan_unit_text_for_surfaces(
    text: str,
    registry: dict[str, Any],
    *,
    min_surface_length: int = 2,
) -> dict[str, Any]:
    """Deterministic lexical scan of unit text against registry surfaces.

    Returns a scan report with matched hits and unmatched surfaces.
    Single-character surfaces are skipped (too many false positives in CJK text).
    """
    # Build a flat list of (surface, entity_id_or_location_id, type, record) for scannable surfaces
    needles: list[tuple[str, str, str, dict[str, Any]]] = []
    for eid, rec in registry.get("entities", {}).items():
        for s in rec.get("surfaces", []):
            if len(s) >= min_surface_length:
                needles.append((s, eid, "entity", rec))
    for lid, rec in registry.get("locations", {}).items():
        for s in rec.get("surfaces", []):
            if len(s) >= min_surface_length:
                needles.append((s, lid, "location", rec))

    # Sort by surface length descending so longer matches take priority
    needles.sort(key=lambda x: -len(x[0]))

    # Find all matches
    raw_matches: list[dict[str, Any]] = []
    for surface, record_id, rec_type, rec in needles:
        start = 0
        while True:
            pos = text.find(surface, start)
            if pos == -1:
                break
            raw_matches.append(
                {
                    "surface": surface,
                    "record_id": record_id,
                    "record_type": rec_type,
                    "canonical_name": rec.get("canonical_name", ""),
                    "start_char": pos,
                    "end_char": pos + len(surface),
                    "surface_length": len(surface),
                    "ambiguous_short": len(surface) <= 2,
                }
            )
            start = pos + 1

    # Sort by position, then by length descending
    raw_matches.sort(key=lambda m: (m["start_char"], -m["surface_length"]))

    # Resolve overlaps: keep longer match, discard shorter that overlap
    resolved: list[dict[str, Any]] = []
    for m in raw_matches:
        # Check if this match overlaps with any already-resolved match
        overlap = False
        for r in resolved:
            if m["start_char"] < r["end_char"] and m["end_char"] > r["start_char"]:
                overlap = True
                break
        if not overlap:
            resolved.append(m)

    # Group by record_id
    by_record: dict[str, dict[str, Any]] = {}
    for m in resolved:
        rid = m["record_id"]
        if rid not in by_record:
            by_record[rid] = {
                "record_id": rid,
                "record_type": m["record_type"],
                "canonical_name": m["canonical_name"],
                "matched_surfaces": [],
                "match_count": 0,
                "ambiguous_short_count": 0,
            }
        by_record[rid]["matched_surfaces"].append(
            {
                "surface": m["surface"],
                "start_char": m["start_char"],
                "end_char": m["end_char"],
                "ambiguous_short": m["ambiguous_short"],
            }
        )
        by_record[rid]["match_count"] += 1
        if m["ambiguous_short"]:
            by_record[rid]["ambiguous_short_count"] += 1

    # Identify known surfaces not found in this unit
    all_scanned: set[str] = set()
    for _, record_id, rec_type, _ in needles:
        all_scanned.add((rec_type, record_id))
    matched_ids: set[tuple[str, str]] = set()
    for m in resolved:
        matched_ids.add((m["record_type"], m["record_id"]))
    surfaces_not_matched: list[dict[str, Any]] = []
    for rec_type, record_id in all_scanned - matched_ids:
        rec = (
            registry.get("entities", {}).get(record_id)
            or registry.get("locations", {}).get(record_id)
            or {}
        )
        surfaces_not_matched.append(
            {
                "record_type": rec_type,
                "record_id": record_id,
                "canonical_name": rec.get("canonical_name", ""),
                "surfaces": rec.get("surfaces", []),
            }
        )

    return {
        "matched_records": sorted(by_record.values(), key=lambda r: -r["match_count"]),
        "surfaces_not_matched": surfaces_not_matched,
        "total_matches": len(resolved),
        "scannable_surfaces": len(needles),
        "min_surface_length": min_surface_length,
    }


# ── compact context builder ────────────────────────────────────────────


def build_compact_context_from_scan(
    scan_report: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Build the compact 'context' block from a surface scan report and registry."""
    matched = scan_report.get("matched_records", [])
    entity_records = []
    location_records = []
    for rec in matched:
        if rec["record_type"] == "entity":
            full = registry["entities"].get(rec["record_id"], {})
            entity_records.append(
                {
                    "entity_id": full.get("entity_id"),
                    "canonical_name": full.get("canonical_name"),
                    "kind": full.get("kind"),
                    "summary": full.get("summary"),
                    "matched_surfaces": rec["matched_surfaces"],
                    "match_count": rec["match_count"],
                }
            )
        elif rec["record_type"] == "location":
            full = registry["locations"].get(rec["record_id"], {})
            location_records.append(
                {
                    "location_id": full.get("location_id"),
                    "canonical_name": full.get("canonical_name"),
                    "kind": full.get("kind"),
                    "summary": full.get("summary"),
                    "matched_surfaces": rec["matched_surfaces"],
                    "match_count": rec["match_count"],
                }
            )

    thread_summaries = []
    for tid, t in registry.get("threads", {}).items():
        thread_summaries.append(
            {
                "thread_id": t["thread_id"],
                "summary": t["summary"],
                "status": t["status"],
            }
        )

    return {
        "entities": entity_records,
        "locations": location_records,
        "active_threads": thread_summaries,
        "recent_events": [],
        "landmark_events": [],
        "time_anchors": [],
        "arc_summaries": [],
    }


BOOK_CONTEXT_SCHEMA_VERSION = "book-context-v0.1"
CONTEXT_SELECTION_POLICY = "passive-context-v0.1"


def stable_book_id(book_path: str | Path) -> str:
    """Return a deterministic local cache id for a book path.

    This is intentionally local-path based for the first passive scaffold. It
    avoids hashing large book files and can be replaced later by a stronger
    content/index identity without changing the artifact layout.
    """
    normalized = str(Path(book_path).expanduser().resolve())
    return f"book-{sha256_text(normalized)[:16]}"


def book_cache_dir(cache_root: str | Path, book_id: str) -> Path:
    return Path(cache_root) / "books" / book_id


def context_pack_dir(cache_root: str | Path, book_id: str, unit_id: str) -> Path:
    return book_cache_dir(cache_root, book_id) / "context_packs" / unit_id


def build_empty_book_state_snapshot(book_path: str | Path) -> dict[str, Any]:
    book_id = stable_book_id(book_path)
    base: dict[str, Any] = {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "book_id": book_id,
        "source": {
            "book_path": str(book_path),
            "identity_strategy": "local_resolved_path_v0",
        },
        "registry": {
            "entities": [],
            "locations": [],
            "threads": [],
            "events": [],
            "time_anchors": [],
            "timelines": [],
        },
        "indices": {
            "surfaces": {},
            "aliases": {},
            "active_threads": [],
        },
        "transactions": [],
    }
    snapshot_hash = sha256_json(base)
    return {
        **base,
        "snapshot_id": f"snapshot-{snapshot_hash[:16]}",
        "snapshot_hash": snapshot_hash,
    }


def build_passive_context_pack(
    book_path: str | Path,
    unit_id: str,
    *,
    source_length: dict[str, int] | None = None,
) -> dict[str, Any]:
    snapshot = build_empty_book_state_snapshot(book_path)
    base: dict[str, Any] = {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "selection_policy": CONTEXT_SELECTION_POLICY,
        "book_id": snapshot["book_id"],
        "target_unit_id": unit_id,
        "book_state_snapshot": {
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_hash": snapshot["snapshot_hash"],
        },
        "source_length": source_length or {},
        "prompt_injection": {
            "enabled": False,
            "reason": "passive scaffold only; not sent to LLM prompts yet",
        },
        "selection_summary": {
            "known_surface_hits": 0,
            "entities_included": 0,
            "locations_included": 0,
            "active_threads_included": 0,
            "recent_events_included": 0,
            "time_anchors_included": 0,
            "arc_summaries_included": 0,
            "excluded_counts": {},
        },
        "context": {
            "entities": [],
            "locations": [],
            "active_threads": [],
            "recent_events": [],
            "landmark_events": [],
            "time_anchors": [],
            "arc_summaries": [],
        },
        "selection_reasons": [],
    }
    context_pack_hash = sha256_json(base)
    return {
        **base,
        "context_pack_id": f"context-pack-{context_pack_hash[:16]}",
        "context_pack_hash": context_pack_hash,
    }


def build_context_pack_from_registry(
    book_path: str | Path,
    unit_id: str,
    unit_text: str,
    registry: dict[str, Any],
    *,
    source_length: dict[str, int] | None = None,
    min_surface_length: int = 2,
) -> dict[str, Any]:
    """Build a context pack using the deterministic surface scanner over a populated registry."""
    snapshot = build_empty_book_state_snapshot(book_path)
    # Populate registry into snapshot
    snapshot["registry"] = {
        "entities": list(registry.get("entities", {}).values()),
        "locations": list(registry.get("locations", {}).values()),
        "threads": list(registry.get("threads", {}).values()),
        "events": list(registry.get("events", {}).values()),
        "time_anchors": [],
        "timelines": list(registry.get("timelines", {}).values()),
    }

    scan = scan_unit_text_for_surfaces(unit_text, registry, min_surface_length=min_surface_length)
    compact_context = build_compact_context_from_scan(scan, registry)

    matched_entities = len([r for r in scan["matched_records"] if r["record_type"] == "entity"])
    matched_locations = len([r for r in scan["matched_records"] if r["record_type"] == "location"])
    not_matched_entities = len([r for r in scan["surfaces_not_matched"] if r["record_type"] == "entity"])
    not_matched_locations = len([r for r in scan["surfaces_not_matched"] if r["record_type"] == "location"])

    selection_policy = "cross-unit-context-v0.1"

    base: dict[str, Any] = {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "selection_policy": selection_policy,
        "book_id": snapshot["book_id"],
        "target_unit_id": unit_id,
        "book_state_snapshot": {
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_hash": snapshot["snapshot_hash"],
        },
        "source_length": source_length or {},
        "prompt_injection": {
            "enabled": False,
            "reason": "deterministic surface scanner only; not injected into LLM prompts yet",
        },
        "selection_summary": {
            "known_surface_hits": scan["total_matches"],
            "scannable_surfaces": scan["scannable_surfaces"],
            "entities_included": matched_entities,
            "locations_included": matched_locations,
            "active_threads_included": len(compact_context["active_threads"]),
            "recent_events_included": 0,
            "time_anchors_included": 0,
            "arc_summaries_included": 0,
            "excluded_counts": {
                "entities_not_matched": not_matched_entities,
                "locations_not_matched": not_matched_locations,
            },
        },
        "context": compact_context,
        "scan_report": {
            "matched_records": scan["matched_records"],
            "surfaces_not_matched": scan["surfaces_not_matched"],
            "total_matches": scan["total_matches"],
            "scannable_surfaces": scan["scannable_surfaces"],
        },
        "selection_reasons": [
            f"Surface scan found {matched_entities} entities and {matched_locations} locations in target unit text.",
            f"All {len(compact_context['active_threads'])} known threads included as compact summaries.",
            f"{not_matched_entities} entities and {not_matched_locations} locations from registry had no surface hits in target unit.",
        ],
    }
    context_pack_hash = sha256_json(base)
    return {
        **base,
        "context_pack_id": f"context-pack-{context_pack_hash[:16]}",
        "context_pack_hash": context_pack_hash,
    }


def build_context_selection_report(context_pack: dict[str, Any]) -> dict[str, Any]:
    scan_report = context_pack.get("scan_report")
    notes = []
    if scan_report:
        notes.append(
            f"Surface scanner found {scan_report.get('total_matches', 0)} matches "
            f"across {len(scan_report.get('matched_records', []))} records "
            f"from {scan_report.get('scannable_surfaces', 0)} scannable surfaces."
        )
    else:
        notes.append("Passive scaffold: no prior book context is selected yet.")
    notes.append("Future selector reports should explain every included registry record.")
    return {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "selection_policy": context_pack.get("selection_policy"),
        "book_id": context_pack.get("book_id"),
        "target_unit_id": context_pack.get("target_unit_id"),
        "context_pack_id": context_pack.get("context_pack_id"),
        "context_pack_hash": context_pack.get("context_pack_hash"),
        "prompt_injection": context_pack.get("prompt_injection", {}),
        "summary": context_pack.get("selection_summary", {}),
        "selection_reasons": context_pack.get("selection_reasons", []),
        "notes": notes,
    }


def write_passive_context_artifacts(
    *,
    book_path: str | Path,
    unit_id: str,
    cache_root: str | Path,
    source_length: dict[str, int] | None = None,
) -> dict[str, str]:
    snapshot = build_empty_book_state_snapshot(book_path)
    context_pack = build_passive_context_pack(
        book_path,
        unit_id,
        source_length=source_length,
    )
    selection_report = build_context_selection_report(context_pack)

    root = book_cache_dir(cache_root, snapshot["book_id"])
    snapshot_dir = root / "registry" / "snapshots"
    pack_dir = context_pack_dir(cache_root, snapshot["book_id"], unit_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshot_dir / f"{snapshot['snapshot_id']}.json"
    latest_path = root / "registry" / "latest.json"
    context_pack_path = pack_dir / "context_pack.json"
    selection_report_path = pack_dir / "context_selection_report.json"

    _write_json(snapshot_path, snapshot)
    _write_json(latest_path, snapshot)
    _write_json(context_pack_path, context_pack)
    _write_json(selection_report_path, selection_report)

    return {
        "book_state_snapshot": str(snapshot_path),
        "book_state_latest": str(latest_path),
        "context_pack": str(context_pack_path),
        "context_selection_report": str(selection_report_path),
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
