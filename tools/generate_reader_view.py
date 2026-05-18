#!/usr/bin/env python3
"""Generate a self-contained reader-view HTML file from extraction pipeline outputs.

Usage:
    python tools/generate_reader_view.py \\
        .tilusion_cache/units/unit-0002/unit_package.json \\
        .tilusion_cache/extraction_chains/<chain>/resolved_segments.json \\
        "books/Fu Sheng Liu Ji --...txt" \\
        -o reader_view.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tilusion.extraction_quality import relocate_evidence_quote


# ── Data loading ──────────────────────────────────────────────────────────

def load_package(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_segments(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_source_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_segment_evidence(chain_dir: str, segment_id: str) -> dict[str, str]:
    """Return {evidence_id: quote} for a segment by reading its result.json."""
    seg_dir = Path(chain_dir) / "segments" / segment_id
    if not seg_dir.exists():
        return {}
    for root, _dirs, files in os.walk(seg_dir):
        if "result.json" in files:
            with open(Path(root) / "result.json", encoding="utf-8") as f:
                data = json.load(f)
            spans = data.get("data", {}).get("evidence_spans", [])
            return {s["evidence_id"]: s.get("quote", "") for s in spans if isinstance(s, dict)}
    return {}


def load_segment_times(chain_dir: str, segment_id: str) -> list[dict[str, Any]]:
    """Return time expression records for a segment by reading its result.json."""
    seg_dir = Path(chain_dir) / "segments" / segment_id
    if not seg_dir.exists():
        return []
    for root, _dirs, files in os.walk(seg_dir):
        if "result.json" in files:
            with open(Path(root) / "result.json", encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("data", {}).get("time_expressions", [])
            return [record for record in records if isinstance(record, dict)]
    return []


def _segment_offsets(segments: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    offsets: dict[str, tuple[int, int]] = {}
    for seg in segments:
        sid = seg.get("segment_id")
        start = seg.get("start")
        end = seg.get("end")
        if isinstance(sid, str) and isinstance(start, int) and isinstance(end, int):
            offsets[sid] = (start, end)
    return offsets


def _locate_evidence_refs(
    source_text: str,
    segments: list[dict[str, Any]],
    evidence_by_segment: dict[str, dict[str, str]],
) -> dict[tuple[str, str], tuple[int, int]]:
    """Locate segment evidence refs and return full-source offsets."""
    locations: dict[tuple[str, str], tuple[int, int]] = {}
    offsets = _segment_offsets(segments)
    base_offset = _unit_base_offset(source_text, segments)
    for sid, evidence in evidence_by_segment.items():
        seg_offset = offsets.get(sid)
        if not seg_offset:
            continue
        seg_start, seg_end = seg_offset
        full_start = base_offset + seg_start
        full_end = base_offset + seg_end
        segment_text = source_text[full_start:full_end]
        for evidence_id, quote in evidence.items():
            if not quote:
                continue
            location = relocate_evidence_quote(
                segment_text,
                quote,
                evidence_id=evidence_id,
            )
            if location.start is None or location.end is None:
                continue
            locations[(sid, evidence_id)] = (
                full_start + location.start,
                full_start + location.end,
            )
    return locations


def _unit_base_offset(source_text: str, segments: list[dict[str, Any]]) -> int:
    """Infer full-source offset for unit-local segment offsets."""
    for seg in segments:
        seg_start = seg.get("start")
        source = seg.get("source") if isinstance(seg.get("source"), dict) else {}
        start_quote = source.get("start_quote") if isinstance(source, dict) else None
        local_start = (
            seg.get("start_location", {}).get("start")
            if isinstance(seg.get("start_location"), dict)
            else None
        )
        if not isinstance(seg_start, int) or not isinstance(start_quote, str):
            continue
        found = source_text.find(start_quote)
        if found >= 0:
            return found - (local_start if isinstance(local_start, int) else seg_start)
    return 0


# ── Text annotation ────────────────────────────────────────────────────────

def _find_all(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Return all (start, end) positions of needle in haystack (non-overlapping)."""
    results = []
    pos = 0
    while True:
        idx = haystack.find(needle, pos)
        if idx == -1:
            break
        results.append((idx, idx + len(needle)))
        pos = idx + 1
    return results


# ── Source pane ────────────────────────────────────────────────────────────


def build_source_html(
    source_text: str,
    segments: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    events: list[dict[str, Any]],
    evidence_by_segment: dict[str, dict[str, str]],
    times_by_segment: dict[str, list[dict[str, Any]]],
) -> str:
    """Build the full source pane HTML with entity/location annotations.

    Renders the entire source text as a continuous scrollable block with
    entity and location surfaces highlighted inline. Event evidence is
    included as data for right-pane navigation, but not visible by default.
    """
    annotations: list[dict[str, Any]] = []
    evidence_locations = _locate_evidence_refs(source_text, segments, evidence_by_segment)
    offsets = _segment_offsets(segments)
    base_offset = _unit_base_offset(source_text, segments)

    # Entity surfaces
    for ent in entities:
        for surface in ent.get("surfaces", []):
            if not surface or len(surface) < 2:
                continue
            for start, end in _find_all(source_text, surface):
                annotations.append({"start": start, "end": end, "kind": "entity", "id": ent["entity_id"]})

    # Location surfaces
    for loc in locations:
        for surface in loc.get("surfaces", []):
            if not surface or len(surface) < 2:
                continue
            for start, end in _find_all(source_text, surface):
                annotations.append({"start": start, "end": end, "kind": "location", "id": loc["location_id"]})

    # Segment-scoped time expressions. Locate within segment windows to avoid
    # over-marking repeated generic surfaces such as "是年".
    for sid, time_records in times_by_segment.items():
        seg_offset = offsets.get(sid)
        if not seg_offset:
            continue
        seg_start, seg_end = seg_offset
        full_start = base_offset + seg_start
        full_end = base_offset + seg_end
        segment_text = source_text[full_start:full_end]
        for time_record in time_records:
            surface = time_record.get("surface")
            time_id = time_record.get("time_expression_id")
            if not isinstance(surface, str) or not surface or len(surface) < 2:
                continue
            if not isinstance(time_id, str):
                time_id = surface
            for local_start, local_end in _find_all(segment_text, surface):
                annotations.append({
                    "start": full_start + local_start,
                    "end": full_start + local_end,
                    "kind": "time",
                    "id": f"{sid}:{time_id}",
                })

    # Event evidence locations are navigation targets, not source-side marks.
    for ev in events:
        for ref in ev.get("evidence_refs", []):
            seg_id = ref.get("segment_id", "")
            evidence_id = ref.get("evidence_id", "")
            located = evidence_locations.get((seg_id, evidence_id))
            if not located:
                continue
            annotations.append({"start": located[0], "end": located[1], "kind": "event", "id": ev["event_id"]})

    boundaries = {0, len(source_text)}
    for item in annotations:
        boundaries.add(item["start"])
        boundaries.add(item["end"])
    cuts = sorted(boundaries)

    # Build marked-up HTML with paragraph breaks on newlines
    parts: list[str] = []
    for start, end in zip(cuts, cuts[1:]):
        if start == end:
            continue
        chunk = html.escape(source_text[start:end]).replace("\n\n", "</p><p>").replace("\n", "<br>")
        covered = [item for item in annotations if item["start"] <= start and item["end"] >= end]
        if not covered:
            parts.append(chunk)
            continue

        entity_ids = sorted({item["id"] for item in covered if item["kind"] == "entity"})
        location_ids = sorted({item["id"] for item in covered if item["kind"] == "location"})
        time_ids = sorted({item["id"] for item in covered if item["kind"] == "time"})
        event_ids = sorted({item["id"] for item in covered if item["kind"] == "event"})
        classes = ["source-mark"]
        attrs = []
        if entity_ids:
            classes.append("entity")
            attrs.append(f'data-entities="{html.escape(",".join(entity_ids))}"')
        if location_ids:
            classes.append("location")
            attrs.append(f'data-locations="{html.escape(",".join(location_ids))}"')
        if time_ids:
            classes.append("time")
            attrs.append(f'data-times="{html.escape(",".join(time_ids))}"')
        if event_ids:
            attrs.append(f'data-events="{html.escape(",".join(event_ids))}"')
        parts.append(f'<mark class="{" ".join(classes)}" {" ".join(attrs)}>{chunk}</mark>')

    body = "".join(parts)
    # Wrap in paragraphs; collapse empty <p></p>
    body = f"<p>{body}</p>"
    # Clean up empty paragraphs from consecutive newlines
    body = body.replace("<p></p>", "")

    return (
        '<div class="source-text">'
        + body
        + "</div>"
    )


# ── Timeline pane ──────────────────────────────────────────────────────────

def _entity_label(eid: str, entities_by_id: dict[str, dict[str, Any]]) -> str:
    ent = entities_by_id.get(eid)
    if not ent:
        return html.escape(eid)
    return html.escape(ent.get("canonical_name", eid))


def _location_label(lid: str, locations_by_id: dict[str, dict[str, Any]]) -> str:
    loc = locations_by_id.get(lid)
    if not loc:
        return html.escape(lid)
    return html.escape(loc.get("canonical_name", lid))


def build_timeline_nav(
    timelines: list[dict[str, Any]],
) -> str:
    """Build timeline sidebar nav items. Returns nav HTML string."""
    nav_parts = []
    for tl in timelines:
        tid = tl["timeline_id"]
        conf = tl.get("confidence", "medium")
        nav_parts.append(
            f'<button class="nav-item" data-timeline="{html.escape(tid)}">'
            f'<span class="conf-dot {html.escape(conf)}"></span>'
            f"{html.escape(tid)}"
            f"</button>"
        )
    return "\n".join(nav_parts)


def build_thread_nav(
    threads: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> str:
    """Build thread sidebar nav items. Returns nav HTML string."""
    # Pre-compute event→segment mapping for counting
    event_segment_sets = [
        set(ev.get("segment_ids", []) or []) for ev in events
    ]
    nav_parts: list[str] = []
    for thread in threads:
        tid = thread["thread_id"]
        thread_segments = set(thread.get("segment_ids", []) or [])
        event_count = sum(1 for ess in event_segment_sets if ess & thread_segments)
        summary = thread.get("summary", tid)
        nav_parts.append(
            f'<button class="nav-item" data-thread="{html.escape(tid)}" title="{html.escape(summary)} ({event_count} events)">'
            f'{html.escape(summary)}'
            f'<span style="color:var(--muted);margin-left:6px;font-size:9px">{event_count}</span>'
            f'</button>'
        )
    return "\n".join(nav_parts)


# ── Data embedding ─────────────────────────────────────────────────────────

def build_data_script(
    entities: list[dict[str, Any]],
    events: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    timelines: list[dict[str, Any]],
) -> str:
    """Return a <script> block embedding structured data as JSON."""
    # Slim down to fields the frontend actually uses
    slim_entities = [
        {k: e[k] for k in ("entity_id", "canonical_name", "surfaces", "kind", "summary") if k in e}
        for e in entities
    ]
    slim_events = [
        {k: ev[k] for k in ("event_id", "summary", "segment_ids", "source_order_hint",
                             "participant_entity_ids", "location_ids", "evidence_refs",
                             "qc_notes", "time_refs", "confidence") if k in ev}
        for ev in events
    ]
    slim_locations = [
        {k: l[k] for k in ("location_id", "canonical_name", "surfaces", "kind", "summary") if k in l}
        for l in locations
    ]
    slim_threads = [
        {k: t[k] for k in ("thread_id", "summary", "status", "segment_ids", "evidence_refs") if k in t}
        for t in threads
    ]
    # Timelines already have the right shape (timeline_id, summary, confidence, ordered_events)

    payload = {
        "entities": slim_entities,
        "events": slim_events,
        "locations": slim_locations,
        "threads": slim_threads,
        "timelines": timelines,
    }
    return (
        '<script type="application/json" id="readerData">\n'
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n</script>"
    )


# ── Assembly ───────────────────────────────────────────────────────────────

def render(template: str, replacements: dict[str, str]) -> str:
    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def generate(
    template_path: str,
    package_path: str,
    segments_path: str,
    book_path: str,
    output_path: str,
) -> None:
    # Load template
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    # Load data
    package = load_package(package_path)
    segments = load_segments(segments_path)
    source_text = load_source_text(book_path)

    data = package.get("data", package)
    entities: list[dict[str, Any]] = data.get("entity_records", [])
    events: list[dict[str, Any]] = data.get("event_records", [])
    locations: list[dict[str, Any]] = data.get("location_records", [])
    threads: list[dict[str, Any]] = data.get("thread_records", [])
    timelines: list[dict[str, Any]] = data.get("timelines", [])
    unresolved: list[dict[str, Any]] = data.get("unresolved_items", [])

    unit_id = data.get("unit_id", package.get("unit_id", "unknown"))
    book_title = Path(book_path).stem

    # Build lookups
    events_by_id = {e["event_id"]: e for e in events}
    entities_by_id = {e["entity_id"]: e for e in entities}
    locations_by_id = {l["location_id"]: l for l in locations}
    threads_by_id = {t["thread_id"]: t for t in threads}

    # Load evidence quotes for all segments
    chain_dir = str(Path(segments_path).parent)
    evidence_by_segment: dict[str, dict[str, str]] = {}
    times_by_segment: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        sid = seg["segment_id"]
        evidence_by_segment[sid] = load_segment_evidence(chain_dir, sid)
        times_by_segment[sid] = load_segment_times(chain_dir, sid)

    # Build components
    source_html = build_source_html(
        source_text,
        segments,
        entities,
        locations,
        events,
        evidence_by_segment,
        times_by_segment,
    )
    timeline_nav_html = build_timeline_nav(timelines)
    thread_nav_html = build_thread_nav(threads, events)
    data_script = build_data_script(entities, events, locations, threads, timelines)

    # Build JS constants (replace DATA_PLACEHOLDER_* with real JSON arrays)
    js_constants = (
        '<script>\n'
        f'const EVENTS = {json.dumps([{k: ev[k] for k in ("event_id","summary","segment_ids","source_order_hint","participant_entity_ids","location_ids","evidence_refs","qc_notes","time_refs","confidence") if k in ev} for ev in events], ensure_ascii=False)};\n'
        f'const ENTITIES = {json.dumps([{k: e[k] for k in ("entity_id","canonical_name","surfaces","kind","summary") if k in e} for e in entities], ensure_ascii=False)};\n'
        f'const LOCATIONS = {json.dumps([{k: l[k] for k in ("location_id","canonical_name","surfaces","kind","summary") if k in l} for l in locations], ensure_ascii=False)};\n'
        f'const THREADS = {json.dumps([{k: t[k] for k in ("thread_id","summary","status","segment_ids","evidence_refs") if k in t} for t in threads], ensure_ascii=False)};\n'
        f'const TIMELINES = {json.dumps(timelines, ensure_ascii=False)};\n'
        '</script>'
    )

    replacements = {
        "{{UNIT_ID}}": html.escape(unit_id),
        "{{BOOK_TITLE}}": html.escape(book_title),
        "{{ENTITY_COUNT}}": str(len(entities)),
        "{{LOCATION_COUNT}}": str(len(locations)),
        "{{EVENT_COUNT}}": str(len(events)),
        "{{TIMELINE_COUNT}}": str(len(timelines)),
        "{{THREAD_COUNT}}": str(len(threads)),
        "{{UNRESOLVED_COUNT}}": str(len(unresolved)),
        "<!-- SOURCE_HTML -->": source_html,
        "<!-- TIMELINE_NAV -->": timeline_nav_html,
        "<!-- THREAD_NAV -->": thread_nav_html,
        "<!-- TIMELINE_HTML -->": (
            '<div id="timelineContent" class="tl-content">'
            '<svg id="timelineGraph" class="tl-graph"></svg>'
            '<div id="threadView" class="thread-view" style="display:none">'
            '<div class="thread-view-header" id="threadViewHeader"></div>'
            '<div class="thread-card-stack" id="threadCardStack"></div>'
            '</div>'
            '</div>'
        ),
        "<!-- DATA_SCRIPT -->": js_constants,
        "const EVENTS = DATA_PLACEHOLDER_EVENTS;": "",
        "const ENTITIES = DATA_PLACEHOLDER_ENTITIES;": "",
        "const LOCATIONS = DATA_PLACEHOLDER_LOCATIONS;": "",
        "const THREADS = DATA_PLACEHOLDER_THREADS;": "",
        "const TIMELINES = DATA_PLACEHOLDER_TIMELINES;": "",
    }

    rendered = render(template, replacements)
    # Remove leftover placeholder lines
    for placeholder_line in [
        "const EVENTS = DATA_PLACEHOLDER_EVENTS;",
        "const ENTITIES = DATA_PLACEHOLDER_ENTITIES;",
        "const LOCATIONS = DATA_PLACEHOLDER_LOCATIONS;",
        "const THREADS = DATA_PLACEHOLDER_THREADS;",
        "const TIMELINES = DATA_PLACEHOLDER_TIMELINES;",
    ]:
        rendered = rendered.replace(placeholder_line, "")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    print(f"Wrote {output_path}")
    print(f"  Entities: {len(entities)}")
    print(f"  Locations: {len(locations)}")
    print(f"  Events: {len(events)}")
    print(f"  Threads: {len(threads)}")
    print(f"  Timelines: {len(timelines)}")
    print(f"  Unresolved: {len(unresolved)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a self-contained reader view HTML file.")
    parser.add_argument("package", help="Path to unit_package.json")
    parser.add_argument("segments", help="Path to resolved_segments.json")
    parser.add_argument("book", help="Path to the source book .txt file")
    parser.add_argument("-o", "--output", default="reader_view.html", help="Output HTML path (default: reader_view.html)")
    parser.add_argument("--template", default=None, help="Path to template HTML (default: tools/reader_view_template.html)")
    args = parser.parse_args()

    template_path = args.template
    if template_path is None:
        # Default: template next to this script
        template_path = str(Path(__file__).parent / "reader_view_template.html")

    if not Path(template_path).exists():
        print(f"Error: template not found at {template_path}", file=sys.stderr)
        sys.exit(1)

    generate(
        template_path=template_path,
        package_path=args.package,
        segments_path=args.segments,
        book_path=args.book,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
