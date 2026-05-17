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


def _resolve_overlaps(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort spans by (start, -length) so longer matches win; remove overlaps."""
    if not spans:
        return []
    sorted_spans = sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"])))
    resolved = []
    last_end = -1
    for s in sorted_spans:
        if s["start"] >= last_end:
            resolved.append(s)
            last_end = s["end"]
    return sorted(resolved, key=lambda s: s["start"])


# ── Source pane ────────────────────────────────────────────────────────────


def build_source_html(
    source_text: str,
    segments: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    events: list[dict[str, Any]],
    evidence_by_segment: dict[str, dict[str, str]],
) -> str:
    """Build the full source pane HTML with entity/event highlights.

    Renders the entire source text as a continuous scrollable block with
    entity surfaces and event evidence quotes highlighted inline. Segment
    boundaries are not shown — the timeline pane carries the structure.
    """
    # Collect all highlight spans across the entire source text
    spans: list[dict[str, Any]] = []

    # Entity surfaces
    for ent in entities:
        for surface in ent.get("surfaces", []):
            if not surface or len(surface) < 2:
                continue
            for start, end in _find_all(source_text, surface):
                spans.append({
                    "start": start, "end": end,
                    "cls": "entity",
                    "attrs": f'data-entity="{html.escape(ent["entity_id"])}"',
                })

    # Event evidence quotes
    for ev in events:
        for ref in ev.get("evidence_refs", []):
            seg_id = ref.get("segment_id", "")
            ev_quotes = evidence_by_segment.get(seg_id, {})
            quote = ev_quotes.get(ref.get("evidence_id", ""), "")
            if not quote or len(quote) < 3:
                continue
            for start, end in _find_all(source_text, quote):
                spans.append({
                    "start": start, "end": end,
                    "cls": "event",
                    "attrs": f'data-event="{html.escape(ev["event_id"])}" data-segment="{html.escape(seg_id)}"',
                })

    resolved = _resolve_overlaps(spans)

    # Build marked-up HTML with paragraph breaks on newlines
    parts: list[str] = []
    cursor = 0
    for s in resolved:
        if s["start"] > cursor:
            chunk = source_text[cursor:s["start"]]
            # Preserve paragraph structure
            chunk = html.escape(chunk).replace("\n\n", "</p><p>").replace("\n", "<br>")
            parts.append(chunk)
        parts.append(f'<mark class="{s["cls"]}" {s["attrs"]}>{html.escape(source_text[s["start"]:s["end"]])}</mark>')
        cursor = s["end"]
    if cursor < len(source_text):
        chunk = source_text[cursor:]
        chunk = html.escape(chunk).replace("\n\n", "</p><p>").replace("\n", "<br>")
        parts.append(chunk)

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


def build_timeline_html(
    timelines: list[dict[str, Any]],
    events_by_id: dict[str, dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    locations_by_id: dict[str, dict[str, Any]],
    threads_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """Build timeline sections HTML and tabs HTML. Returns (tabs_html, timeline_html)."""
    tab_parts = []
    section_parts = []

    for tl in timelines:
        tid = tl["timeline_id"]
        conf = tl.get("confidence", "medium")
        summary = tl.get("summary", tid)
        ordered = tl.get("ordered_events", [])

        # Tab
        tab_parts.append(
            f'<button class="tl-tab" data-timeline="{html.escape(tid)}">'
            f'<span class="conf-dot {html.escape(conf)}"></span>'
            f"{html.escape(tid)}"
            f"</button>"
        )

        # Section
        cards = []
        for entry in ordered:
            eid = entry.get("event_id", "")
            ev = events_by_id.get(eid)
            if not ev:
                continue
            before = entry.get("before_events", []) or []
            rationale = entry.get("rationale", "")

            tags = []
            # Participants
            for peid in ev.get("participant_entity_ids", []) or []:
                tags.append(
                    f'<span class="tag entity-tag" data-entity="{html.escape(peid)}">'
                    f"{_entity_label(peid, entities_by_id)}</span>"
                )
            # Locations
            for lid in ev.get("location_ids", []) or []:
                tags.append(
                    f'<span class="tag location-tag">'
                    f"{_location_label(lid, locations_by_id)}</span>"
                )
            # Confidence
            ev_conf = ev.get("confidence", "")
            if ev_conf:
                tags.append(f'<span class="tag conf-tag {html.escape(ev_conf)}">{html.escape(ev_conf)}</span>')
            # QC notes flag
            if ev.get("qc_notes"):
                tags.append('<span class="tag" style="color:#9a4a3f">needs review</span>')
            # before_edges
            for beid in before:
                tags.append(
                    f'<span class="tag before-tag">&rarr; {html.escape(beid)}</span>'
                )
            # Thread associations
            for th in threads_by_id.values():
                th_sids = th.get("segment_ids", []) or []
                ev_sids = ev.get("segment_ids", []) or []
                if set(th_sids) & set(ev_sids):
                    tags.append(
                        f'<span class="tag thread-tag" data-thread="{html.escape(th["thread_id"])}">'
                        f"{html.escape(th.get('summary', th['thread_id']) )}</span>"
                    )
                    break  # one thread tag per card is enough

            cards.append(
                f'<article class="event-card" data-event="{html.escape(eid)}">'
                f'<div class="ev-head"><span class="ev-id">{html.escape(eid)}</span></div>'
                f'<div class="ev-summary">{html.escape(ev.get("summary", eid))}</div>'
                + (f'<div class="ev-rationale">{html.escape(rationale)}</div>' if rationale else "")
                + f'<div class="meta-row">{"".join(tags)}</div>'
                f"</article>"
            )

        section_parts.append(
            f'<div class="tl-section" data-timeline="{html.escape(tid)}">'
            f'<div class="tl-section-header">'
            f'<h3>{html.escape(summary)}</h3>'
            f'<div class="tl-meta">{html.escape(tid)} · {len(cards)} events · confidence: {html.escape(conf)}</div>'
            f"</div>"
            + "\n".join(cards)
            + "</div>"
        )

    return "\n".join(tab_parts), "\n".join(section_parts)


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
                             "participant_entity_ids", "location_ids", "qc_notes") if k in ev}
        for ev in events
    ]
    slim_locations = [
        {k: l[k] for k in ("location_id", "canonical_name", "kind", "summary") if k in l}
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
    for seg in segments:
        sid = seg["segment_id"]
        evidence_by_segment[sid] = load_segment_evidence(chain_dir, sid)

    # Build components
    source_html = build_source_html(source_text, segments, entities, events, evidence_by_segment)
    tabs_html, timeline_html = build_timeline_html(
        timelines, events_by_id, entities_by_id, locations_by_id, threads_by_id,
    )
    data_script = build_data_script(entities, events, locations, threads, timelines)

    # Build JS constants (replace DATA_PLACEHOLDER_* with real JSON arrays)
    js_constants = (
        '<script>\n'
        f'const EVENTS = {json.dumps([{k: ev[k] for k in ("event_id","summary","segment_ids","source_order_hint","participant_entity_ids","location_ids","qc_notes") if k in ev} for ev in events], ensure_ascii=False)};\n'
        f'const ENTITIES = {json.dumps([{k: e[k] for k in ("entity_id","canonical_name","surfaces","kind","summary") if k in e} for e in entities], ensure_ascii=False)};\n'
        f'const LOCATIONS = {json.dumps([{k: l[k] for k in ("location_id","canonical_name","kind","summary") if k in l} for l in locations], ensure_ascii=False)};\n'
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
        "<!-- TIMELINE_TABS -->": tabs_html,
        "<!-- TIMELINE_HTML -->": timeline_html,
        "<!-- DATA_SCRIPT -->": js_constants,
        "const EVENTS = DATA_PLACEHOLDER_EVENTS;": "",
        "const ENTITIES = DATA_PLACEHOLDER_ENTITIES;": "",
        "const LOCATIONS = DATA_PLACEHOLDER_LOCATIONS;": "",
        "const THREADS = DATA_PLACEHOLDER_THREADS;": "",
        "const TIMELINES = DATA_PLACEHOLDER_TIMELINES;": "",
        "const EVENT_BY_ID = Object.fromEntries(EVENTS.map(e => [e.event_id, e]));": "",
        "const ENTITY_BY_ID = Object.fromEntries(ENTITIES.map(e => [e.entity_id, e]));": "",
        "const LOCATION_BY_ID = Object.fromEntries(LOCATIONS.map(l => [l.location_id, l]));": "",
        "const THREAD_BY_ID = Object.fromEntries(THREADS.map(t => [t.thread_id, t]));": "",
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
