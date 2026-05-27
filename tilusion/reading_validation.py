from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .reading_schema import (
    READING_UNIT_SCHEMA_VERSION,
    REGISTRY_DELTA_OPERATION_TYPES,
    RECOMMENDED_PROVENANCE_VALUES,
    is_open_type_string,
)


@dataclass(slots=True)
class ReadingValidationIssue:
    severity: str
    code: str
    path: str
    message: str
    repair_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReadingValidationReport:
    subject_id: str
    issues: list[ReadingValidationIssue]

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "passed": self.passed,
            "issue_count": len(self.issues),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_extraction_unit_package(package: Any) -> ReadingValidationReport:
    """Validate a reading-unit-v0.3 package.

    This validator checks structural integrity and source-grounding rules. It
    does not judge extraction quality except for lightweight warnings; semantic
    quality belongs to later deterministic metrics and LLM repair/review passes.
    """

    data = _as_dict(package)
    issues: list[ReadingValidationIssue] = []
    subject_id = str(data.get("unit_id") or "")

    required = {
        "schema_version": str,
        "unit_id": str,
        "source": dict,
        "source_blocks": list,
        "concepts": list,
        "atomic_items": list,
        "logical_groups": list,
        "unresolved_items": list,
        "validation": dict,
        "context_metadata": dict,
    }
    for key, expected_type in required.items():
        _require_type(data, key, expected_type, issues)

    if data.get("schema_version") != READING_UNIT_SCHEMA_VERSION:
        issues.append(
            _issue(
                "error",
                "schema_version_mismatch",
                "schema_version",
                f"Expected schema_version `{READING_UNIT_SCHEMA_VERSION}`.",
                "Regenerate this artifact with the v0.3 reading schema.",
            )
        )

    stale_keys = [
        "source_spans",
        "concept_mentions",
        "links",
        "derived_views",
        "timelines",
        "entity_records",
        "location_records",
        "atom_records",
        "thread_records",
    ]
    for stale_key in stale_keys:
        if stale_key in data:
            issues.append(
                _issue(
                    "error",
                    "stale_core_field",
                    stale_key,
                    f"`{stale_key}` is not a reading-unit-v0.3 core field.",
                    "Move this data into concepts, atomic_items, or logical_groups as defined by v0.3.",
                )
            )

    blocks = _list(data.get("source_blocks"))
    concepts = _list(data.get("concepts"))
    atomic_items = _list(data.get("atomic_items"))
    groups = _list(data.get("logical_groups"))

    block_ids = _collect_ids(blocks, "block_id", "source_blocks", issues)
    concept_ids = _collect_ids(concepts, "concept_id", "concepts", issues)
    item_ids = _collect_ids(atomic_items, "item_id", "atomic_items", issues)
    _collect_ids(groups, "group_id", "logical_groups", issues)

    for index, block in enumerate(blocks):
        path = f"source_blocks[{index}]"
        if not isinstance(block, dict):
            continue
        _require_type(block, "unit_id", str, issues, path)
        _require_type(block, "segment_id", str, issues, path)
        _require_type(block, "block_index", int, issues, path)
        _require_open_type(block.get("block_type"), f"{path}.block_type", issues)
        _require_type(block, "start", int, issues, path)
        _require_type(block, "end", int, issues, path)
        _require_type(block, "text", str, issues, path)
        _require_type(block, "text_hash", str, issues, path)
        _validate_provenance(block.get("provenance"), f"{path}.provenance", issues, allow_missing=True)
        if block.get("unit_id") != data.get("unit_id"):
            issues.append(
                _issue(
                    "error",
                    "unit_id_mismatch",
                    f"{path}.unit_id",
                    "Source block unit_id must match the package unit_id.",
                    "Copy the package unit_id exactly.",
                )
            )
        start = block.get("start")
        end = block.get("end")
        text = block.get("text")
        if isinstance(start, int) and isinstance(end, int):
            if start < 0 or end < start:
                issues.append(
                    _issue(
                        "error",
                        "invalid_source_block_range",
                        f"{path}.start",
                        "Source block start/end must be a non-negative forward range.",
                    )
                )
            if isinstance(text, str) and end - start != len(text):
                issues.append(
                    _issue(
                        "error",
                        "source_block_range_length_mismatch",
                        f"{path}.end",
                        "Source block end-start must equal len(text).",
                        "Use offsets from the deterministic source block splitter.",
                    )
                )
        _validate_optional_round_trip(data.get("source"), block, path, issues)

    for index, concept in enumerate(concepts):
        path = f"concepts[{index}]"
        if not isinstance(concept, dict):
            continue
        _require_type(concept, "surface", str, issues, path)
        _require_open_type(concept.get("concept_type"), f"{path}.concept_type", issues)
        _validate_ref_list(
            concept.get("source_block_refs"),
            block_ids,
            f"{path}.source_block_refs",
            issues,
            require_non_empty=True,
            empty_code="missing_source_block_refs",
            empty_message="Source-grounded concepts must cite at least one source block.",
        )
        _validate_string_list(concept.get("aliases", []), f"{path}.aliases", issues)
        _validate_string_list(concept.get("observed_surfaces", []), f"{path}.observed_surfaces", issues)
        _validate_string_list(concept.get("facets", []), f"{path}.facets", issues)
        _validate_string_list(concept.get("uncertainty", []), f"{path}.uncertainty", issues)
        _validate_provenance(concept.get("provenance"), f"{path}.provenance", issues, allow_missing=True)

    for index, item in enumerate(atomic_items):
        path = f"atomic_items[{index}]"
        if not isinstance(item, dict):
            continue
        _require_open_type(item.get("item_type"), f"{path}.item_type", issues)
        _require_type(item, "summary", str, issues, path)
        _validate_ref_list(
            item.get("source_block_refs"),
            block_ids,
            f"{path}.source_block_refs",
            issues,
            require_non_empty=True,
            empty_code="missing_source_block_refs",
            empty_message="Source-grounded atomic items must cite at least one source block.",
        )
        _validate_ref_list(item.get("concept_refs", []), concept_ids, f"{path}.concept_refs", issues)
        _validate_temporal_attributes(item.get("temporal_attributes", []), block_ids, f"{path}.temporal_attributes", issues)
        _validate_string_list(item.get("uncertainty", []), f"{path}.uncertainty", issues)
        _validate_provenance(item.get("provenance"), f"{path}.provenance", issues, allow_missing=True)

    for index, group in enumerate(groups):
        path = f"logical_groups[{index}]"
        if not isinstance(group, dict):
            continue
        _require_open_type(group.get("group_type"), f"{path}.group_type", issues)
        _require_type(group, "summary", str, issues, path)
        _validate_ref_list(
            group.get("item_refs", []),
            item_ids,
            f"{path}.item_refs",
            issues,
            require_non_empty=False,
        )
        _validate_ref_list(group.get("concept_refs", []), concept_ids, f"{path}.concept_refs", issues)
        _validate_string_list(group.get("uncertainty", []), f"{path}.uncertainty", issues)
        _validate_provenance(group.get("provenance"), f"{path}.provenance", issues, allow_missing=True)
        _validate_group_graph(group.get("graph", {}), item_ids, block_ids, f"{path}.graph", issues)

    _add_quality_warnings(block_ids, atomic_items, groups, issues)
    return ReadingValidationReport(subject_id=subject_id, issues=issues)


def validate_registry_delta(delta: Any, *, expected_base_snapshot_id: str | None = None) -> ReadingValidationReport:
    data = _as_dict(delta)
    issues: list[ReadingValidationIssue] = []
    subject_id = str(data.get("delta_id") or "")
    required = {
        "schema_version": str,
        "delta_id": str,
        "base_snapshot_id": str,
        "unit_id": str,
        "operations": list,
        "validation": dict,
    }
    for key, expected_type in required.items():
        _require_type(data, key, expected_type, issues)
    if expected_base_snapshot_id is not None and data.get("base_snapshot_id") != expected_base_snapshot_id:
        issues.append(
            _issue(
                "error",
                "base_snapshot_mismatch",
                "base_snapshot_id",
                "Registry delta base snapshot does not match the expected snapshot.",
                "Rebuild the delta against the current document-state snapshot.",
            )
        )
    seen_operation_ids: set[str] = set()
    for index, op in enumerate(_list(data.get("operations"))):
        path = f"operations[{index}]"
        if not isinstance(op, dict):
            issues.append(_issue("error", "wrong_item_type", path, "Operation must be an object."))
            continue
        op_id = op.get("operation_id")
        if not isinstance(op_id, str) or not op_id:
            issues.append(_issue("error", "missing_required_field", f"{path}.operation_id", "Operation is missing operation_id."))
        elif op_id in seen_operation_ids:
            issues.append(_issue("error", "duplicate_object_id", f"{path}.operation_id", f"Duplicate operation_id `{op_id}`."))
        else:
            seen_operation_ids.add(op_id)
        op_type = op.get("operation_type")
        if op_type not in REGISTRY_DELTA_OPERATION_TYPES:
            issues.append(
                _issue(
                    "error",
                    "unsupported_delta_operation",
                    f"{path}.operation_type",
                    f"Unsupported registry delta operation `{op_type}`.",
                    "Use a proposed operation type such as merge_proposal instead of direct mutation.",
                )
            )
        if op_type in {"merge", "delete_canonical_concept", "rewrite_snapshot"}:
            issues.append(
                _issue(
                    "error",
                    "destructive_auto_merge",
                    f"{path}.operation_type",
                    "Raw deltas must not perform destructive global mutations.",
                    "Emit a merge_proposal or user_review_needed operation instead.",
                )
            )
        for ref_path in ["evidence_refs", "source_refs"]:
            refs = op.get(ref_path, [])
            if isinstance(refs, list):
                for ref_index, ref in enumerate(refs):
                    if _ref_uses_prior_context(ref):
                        issues.append(_prior_context_issue(f"{path}.{ref_path}[{ref_index}]"))
    return ReadingValidationReport(subject_id=subject_id, issues=issues)


def _validate_group_graph(
    graph: Any,
    item_ids: set[str],
    block_ids: set[str],
    path: str,
    issues: list[ReadingValidationIssue],
) -> None:
    if graph in (None, {}):
        return
    if not isinstance(graph, dict):
        issues.append(_issue("error", "wrong_field_type", path, "Graph must be an object."))
        return
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not isinstance(nodes, list):
        issues.append(_issue("error", "wrong_field_type", f"{path}.nodes", "Graph nodes must be a list."))
        nodes = []
    if not isinstance(edges, list):
        issues.append(_issue("error", "wrong_field_type", f"{path}.edges", "Graph edges must be a list."))
        edges = []

    node_ids = _collect_ids(nodes, "node_id", f"{path}.nodes", issues)
    for index, node in enumerate(nodes):
        node_path = f"{path}.nodes[{index}]"
        if not isinstance(node, dict):
            continue
        _validate_single_ref(node.get("item_ref"), item_ids, f"{node_path}.item_ref", issues)
        if "label" in node and not isinstance(node.get("label"), str):
            issues.append(_issue("error", "wrong_field_type", f"{node_path}.label", "Node label must be a string."))

    for index, edge in enumerate(edges):
        edge_path = f"{path}.edges[{index}]"
        if not isinstance(edge, dict):
            issues.append(_issue("error", "wrong_item_type", edge_path, "Graph edge must be an object."))
            continue
        _validate_single_ref(edge.get("source"), node_ids, f"{edge_path}.source", issues)
        _validate_single_ref(edge.get("target"), node_ids, f"{edge_path}.target", issues)
        _require_open_type(edge.get("edge_type"), f"{edge_path}.edge_type", issues)
        if "summary" in edge and not isinstance(edge.get("summary"), str):
            issues.append(_issue("error", "wrong_field_type", f"{edge_path}.summary", "Edge summary must be a string."))
        _validate_ref_list(edge.get("source_block_refs", []), block_ids, f"{edge_path}.source_block_refs", issues)
        grounding = _grounding(edge.get("provenance"))
        if grounding is not None and grounding not in RECOMMENDED_PROVENANCE_VALUES:
            issues.append(_bad_grounding_issue(f"{edge_path}.provenance.grounding"))
        if grounding == "source_grounded" and not edge.get("source_block_refs"):
            issues.append(
                _issue(
                    "error",
                    "missing_source_grounded_evidence",
                    f"{edge_path}.source_block_refs",
                    "Source-grounded graph edges must cite at least one source block.",
                    "Add source_block_refs or mark the edge as synthesis.",
                )
            )


def _validate_temporal_attributes(
    attrs: Any,
    block_ids: set[str],
    path: str,
    issues: list[ReadingValidationIssue],
) -> None:
    if not isinstance(attrs, list):
        issues.append(_issue("error", "wrong_field_type", path, "Temporal attributes must be a list."))
        return
    for index, attr in enumerate(attrs):
        attr_path = f"{path}[{index}]"
        if not isinstance(attr, dict):
            issues.append(_issue("error", "wrong_item_type", attr_path, "Temporal attribute must be an object."))
            continue
        _require_open_type(attr.get("kind"), f"{attr_path}.kind", issues)
        source_block_ref = attr.get("source_block_ref")
        if source_block_ref:
            _validate_single_ref(source_block_ref, block_ids, f"{attr_path}.source_block_ref", issues)
        elif _ref_uses_prior_context(attr):
            issues.append(_prior_context_issue(attr_path))
        _validate_string_list(attr.get("uncertainty", []), f"{attr_path}.uncertainty", issues)


def _validate_optional_round_trip(
    source: Any,
    block: dict[str, Any],
    path: str,
    issues: list[ReadingValidationIssue],
) -> None:
    if not isinstance(source, dict):
        return
    unit_text = source.get("unit_text")
    if not isinstance(unit_text, str):
        return
    start = block.get("start")
    end = block.get("end")
    text = block.get("text")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str):
        return
    if unit_text[start:end] != text:
        issues.append(
            _issue(
                "error",
                "source_block_round_trip_mismatch",
                f"{path}.text",
                "Source block text must match source.unit_text[start:end] when unit_text is available.",
                "Regenerate source blocks with the deterministic splitter.",
            )
        )


def _validate_provenance(
    provenance: Any,
    path: str,
    issues: list[ReadingValidationIssue],
    *,
    allow_missing: bool = False,
) -> None:
    if provenance in (None, {}):
        if not allow_missing:
            issues.append(_issue("error", "missing_required_field", path, "Missing provenance."))
        return
    if not isinstance(provenance, dict):
        issues.append(_issue("error", "wrong_field_type", path, "Provenance must be an object."))
        return
    grounding = provenance.get("grounding")
    if grounding is not None and grounding not in RECOMMENDED_PROVENANCE_VALUES:
        issues.append(_bad_grounding_issue(f"{path}.grounding"))
    created_by = provenance.get("created_by")
    if created_by is not None and created_by not in RECOMMENDED_PROVENANCE_VALUES:
        issues.append(_bad_grounding_issue(f"{path}.created_by"))


def _grounding(provenance: Any) -> str | None:
    if isinstance(provenance, dict):
        value = provenance.get("grounding")
        return value if isinstance(value, str) else None
    return None


def _add_quality_warnings(
    block_ids: set[str],
    atomic_items: list[Any],
    groups: list[Any],
    issues: list[ReadingValidationIssue],
) -> None:
    cited_blocks: set[str] = set()
    for item in atomic_items:
        if isinstance(item, dict):
            cited_blocks.update(ref for ref in _list(item.get("source_block_refs")) if isinstance(ref, str))
    unreferenced = sorted(block_ids - cited_blocks)
    if block_ids and unreferenced:
        issues.append(
            _issue(
                "warning",
                "unreferenced_source_blocks",
                "source_blocks",
                f"{len(unreferenced)} source block(s) are not cited by any atomic item.",
                "This may be fine for sparse/front-matter text; otherwise check extraction coverage.",
            )
        )
    singleton_count = 0
    group_count = 0
    for group in groups:
        if isinstance(group, dict):
            refs = _list(group.get("item_refs"))
            group_count += 1
            if len(refs) == 1:
                singleton_count += 1
    if group_count and singleton_count == group_count and group_count > 1:
        issues.append(
            _issue(
                "warning",
                "all_singleton_logical_groups",
                "logical_groups",
                "All logical groups contain only one atomic item.",
                "This may indicate that unit-level grouping is too weak.",
            )
        )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        if isinstance(result, dict):
            return result
    return {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _require_type(
    data: dict[str, Any],
    key: str,
    expected_type: type,
    issues: list[ReadingValidationIssue],
    parent_path: str | None = None,
) -> None:
    path = f"{parent_path}.{key}" if parent_path else key
    if key not in data:
        issues.append(_issue("error", "missing_required_field", path, f"Missing required field `{path}`."))
    elif not isinstance(data[key], expected_type):
        issues.append(
            _issue(
                "error",
                "wrong_field_type",
                path,
                f"Field `{path}` must be {expected_type.__name__}.",
            )
        )


def _require_open_type(value: Any, path: str, issues: list[ReadingValidationIssue]) -> None:
    if not is_open_type_string(value):
        issues.append(
            _issue(
                "error",
                "invalid_type_string",
                path,
                "Type fields must be non-empty strings.",
                "Use a recommended type, `other`, or a justified custom string.",
            )
        )


def _collect_ids(
    records: list[Any],
    id_field: str,
    collection: str,
    issues: list[ReadingValidationIssue],
) -> set[str]:
    ids: set[str] = set()
    for index, record in enumerate(records):
        path = f"{collection}[{index}]"
        if not isinstance(record, dict):
            issues.append(_issue("error", "wrong_item_type", path, "Record must be an object."))
            continue
        value = record.get(id_field)
        id_path = f"{path}.{id_field}"
        if not isinstance(value, str) or not value:
            issues.append(_issue("error", "missing_required_field", id_path, f"Missing `{id_field}`."))
            continue
        if value in ids:
            issues.append(_issue("error", "duplicate_object_id", id_path, f"Duplicate id `{value}`."))
        ids.add(value)
    return ids


def _validate_ref_list(
    refs: Any,
    valid_ids: set[str],
    path: str,
    issues: list[ReadingValidationIssue],
    *,
    require_non_empty: bool = False,
    empty_code: str = "empty_ref_list",
    empty_message: str = "Reference list must not be empty.",
) -> None:
    if not isinstance(refs, list):
        issues.append(_issue("error", "wrong_field_type", path, "Reference field must be a list."))
        return
    if require_non_empty and not refs:
        issues.append(_issue("error", empty_code, path, empty_message))
        return
    for index, ref in enumerate(refs):
        ref_path = f"{path}[{index}]"
        _validate_single_ref(ref, valid_ids, ref_path, issues)


def _validate_single_ref(
    ref: Any,
    valid_ids: set[str],
    path: str,
    issues: list[ReadingValidationIssue],
) -> None:
    if not isinstance(ref, str) or not ref:
        issues.append(_issue("error", "invalid_ref", path, "Reference must be a non-empty string."))
        return
    if _ref_uses_prior_context(ref):
        issues.append(_prior_context_issue(path))
        return
    if ref not in valid_ids:
        issues.append(
            _issue(
                "error",
                "unknown_ref",
                path,
                f"Reference `{ref}` does not resolve within the package.",
                "Use an existing source block, concept, atomic item, or graph node id.",
            )
        )


def _validate_string_list(value: Any, path: str, issues: list[ReadingValidationIssue]) -> None:
    if not isinstance(value, list):
        issues.append(_issue("error", "wrong_field_type", path, "Field must be a list of strings."))
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            issues.append(_issue("error", "wrong_item_type", f"{path}[{index}]", "List item must be a string."))


def _ref_uses_prior_context(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("context:") or value.startswith("prior:") or value.startswith("book_context:")
    if isinstance(value, dict):
        return any(_ref_uses_prior_context(v) for v in value.values())
    if isinstance(value, list):
        return any(_ref_uses_prior_context(v) for v in value)
    return False


def _prior_context_issue(path: str) -> ReadingValidationIssue:
    return _issue(
        "error",
        "prior_context_used_as_evidence",
        path,
        "Prior context cannot be cited as evidence for current-unit records.",
        "Use current-unit source blocks as evidence, or mark the relation as synthesis.",
    )


def _bad_grounding_issue(path: str) -> ReadingValidationIssue:
    return _issue(
        "error",
        "invalid_grounding",
        path,
        "Grounding/provenance must use a supported provenance value.",
    )


def _issue(
    severity: str,
    code: str,
    path: str,
    message: str,
    repair_hint: str = "",
) -> ReadingValidationIssue:
    return ReadingValidationIssue(
        severity=severity,
        code=code,
        path=path,
        message=message,
        repair_hint=repair_hint,
    )
