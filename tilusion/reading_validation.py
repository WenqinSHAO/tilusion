from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .reading_schema import (
    CONFIDENCE_VALUES,
    GROUNDING_VALUES,
    REGISTRY_DELTA_OPERATION_TYPES,
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
    data = _as_dict(package)
    issues: list[ReadingValidationIssue] = []
    subject_id = str(data.get("unit_id") or "")

    required = {
        "schema_version": str,
        "unit_id": str,
        "source": dict,
        "source_spans": list,
        "source_blocks": list,
        "concept_mentions": list,
        "logical_groups": list,
        "links": list,
        "derived_views": list,
        "unresolved_items": list,
        "validation": dict,
        "context_metadata": dict,
    }
    for key, expected_type in required.items():
        _require_type(data, key, expected_type, issues)

    for stale_key in ["timelines", "entity_records", "location_records", "atom_records", "thread_records"]:
        if stale_key in data:
            issues.append(
                _issue(
                    "error",
                    "stale_core_field",
                    stale_key,
                    f"`{stale_key}` is not a core reading-package field.",
                    "Move this data into concepts, logical_groups, links, or derived_views.",
                )
            )

    spans = _list(data.get("source_spans"))
    blocks = _list(data.get("source_blocks"))
    concepts = _list(data.get("concept_mentions"))
    groups = _list(data.get("logical_groups"))
    links = _list(data.get("links"))
    views = _list(data.get("derived_views"))

    span_ids = _collect_ids(spans, "span_id", "source_spans", issues)
    block_ids = _collect_ids(blocks, "block_id", "source_blocks", issues)
    concept_ids = _collect_ids(concepts, "mention_id", "concept_mentions", issues)
    group_ids = _collect_ids(groups, "group_id", "logical_groups", issues)
    link_ids = _collect_ids(links, "link_id", "links", issues)
    view_ids = _collect_ids(views, "view_id", "derived_views", issues)

    for index, span in enumerate(spans):
        path = f"source_spans[{index}]"
        _require_type(span, "span_id", str, issues, path)
        _require_type(span, "unit_id", str, issues, path)
        _require_type(span, "source_range", dict, issues, path)
        _require_type(span, "quote", str, issues, path)
        if span.get("unit_id") != data.get("unit_id"):
            issues.append(
                _issue(
                    "error",
                    "unit_id_mismatch",
                    f"{path}.unit_id",
                    "Source span unit_id must match the package unit_id.",
                    "Copy the package unit_id exactly.",
                )
            )
        if _source_range_uses_prior_context(span.get("source_range")):
            issues.append(_prior_context_issue(f"{path}.source_range"))

    for index, block in enumerate(blocks):
        path = f"source_blocks[{index}]"
        _require_open_type(block.get("block_type"), f"{path}.block_type", issues)
        _validate_confidence(block.get("confidence"), f"{path}.confidence", issues)
        _validate_ref_list(block.get("span_refs"), span_ids, f"{path}.span_refs", issues)

    for index, concept in enumerate(concepts):
        path = f"concept_mentions[{index}]"
        _require_type(concept, "surface", str, issues, path)
        _require_open_type(concept.get("concept_type"), f"{path}.concept_type", issues)
        _validate_confidence(concept.get("confidence"), f"{path}.confidence", issues)
        _validate_ref_list(concept.get("source_block_refs"), block_ids, f"{path}.source_block_refs", issues)
        _validate_ref_list(concept.get("source_span_refs"), span_ids, f"{path}.source_span_refs", issues)

    for index, group in enumerate(groups):
        path = f"logical_groups[{index}]"
        _require_open_type(group.get("group_type"), f"{path}.group_type", issues)
        _require_type(group, "summary", str, issues, path)
        _validate_confidence(group.get("confidence"), f"{path}.confidence", issues)
        _validate_ref_list(group.get("source_block_refs"), block_ids, f"{path}.source_block_refs", issues)
        _validate_ref_list(group.get("concept_refs", []), concept_ids, f"{path}.concept_refs", issues)
        _validate_ref_list(group.get("link_refs", []), link_ids, f"{path}.link_refs", issues)
        provenance = group.get("provenance") if isinstance(group.get("provenance"), dict) else {}
        grounding = provenance.get("grounding")
        if grounding is not None and grounding not in GROUNDING_VALUES:
            issues.append(_bad_grounding_issue(f"{path}.provenance.grounding"))

    valid_link_endpoint_ids = span_ids | block_ids | concept_ids | group_ids
    for index, link in enumerate(links):
        path = f"links[{index}]"
        _require_open_type(link.get("link_type"), f"{path}.link_type", issues)
        _validate_confidence(link.get("confidence"), f"{path}.confidence", issues)
        grounding = link.get("grounding", "source_grounded")
        if grounding not in GROUNDING_VALUES:
            issues.append(_bad_grounding_issue(f"{path}.grounding"))
        _validate_single_ref(link.get("source_ref"), valid_link_endpoint_ids, f"{path}.source_ref", issues)
        _validate_single_ref(link.get("target_ref"), valid_link_endpoint_ids, f"{path}.target_ref", issues)
        evidence_refs = link.get("evidence_block_refs", [])
        _validate_ref_list(evidence_refs, block_ids, f"{path}.evidence_block_refs", issues)
        if grounding == "source_grounded" and not evidence_refs:
            issues.append(
                _issue(
                    "error",
                    "missing_source_grounded_evidence",
                    f"{path}.evidence_block_refs",
                    "Source-grounded links must cite at least one source block.",
                    "Add evidence_block_refs or mark the link as synthesis.",
                )
            )

    for index, view in enumerate(views):
        path = f"derived_views[{index}]"
        _require_open_type(view.get("view_type"), f"{path}.view_type", issues)
        _validate_confidence(view.get("confidence"), f"{path}.confidence", issues)
        _validate_ref_list(view.get("input_group_refs", []), group_ids, f"{path}.input_group_refs", issues)
        _validate_ref_list(view.get("input_link_refs", []), link_ids, f"{path}.input_link_refs", issues)
        if view.get("is_source_of_truth") is not False:
            issues.append(
                _issue(
                    "error",
                    "derived_view_marked_source_of_truth",
                    f"{path}.is_source_of_truth",
                    "Derived views must not be marked as source of truth.",
                    "Set is_source_of_truth to false and keep core records authoritative.",
                )
            )

    # Touch the set to avoid accidental future removal without test coverage.
    _ = view_ids
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


def _validate_confidence(value: Any, path: str, issues: list[ReadingValidationIssue]) -> None:
    if value not in CONFIDENCE_VALUES:
        issues.append(
            _issue(
                "error",
                "invalid_confidence",
                path,
                "Confidence must be high, medium, low, or unknown.",
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
) -> None:
    if not isinstance(refs, list):
        issues.append(_issue("error", "wrong_field_type", path, "Reference field must be a list."))
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
                "Use an existing source block, span, concept, group, or link id.",
            )
        )


def _source_range_uses_prior_context(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_ref_uses_prior_context(v) for v in value.values())


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
        "Use current-unit source spans/blocks as evidence, or mark the relation as synthesis.",
    )


def _bad_grounding_issue(path: str) -> ReadingValidationIssue:
    return _issue(
        "error",
        "invalid_grounding",
        path,
        "Grounding must be one of the supported provenance values.",
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
