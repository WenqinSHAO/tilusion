from __future__ import annotations

from typing import Any


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
        "atom_records": list,
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
    if not isinstance(timelines, list):
        issues.append(unit_finalization_issue("error", "missing_required_field", "timelines"))
        error_count = sum(1 for i in issues if i["severity"] == "error")
        return {
            "passed": False,
            "issue_count": len(issues),
            "error_count": error_count,
            "warning_count": 0,
            "issues": issues,
        }
    if len(timelines) == 0:
        return {
            "passed": True,
            "issue_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "issues": [],
        }

    input_atom_ids = {a.get("atom_id") for a in data.get("atom_records", []) if isinstance(a, dict)}
    all_atom_ids: set[str] = set()
    timeline_atom_sets: list[set[str]] = []
    for i, timeline in enumerate(timelines):
        prefix = f"timelines[{i}]"
        for field in ["timeline_id", "summary", "ordered_atoms", "confidence"]:
            if field not in timeline:
                issues.append(unit_finalization_issue("error", "missing_required_field", f"{prefix}.{field}"))
        tid = timeline.get("timeline_id", "")
        if not isinstance(tid, str) or not tid.startswith("unit-timeline-"):
            issues.append(unit_finalization_issue("error", "wrong_field_type", f"{prefix}.timeline_id"))
        conf = timeline.get("confidence")
        if conf not in ("high", "medium", "low"):
            issues.append(unit_finalization_issue("warning", "wrong_field_type", f"{prefix}.confidence"))

        ordered = timeline.get("ordered_atoms")
        tl_atom_ids: set[str] = set()
        if isinstance(ordered, list):
            for j, entry in enumerate(ordered):
                if not isinstance(entry, dict):
                    issues.append(unit_finalization_issue("error", "wrong_field_type", f"{prefix}.ordered_atoms[{j}]"))
                    continue
                eid = entry.get("atom_id")
                if not isinstance(eid, str):
                    issues.append(unit_finalization_issue("error", "missing_required_field", f"{prefix}.ordered_atoms[{j}].atom_id"))
                else:
                    all_atom_ids.add(eid)
                    tl_atom_ids.add(eid)
                before = entry.get("before_atoms")
                if before is not None and not isinstance(before, list):
                    issues.append(unit_finalization_issue("error", "wrong_field_type", f"{prefix}.ordered_atoms[{j}].before_atoms"))
                has_edges = bool(before)
                if has_edges and not isinstance(entry.get("rationale"), str):
                    issues.append(unit_finalization_issue("warning", "missing_required_field", f"{prefix}.ordered_atoms[{j}].rationale"))
                # Self-loop check
                if isinstance(eid, str) and isinstance(before, list) and eid in before:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "timeline_self_loop",
                            "path": f"{prefix}.ordered_atoms[{j}]",
                            "message": f"Atom {eid} lists itself in before_atoms.",
                        }
                    )
                # Phantom ref check
                if isinstance(before, list):
                    for ref_id in before:
                        if isinstance(ref_id, str) and ref_id not in input_atom_ids:
                            issues.append(
                                {
                                    "severity": "error",
                                    "code": "timeline_phantom_ref",
                                    "path": f"{prefix}.ordered_atoms[{j}].before_atoms",
                                    "message": f"before_atoms references unknown atom '{ref_id}'.",
                                }
                            )

        timeline_atom_sets.append(tl_atom_ids)
        cycles = _detect_timeline_cycles(ordered if isinstance(ordered, list) else [])
        for cycle in cycles:
            issues.append(
                {
                    "severity": "error",
                    "code": "timeline_cycle_detected",
                    "path": f"{prefix}.ordered_atoms",
                    "message": f"Cycle detected: {' -> '.join(cycle)}",
                }
            )

    # Duplicate atom across timelines check
    for i in range(len(timeline_atom_sets)):
        for j in range(i + 1, len(timeline_atom_sets)):
            overlap = timeline_atom_sets[i] & timeline_atom_sets[j]
            for eid in overlap:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "timeline_shared_atom",
                        "path": f"timelines[{i}],timelines[{j}]",
                        "message": f"Atom {eid} appears in multiple timelines. Allowed as intersection point but should be intentional.",
                    }
                )

    missing = input_atom_ids - all_atom_ids
    extra = all_atom_ids - input_atom_ids
    if missing:
        issues.append(
            {
                "severity": "warning",
                "code": "atoms_missing_from_timelines",
                "path": "timelines",
                "message": f"Atoms not covered by any timeline: {sorted(missing)}",
            }
        )
    if extra:
        issues.append(unit_finalization_issue("error", "unknown_atoms_in_timelines", f"extra: {sorted(extra)}"))

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")
    return {
        "passed": error_count == 0,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }


def _detect_timeline_cycles(ordered_atoms: list[dict]) -> list[list[str]]:
    adj: dict[str, list[str]] = {}
    for entry in ordered_atoms:
        eid = entry.get("atom_id")
        if not isinstance(eid, str):
            continue
        adj.setdefault(eid, [])
        for before_id in (entry.get("before_atoms") or []):
            if isinstance(before_id, str) and before_id in {e.get("atom_id") for e in ordered_atoms if isinstance(e.get("atom_id"), str)}:
                adj.setdefault(eid, []).append(before_id)

    WHITE, GRAY, BLACK = 0, 1, 2
    all_nodes = {e.get("atom_id") for e in ordered_atoms if isinstance(e.get("atom_id"), str)}
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
