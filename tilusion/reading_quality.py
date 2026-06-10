from __future__ import annotations

import sys
from typing import Any

from .reading_schema import (
    RECOMMENDED_CONCEPT_TYPES,
    RECOMMENDED_EDGE_TYPES,
    RECOMMENDED_GROUP_TYPES,
    normalize_concept_type,
)


def _preview_text(value: Any, *, limit: int = 96) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


_STABLE_CANONICAL_TYPES = {
    "person",
    "group",
    "organization",
    "place",
    "term",
    "method",
    "theme",
    "motif",
    "institution",
    "symbol",
    "source",
    "technical_component",
    "dataset",
    "metric",
}


def compute_quality_metrics(data: dict[str, Any], *, reader_language: str = "zh-Hans") -> dict[str, Any]:
    source_blocks = data.get("source_blocks", []) if isinstance(data.get("source_blocks"), list) else []
    concepts = data.get("concepts", []) if isinstance(data.get("concepts"), list) else []
    items = data.get("atomic_items", []) if isinstance(data.get("atomic_items"), list) else []
    groups = data.get("logical_groups", []) if isinstance(data.get("logical_groups"), list) else []
    block_text = {
        str(block.get("block_id", "")): str(block.get("text", ""))
        for block in source_blocks
        if isinstance(block, dict)
    }
    item_by_id = {
        str(item.get("item_id", "")): item
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }

    ungrouped = _ungrouped_item_count(items, groups)
    return {
        "field_language": _field_language_metrics(
            concepts, items, groups, block_text, reader_language=reader_language
        ),
        "type_vocabulary": _type_vocabulary_metrics(concepts, groups),
        "canonical_names": _canonical_name_metrics(concepts),
        "facets": _facet_metrics(concepts),
        "group_granularity": _group_granularity_metrics(groups, item_by_id, block_text),
        "ungrouped_items": ungrouped,
    }


def _field_language_metrics(
    concepts: list[dict[str, Any]],
    items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    block_text: dict[str, str],
    *,
    reader_language: str,
) -> dict[str, Any]:
    source_surface_issues: list[dict[str, Any]] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        refs = [str(ref) for ref in concept.get("source_block_refs", [])]
        surface = str(concept.get("surface") or "")
        if surface and refs and not _text_found_in_blocks(surface, refs, block_text):
            source_surface_issues.append(_concept_issue_preview(concept, "surface", surface))
        for observed in concept.get("observed_surfaces", []) or []:
            observed_text = str(observed or "")
            if observed_text and refs and not _text_found_in_blocks(observed_text, refs, block_text):
                source_surface_issues.append(_concept_issue_preview(concept, "observed_surface", observed_text))
                break

    reader_english: list[dict[str, Any]] = []
    if reader_language.lower() in {"zh", "zh-hans", "zh_cn", "zh-cn"}:
        for concept in concepts:
            summary = str(concept.get("summary") or "") if isinstance(concept, dict) else ""
            if _looks_like_english_prose(summary):
                reader_english.append(_concept_issue_preview(concept, "summary", summary))
        for item in items:
            summary = str(item.get("summary") or "") if isinstance(item, dict) else ""
            if _looks_like_english_prose(summary):
                reader_english.append(_item_issue_preview(item, "summary", summary))
        for group in groups:
            if not isinstance(group, dict):
                continue
            summary = str(group.get("summary") or "")
            if _looks_like_english_prose(summary):
                reader_english.append(_group_issue_preview(group, "summary", summary))
            graph = group.get("graph") if isinstance(group.get("graph"), dict) else {}
            for edge in graph.get("edges", []) or []:
                edge_summary = str(edge.get("summary") or "") if isinstance(edge, dict) else ""
                if _looks_like_english_prose(edge_summary):
                    reader_english.append({
                        "kind": "graph_edge",
                        "field": "summary",
                        "edge_type": edge.get("edge_type", "") if isinstance(edge, dict) else "",
                        "text": _preview_text(edge_summary),
                    })
                    break

    return {
        "reader_language": reader_language,
        "source_surface_issue_count": len(source_surface_issues),
        "source_surface_examples": source_surface_issues[:8],
        "reader_language_issue_count": len(reader_english),
        "reader_language_examples": reader_english[:8],
    }


def _type_vocabulary_metrics(
    concepts: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    concept_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    edge_counts: dict[str, int] = {}
    for concept in concepts:
        ctype = normalize_concept_type(concept.get("concept_type", "other")) if isinstance(concept, dict) else "other"
        if ctype not in RECOMMENDED_CONCEPT_TYPES:
            concept_counts[ctype] = concept_counts.get(ctype, 0) + 1
    for group in groups:
        if not isinstance(group, dict):
            continue
        gtype = str(group.get("group_type") or "other")
        if gtype not in RECOMMENDED_GROUP_TYPES:
            group_counts[gtype] = group_counts.get(gtype, 0) + 1
        graph = group.get("graph") if isinstance(group.get("graph"), dict) else {}
        for edge in graph.get("edges", []) or []:
            if not isinstance(edge, dict):
                continue
            etype = str(edge.get("edge_type") or "other")
            if etype not in RECOMMENDED_EDGE_TYPES:
                edge_counts[etype] = edge_counts.get(etype, 0) + 1
    return {
        "nonstandard_concept_type_count": sum(concept_counts.values()),
        "nonstandard_concept_types": concept_counts,
        "nonstandard_group_type_count": sum(group_counts.values()),
        "nonstandard_group_types": group_counts,
        "nonstandard_edge_type_count": sum(edge_counts.values()),
        "nonstandard_edge_types": edge_counts,
    }


def _canonical_name_metrics(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, int]] = {}
    eligible_total = 0
    eligible_present = 0
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        ctype = normalize_concept_type(concept.get("concept_type", "other"))
        bucket = by_type.setdefault(ctype, {"total": 0, "present": 0, "eligible": 0, "eligible_present": 0})
        bucket["total"] += 1
        has_cname = bool(str(concept.get("canonical_name") or "").strip())
        if has_cname:
            bucket["present"] += 1
        if ctype in _STABLE_CANONICAL_TYPES:
            bucket["eligible"] += 1
            eligible_total += 1
            if has_cname:
                bucket["eligible_present"] += 1
                eligible_present += 1
    return {
        "eligible_total": eligible_total,
        "eligible_present": eligible_present,
        "eligible_coverage": round(eligible_present / eligible_total, 3) if eligible_total else 1.0,
        "by_type": by_type,
    }


def _facet_metrics(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    empty = 0
    prose_like = 0
    examples: list[dict[str, Any]] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        facets = concept.get("facets", [])
        if not isinstance(facets, list) or not [f for f in facets if str(f).strip()]:
            empty += 1
            if len(examples) < 8:
                examples.append(_concept_issue_preview(concept, "facets", "<empty>"))
            continue
        if any(_looks_like_prose_facet(str(f)) for f in facets):
            prose_like += 1
            if len(examples) < 8:
                examples.append(_concept_issue_preview(concept, "facets", ", ".join(str(f) for f in facets[:3])))
    total = len([c for c in concepts if isinstance(c, dict)])
    return {
        "concept_count": total,
        "empty_count": empty,
        "coverage": round((total - empty) / total, 3) if total else 1.0,
        "prose_like_count": prose_like,
        "examples": examples,
    }


def _group_granularity_metrics(
    groups: list[dict[str, Any]],
    item_by_id: dict[str, dict[str, Any]],
    block_text: dict[str, str],
) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    temporal_groups: list[dict[str, Any]] = []
    block_order = {block_id: i for i, block_id in enumerate(block_text)}
    for group in groups:
        if not isinstance(group, dict):
            continue
        gtype = str(group.get("group_type") or "other")
        type_counts[gtype] = type_counts.get(gtype, 0) + 1
        if gtype not in {"timeline", "temporal_sequence"}:
            continue
        item_refs = [str(ref) for ref in group.get("item_refs", [])]
        refs: list[str] = []
        for item_ref in item_refs:
            item = item_by_id.get(item_ref, {})
            refs.extend(str(ref) for ref in item.get("source_block_refs", []) if ref)
        indexes = [block_order[ref] for ref in refs if ref in block_order]
        span = (max(indexes) - min(indexes) + 1) if indexes else 0
        temporal_groups.append({
            "group_id": group.get("group_id", ""),
            "group_type": gtype,
            "item_count": len(item_refs),
            "block_span": span,
            "summary": _preview_text(group.get("summary", "")),
        })
    adjacent_candidates = 0
    sorted_temporal = sorted(
        [g for g in temporal_groups if g["group_type"] == "temporal_sequence"],
        key=lambda g: (g.get("block_span", 0), g.get("group_id", "")),
    )
    if len(sorted_temporal) > 1:
        adjacent_candidates = len(sorted_temporal) - 1
    return {
        "group_type_counts": type_counts,
        "timeline_count": type_counts.get("timeline", 0),
        "temporal_sequence_count": type_counts.get("temporal_sequence", 0),
        "temporal_groups": temporal_groups[:20],
        "candidate_adjacent_temporal_sequence_pairs": adjacent_candidates,
    }


def _text_found_in_blocks(needle: str, refs: list[str], block_text: dict[str, str]) -> bool:
    normalized = needle.strip()
    if not normalized:
        return True
    compact = "".join(normalized.split())
    for ref in refs:
        text = block_text.get(ref, "")
        if normalized in text or (compact and compact in "".join(text.split())):
            return True
    return False


def _looks_like_english_prose(text: str) -> bool:
    if not text or len(text.strip()) < 12:
        return False
    ascii_alpha = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return ascii_alpha >= 12 and ascii_alpha > cjk * 2


def _looks_like_prose_facet(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in stripped):
        return True
    return " " in stripped and len(stripped) > 24


def _concept_issue_preview(concept: dict[str, Any], field: str, text: str) -> dict[str, Any]:
    return {
        "kind": "concept",
        "concept_id": concept.get("concept_id", ""),
        "field": field,
        "surface": concept.get("surface", ""),
        "canonical_name": concept.get("canonical_name", ""),
        "concept_type": concept.get("concept_type", ""),
        "text": _preview_text(text),
    }


def _item_issue_preview(item: dict[str, Any], field: str, text: str) -> dict[str, Any]:
    return {
        "kind": "item",
        "item_id": item.get("item_id", ""),
        "field": field,
        "item_type": item.get("item_type", ""),
        "text": _preview_text(text),
    }


def _group_issue_preview(group: dict[str, Any], field: str, text: str) -> dict[str, Any]:
    return {
        "kind": "group",
        "group_id": group.get("group_id", ""),
        "field": field,
        "group_type": group.get("group_type", ""),
        "text": _preview_text(text),
    }


def _ungrouped_item_count(
    items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    all_grouped: set[str] = set()
    for g in groups:
        if isinstance(g, dict):
            all_grouped.update(str(r) for r in (g.get("item_refs") or []))
    unresolved = {
        str(r) for r in (data.get("unresolved_items") or [])
    } if isinstance(data := {}, dict) else set()  # not available at this scope
    total = len([i for i in items if isinstance(i, dict)])
    grouped = sum(1 for i in items if isinstance(i, dict) and str(i.get("item_id", "")) in all_grouped)
    ungrouped = total - grouped
    examples = []
    for i in items:
        if isinstance(i, dict) and str(i.get("item_id", "")) not in all_grouped:
            examples.append({
                "item_id": i.get("item_id", ""),
                "item_type": i.get("item_type", ""),
                "summary": _preview_text(i.get("summary", "")),
            })
            if len(examples) >= 5:
                break
    return {
        "total": total,
        "grouped": grouped,
        "ungrouped": ungrouped,
        "ungrouped_ratio": round(ungrouped / total, 3) if total else 0.0,
        "examples": examples,
    }


def log_quality_metrics(metrics: dict[str, Any]) -> None:
    field = metrics.get("field_language", {})
    types = metrics.get("type_vocabulary", {})
    canonical = metrics.get("canonical_names", {})
    facets = metrics.get("facets", {})
    groups = metrics.get("group_granularity", {})
    print(
        "    quality: "
        f"source_surface_issues={field.get('source_surface_issue_count', 0)}, "
        f"reader_language_issues={field.get('reader_language_issue_count', 0)}, "
        f"nonstandard_types="
        f"{types.get('nonstandard_concept_type_count', 0)}/"
        f"{types.get('nonstandard_group_type_count', 0)}/"
        f"{types.get('nonstandard_edge_type_count', 0)} "
        "(concept/group/edge), "
        f"canonical_eligible={canonical.get('eligible_present', 0)}/"
        f"{canonical.get('eligible_total', 0)}, "
        f"facet_coverage={facets.get('coverage', 1.0)}, "
        f"groups timeline/temporal={groups.get('timeline_count', 0)}/"
        f"{groups.get('temporal_sequence_count', 0)}",
        file=sys.stderr,
    )
    ungrouped = metrics.get("ungrouped_items", {})
    if ungrouped.get("ungrouped", 0) > 0:
        print(
            f"    ungrouped items: {ungrouped.get('ungrouped', 0)}/"
            f"{ungrouped.get('total', 0)}",
            file=sys.stderr,
        )
    for example in field.get("source_surface_examples", [])[:3]:
        print(
            f"      source-field issue: {example.get('concept_id', '')} "
            f"{example.get('field', '')}={example.get('text', '')} "
            f"surface={example.get('surface', '')}",
            file=sys.stderr,
        )
    for example in field.get("reader_language_examples", [])[:3]:
        print(
            f"      reader-language issue: {example.get('kind', '')} "
            f"{example.get('concept_id') or example.get('item_id') or example.get('group_id') or ''} "
            f"{example.get('field', '')}={example.get('text', '')}",
            file=sys.stderr,
        )


