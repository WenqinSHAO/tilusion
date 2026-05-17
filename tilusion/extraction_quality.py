from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import re
from typing import Any


@dataclass(slots=True)
class EvidenceLocation:
    evidence_id: str
    status: str
    strategy: str
    quote: str
    start: int | None = None
    end: int | None = None
    match_text: str | None = None
    candidate_count: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractionQualityIssue:
    severity: str
    code: str
    path: str
    message: str
    repair_hint: str
    object_id: str | None = None
    evidence_id: str | None = None
    source_windows: list[dict[str, Any]] = field(default_factory=list)
    relocation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractionQualityReport:
    unit_id: str
    issue_count: int
    error_count: int
    warning_count: int
    issues: list[ExtractionQualityIssue]
    evidence_locations: list[EvidenceLocation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "passed": self.passed,
            "issue_count": self.issue_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "evidence_location_summary": evidence_location_summary(self.evidence_locations),
            "evidence_locations": [location.to_dict() for location in self.evidence_locations],
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_repair_payload(self, *, max_issues: int = 40) -> dict[str, Any]:
        return self.to_llm_repair_payload(max_issues=max_issues)

    def actionable_issues(self) -> list[ExtractionQualityIssue]:
        return [issue for issue in self.issues if is_llm_actionable_issue(issue)]

    def to_llm_repair_payload(self, *, max_issues: int = 40) -> dict[str, Any]:
        actionable_issues = self.actionable_issues()
        selected = actionable_issues[:max_issues]
        unresolved_locations = [
            location
            for location in self.evidence_locations
            if location.status in {"ambiguous", "missing"}
        ]
        return {
            "unit_id": self.unit_id,
            "quality_summary": {
                "passed": self.passed,
                "issue_count": self.issue_count,
                "llm_actionable_issue_count": len(actionable_issues),
                "error_count": self.error_count,
                "warning_count": self.warning_count,
                "truncated": len(actionable_issues) > len(selected),
            },
            "repair_instructions": [
                "Fix the listed issues while preserving valid extracted objects whenever possible.",
                "Prefer exact source quotes; if a quote is relocatable, keep it only when the resolved source span supports the object.",
                "For missing or ambiguous evidence, replace the quote with a shorter source substring that can be uniquely relocated.",
                "Return the complete corrected extraction JSON, not a patch.",
            ],
            "evidence_relocation": {
                "summary": evidence_location_summary(self.evidence_locations),
                "unresolved": [location.to_dict() for location in unresolved_locations],
            },
            "issues": [issue.to_dict() for issue in selected],
        }

    def to_validated_result(self, data: dict[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(data)
        locations_by_id = {
            location.evidence_id: location.to_dict()
            for location in self.evidence_locations
            if location.evidence_id
        }
        evidence_spans = enriched.get("evidence_spans")
        if isinstance(evidence_spans, list):
            for evidence in evidence_spans:
                if not isinstance(evidence, dict):
                    continue
                evidence_id = evidence.get("evidence_id")
                if isinstance(evidence_id, str) and evidence_id in locations_by_id:
                    evidence["source_location"] = locations_by_id[evidence_id]
        return {
            "unit_id": self.unit_id,
            "passed": self.passed,
            "validation_summary": {
                "issue_count": self.issue_count,
                "error_count": self.error_count,
                "warning_count": self.warning_count,
                "evidence_location_summary": evidence_location_summary(self.evidence_locations),
            },
            "source_locations": {
                "evidence_spans": locations_by_id,
            },
            "data": enriched,
        }


def evidence_location_summary(locations: list[EvidenceLocation]) -> dict[str, int]:
    summary = {"exact": 0, "relocated": 0, "ambiguous": 0, "missing": 0}
    for location in locations:
        if location.status not in summary:
            summary[location.status] = 0
        summary[location.status] += 1
    return summary


LLM_ACTIONABLE_WARNING_CODES = {
    "evidence_quote_ambiguous",
    "evidence_quote_too_long",
}


def is_llm_actionable_issue(issue: ExtractionQualityIssue) -> bool:
    if issue.severity == "error":
        return True
    return issue.code in LLM_ACTIONABLE_WARNING_CODES


def validate_extraction_quality(
    data: dict[str, Any],
    unit_text: str,
    *,
    expected_unit_id: str | None = None,
    max_evidence_chars: int = 600,
    source_window_chars: int = 120,
) -> ExtractionQualityReport:
    issues: list[ExtractionQualityIssue] = []
    evidence_locations: list[EvidenceLocation] = []
    unit_id = str(data.get("unit_id") or expected_unit_id or "")
    required = {
        "unit_id": str,
        "evidence_spans": list,
        "entity_mentions": list,
        "location_mentions": list,
        "event_mentions": list,
        "time_expressions": list,
        "thread_candidates": list,
        "warnings": list,
    }

    for key, expected_type in required.items():
        value = data.get(key)
        if key not in data:
            issues.append(
                quality_issue(
                    "error",
                    "missing_required_field",
                    key,
                    f"Missing required field `{key}`.",
                    "Add the required top-level field with the expected type.",
                )
            )
        elif not isinstance(value, expected_type):
            issues.append(
                quality_issue(
                    "error",
                    "wrong_field_type",
                    key,
                    f"Field `{key}` must be {expected_type.__name__}.",
                    "Replace the field value with the expected JSON type.",
                )
            )

    if expected_unit_id is not None and data.get("unit_id") != expected_unit_id:
        issues.append(
            quality_issue(
                "error",
                "unit_id_mismatch",
                "unit_id",
                f"unit_id should be `{expected_unit_id}` but got `{data.get('unit_id')}`.",
                "Copy the source unit id exactly.",
            )
        )

    evidence_spans = data.get("evidence_spans") if isinstance(data.get("evidence_spans"), list) else []
    evidence_by_id = collect_objects_by_id(
        evidence_spans, "evidence_id", "evidence_spans", issues
    )
    evidence_locations_by_id: dict[str, EvidenceLocation] = {}
    quote_locations: dict[str, list[tuple[int, int]]] = {}
    for index, evidence in enumerate(evidence_spans):
        path = f"evidence_spans[{index}]"
        evidence_id = str(evidence.get("evidence_id") or "")
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote:
            issues.append(
                quality_issue(
                    "error",
                    "missing_evidence_quote",
                    f"{path}.quote",
                    "Evidence span is missing a non-empty quote.",
                    "Use a short source quote that supports the object.",
                    object_id=evidence_id or None,
                    evidence_id=evidence_id or None,
                )
            )
            continue
        relocation = relocate_evidence_quote(
            unit_text,
            quote,
            evidence_id=evidence_id,
            source_window_chars=source_window_chars,
        )
        evidence_locations.append(relocation)
        evidence_locations_by_id[evidence_id] = relocation
        quote_locations[evidence_id] = (
            [(relocation.start, relocation.end)]
            if relocation.start is not None and relocation.end is not None
            else []
        )
        if relocation.status == "missing":
            issues.append(
                quality_issue(
                    "error",
                    "evidence_quote_missing",
                    f"{path}.quote",
                    "Evidence quote could not be relocated in the source text.",
                    "Replace this with a real source substring, or remove the unsupported object.",
                    object_id=evidence_id,
                    evidence_id=evidence_id,
                    source_windows=guess_source_windows_for_quote(
                        unit_text, quote, source_window_chars
                    ),
                    relocation=relocation.to_dict(),
                )
            )
        elif relocation.status == "ambiguous":
            issues.append(
                quality_issue(
                    "warning",
                    "evidence_quote_ambiguous",
                    f"{path}.quote",
                    f"Evidence quote has {relocation.candidate_count} possible source locations.",
                    "Use a longer quote or clearer local hints so the locator can be reconstructed.",
                    object_id=evidence_id,
                    evidence_id=evidence_id,
                    source_windows=relocation.candidates[:3],
                    relocation=relocation.to_dict(),
                )
            )
        if len(quote) > max_evidence_chars:
            issues.append(
                quality_issue(
                    "warning",
                    "evidence_quote_too_long",
                    f"{path}.quote",
                    f"Evidence quote has {len(quote)} characters, above limit {max_evidence_chars}.",
                    "Shorten evidence to the minimal source phrase, line, or sentence needed for support.",
                    object_id=evidence_id,
                    evidence_id=evidence_id,
                    source_windows=source_windows(
                        unit_text, quote_locations[evidence_id][:1], source_window_chars
                    ),
                    relocation=relocation.to_dict(),
                )
            )

    validate_id_set(data, "entity_mentions", "mention_id", issues)
    validate_id_set(data, "location_mentions", "mention_id", issues)
    validate_id_set(data, "event_mentions", "event_id", issues)
    validate_id_set(data, "time_expressions", "time_expression_id", issues)
    validate_id_set(data, "thread_candidates", "thread_id", issues)

    entity_ids = object_ids(data, "entity_mentions", "mention_id")
    location_ids = object_ids(data, "location_mentions", "mention_id")
    time_ids = object_ids(data, "time_expressions", "time_expression_id")

    for collection in [
        "entity_mentions",
        "location_mentions",
        "event_mentions",
        "time_expressions",
        "thread_candidates",
    ]:
        validate_evidence_refs(data, collection, evidence_by_id, issues)

    validate_surface_grounding(
        data, "entity_mentions", evidence_by_id, evidence_locations_by_id, unit_text, issues
    )
    validate_surface_grounding(
        data, "location_mentions", evidence_by_id, evidence_locations_by_id, unit_text, issues
    )

    for index, event in enumerate(data.get("event_mentions", []) or []):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "")
        validate_link_refs(
            event,
            "participant_mention_ids",
            entity_ids,
            f"event_mentions[{index}]",
            event_id,
            issues,
            target_name="entity mention",
        )
        validate_link_refs(
            event,
            "location_mention_ids",
            location_ids,
            f"event_mentions[{index}]",
            event_id,
            issues,
            target_name="location mention",
        )
        validate_link_refs(
            event,
            "time_expression_ids",
            time_ids,
            f"event_mentions[{index}]",
            event_id,
            issues,
            target_name="time expression",
        )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    return ExtractionQualityReport(
        unit_id=unit_id,
        issue_count=len(issues),
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        evidence_locations=evidence_locations,
    )


def relocate_evidence_quote(
    text: str,
    quote: str,
    *,
    evidence_id: str,
    source_window_chars: int = 120,
) -> EvidenceLocation:
    exact_spans = find_all_spans(text, quote)
    if len(exact_spans) == 1:
        start, end = exact_spans[0]
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="exact",
            strategy="exact",
            quote=quote,
            start=start,
            end=end,
            match_text=text[start:end],
            candidate_count=1,
        )
    if len(exact_spans) > 1:
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="ambiguous",
            strategy="exact",
            quote=quote,
            candidate_count=len(exact_spans),
            candidates=source_windows(text, exact_spans[:5], source_window_chars),
        )

    normalized_spans = find_normalized_spans(
        text, quote, strip_notes=True, fold_punctuation=False
    )
    if len(normalized_spans) == 1:
        start, end = normalized_spans[0]
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="relocated",
            strategy="annotation_whitespace_tolerant",
            quote=quote,
            start=start,
            end=end,
            match_text=text[start:end],
            candidate_count=1,
        )
    if len(normalized_spans) > 1:
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="ambiguous",
            strategy="annotation_whitespace_tolerant",
            quote=quote,
            candidate_count=len(normalized_spans),
            candidates=source_windows(text, normalized_spans[:5], source_window_chars),
        )

    punctuation_spans = find_normalized_spans(
        text, quote, strip_notes=True, fold_punctuation=True
    )
    if len(punctuation_spans) == 1:
        start, end = punctuation_spans[0]
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="relocated",
            strategy="annotation_whitespace_punctuation_tolerant",
            quote=quote,
            start=start,
            end=end,
            match_text=text[start:end],
            candidate_count=1,
        )
    if len(punctuation_spans) > 1:
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="ambiguous",
            strategy="annotation_whitespace_punctuation_tolerant",
            quote=quote,
            candidate_count=len(punctuation_spans),
            candidates=source_windows(text, punctuation_spans[:5], source_window_chars),
        )

    punctuation_dropped_spans = find_normalized_spans(
        text, quote, strip_notes=True, fold_punctuation=True, drop_punctuation=True
    )
    if len(punctuation_dropped_spans) == 1:
        start, end = punctuation_dropped_spans[0]
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="relocated",
            strategy="annotation_whitespace_punctuation_dropped",
            quote=quote,
            start=start,
            end=end,
            match_text=text[start:end],
            candidate_count=1,
        )
    if len(punctuation_dropped_spans) > 1:
        return EvidenceLocation(
            evidence_id=evidence_id,
            status="ambiguous",
            strategy="annotation_whitespace_punctuation_dropped",
            quote=quote,
            candidate_count=len(punctuation_dropped_spans),
            candidates=source_windows(text, punctuation_dropped_spans[:5], source_window_chars),
        )

    return EvidenceLocation(
        evidence_id=evidence_id,
        status="missing",
        strategy="not_found",
        quote=quote,
        candidate_count=0,
        candidates=guess_source_windows_for_quote(text, quote, source_window_chars),
    )


def find_normalized_spans(
    text: str,
    quote: str,
    *,
    strip_notes: bool,
    fold_punctuation: bool,
    drop_punctuation: bool = False,
) -> list[tuple[int, int]]:
    normalized_text, text_map = normalize_for_location(
        text,
        strip_notes=strip_notes,
        fold_punctuation=fold_punctuation,
        drop_punctuation=drop_punctuation,
    )
    normalized_quote, _ = normalize_for_location(
        quote,
        strip_notes=strip_notes,
        fold_punctuation=fold_punctuation,
        drop_punctuation=drop_punctuation,
    )
    if not normalized_quote:
        return []
    normalized_matches = find_all_spans(normalized_text, normalized_quote)
    spans: list[tuple[int, int]] = []
    for start, end in normalized_matches:
        if start >= len(text_map) or end - 1 >= len(text_map):
            continue
        original_start = text_map[start]
        original_end = text_map[end - 1] + 1
        if drop_punctuation:
            while original_end < len(text) and is_punctuation(text[original_end]):
                original_end += 1
        spans.append((original_start, original_end))
    return spans


def normalize_for_location(
    text: str,
    *,
    strip_notes: bool,
    fold_punctuation: bool,
    drop_punctuation: bool = False,
) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    index = 0
    while index < len(text):
        if strip_notes:
            match = NOTE_MARKER_RE.match(text, index)
            if match:
                index = match.end()
                continue
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if drop_punctuation and is_punctuation(char):
            index += 1
            continue
        chars.append(fold_char(char) if fold_punctuation else char)
        index_map.append(index)
        index += 1
    return "".join(chars), index_map


NOTE_MARKER_RE = re.compile(r"\[\d{1,4}\]")
PUNCTUATION_FOLD = str.maketrans(
    {
        "，": ",",
        "、": ",",
        "。": ".",
        "．": ".",
        "：": ":",
        "；": ";",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "—": "-",
        "－": "-",
    }
)


def fold_char(char: str) -> str:
    return char.translate(PUNCTUATION_FOLD)


def is_punctuation(char: str) -> bool:
    return char in PUNCTUATION_CHARS


PUNCTUATION_CHARS = set(
    "，、。．：；！？（）“”‘’—－《》〈〉「」『』【】[]()"
    ",.!?;:\"'`"
)


def quality_issue(
    severity: str,
    code: str,
    path: str,
    message: str,
    repair_hint: str,
    *,
    object_id: str | None = None,
    evidence_id: str | None = None,
    source_windows: list[dict[str, Any]] | None = None,
    relocation: dict[str, Any] | None = None,
) -> ExtractionQualityIssue:
    return ExtractionQualityIssue(
        severity=severity,
        code=code,
        path=path,
        message=message,
        repair_hint=repair_hint,
        object_id=object_id,
        evidence_id=evidence_id,
        source_windows=source_windows or [],
        relocation=relocation,
    )


def collect_objects_by_id(
    objects: list[Any], id_field: str, collection: str, issues: list[ExtractionQualityIssue]
) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for index, obj in enumerate(objects):
        path = f"{collection}[{index}]"
        if not isinstance(obj, dict):
            issues.append(
                quality_issue(
                    "error",
                    "wrong_object_type",
                    path,
                    f"{path} must be an object.",
                    "Replace this list item with a JSON object or remove it.",
                )
            )
            continue
        object_id = obj.get(id_field)
        if not isinstance(object_id, str) or not object_id:
            issues.append(
                quality_issue(
                    "error",
                    "missing_object_id",
                    f"{path}.{id_field}",
                    f"{path} is missing `{id_field}`.",
                    "Assign a response-local temporary id.",
                )
            )
            continue
        if object_id in seen:
            issues.append(
                quality_issue(
                    "error",
                    "duplicate_object_id",
                    f"{path}.{id_field}",
                    f"Duplicate response-local id `{object_id}`.",
                    "Rename duplicate ids and update all references inside this response.",
                    object_id=object_id,
                )
            )
        seen.add(object_id)
        by_id[object_id] = obj
    return by_id


def validate_id_set(
    data: dict[str, Any],
    collection: str,
    id_field: str,
    issues: list[ExtractionQualityIssue],
) -> None:
    objects = data.get(collection)
    if isinstance(objects, list):
        collect_objects_by_id(objects, id_field, collection, issues)


def object_ids(data: dict[str, Any], collection: str, id_field: str) -> set[str]:
    values: set[str] = set()
    for obj in data.get(collection, []) or []:
        if isinstance(obj, dict) and isinstance(obj.get(id_field), str):
            values.add(obj[id_field])
    return values


def validate_evidence_refs(
    data: dict[str, Any],
    collection: str,
    evidence_by_id: dict[str, dict[str, Any]],
    issues: list[ExtractionQualityIssue],
) -> None:
    for index, obj in enumerate(data.get(collection, []) or []):
        if not isinstance(obj, dict):
            continue
        object_id = object_identifier(obj)
        refs = obj.get("evidence_span_ids")
        path = f"{collection}[{index}].evidence_span_ids"
        if not isinstance(refs, list) or not refs:
            issues.append(
                quality_issue(
                    "error",
                    "missing_evidence_refs",
                    path,
                    "Object must cite at least one evidence span.",
                    "Add one or more evidence_span_ids that support this object, or remove the object.",
                    object_id=object_id,
                )
            )
            continue
        for ref_index, ref in enumerate(refs):
            if ref not in evidence_by_id:
                issues.append(
                    quality_issue(
                        "error",
                        "unresolved_evidence_ref",
                        f"{path}[{ref_index}]",
                        f"Evidence reference `{ref}` does not resolve.",
                        "Use an existing evidence_id or add the missing evidence span.",
                        object_id=object_id,
                        evidence_id=str(ref),
                    )
                )


def validate_surface_grounding(
    data: dict[str, Any],
    collection: str,
    evidence_by_id: dict[str, dict[str, Any]],
    evidence_locations_by_id: dict[str, EvidenceLocation],
    unit_text: str,
    issues: list[ExtractionQualityIssue],
) -> None:
    for index, obj in enumerate(data.get(collection, []) or []):
        if not isinstance(obj, dict):
            continue
        surface = obj.get("surface")
        if not isinstance(surface, str) or not surface:
            continue
        refs = obj.get("evidence_span_ids")
        if not isinstance(refs, list):
            continue
        support_texts = [
            evidence_support_text(unit_text, evidence_locations_by_id[ref])
            for ref in refs
            if ref in evidence_by_id and ref in evidence_locations_by_id
        ]
        if support_texts and not surface_supported_by_texts(surface, support_texts):
            object_id = object_identifier(obj)
            issues.append(
                quality_issue(
                    "warning",
                    "surface_not_in_evidence_context",
                    f"{collection}[{index}].surface",
                    f"Surface `{surface}` does not appear in the reconstructed cited evidence context.",
                    "Move the attested text form into `surface` and the editorial name into `canonical_name`, "
                    "or cite evidence that contains the surface.",
                    object_id=object_id,
                    source_windows=guess_source_windows_for_quote(unit_text, surface, 80),
                )
            )


def evidence_support_text(text: str, location: EvidenceLocation) -> str:
    if location.start is None or location.end is None:
        return location.match_text or location.quote
    return containing_paragraph(text, location.start, location.end)


def containing_paragraph(text: str, start: int, end: int, *, max_chars: int = 1200) -> str:
    paragraph_start = text.rfind("\n\n", 0, start)
    paragraph_start = 0 if paragraph_start < 0 else paragraph_start + 2
    paragraph_end = text.find("\n\n", end)
    paragraph_end = len(text) if paragraph_end < 0 else paragraph_end
    if paragraph_end - paragraph_start <= max_chars:
        return text[paragraph_start:paragraph_end]
    window_start = max(paragraph_start, start - (max_chars // 2))
    window_end = min(paragraph_end, end + (max_chars // 2))
    return text[window_start:window_end]


def surface_supported_by_texts(surface: str, texts: list[str]) -> bool:
    needles = surface_needles(surface)
    normalized_texts = [normalize_support_text(text) for text in texts]
    for needle in needles:
        if needle and any(needle in text for text in normalized_texts):
            return True
    return False


def surface_needles(surface: str) -> list[str]:
    normalized = normalize_support_text(surface)
    needles = [normalized]
    if len(normalized) >= 3:
        needles.append(normalized[1:])
        needles.append(normalized[:-1])
    if len(normalized) >= 5:
        needles.append(normalized[2:])
        needles.append(normalized[:-2])
    unique = []
    for needle in needles:
        if len(needle) >= 1 and needle not in unique:
            unique.append(needle)
    return unique


def normalize_support_text(text: str) -> str:
    normalized, _ = normalize_for_location(
        text, strip_notes=True, fold_punctuation=True, drop_punctuation=True
    )
    return normalized


def validate_link_refs(
    obj: dict[str, Any],
    field_name: str,
    valid_ids: set[str],
    base_path: str,
    object_id: str,
    issues: list[ExtractionQualityIssue],
    *,
    target_name: str,
) -> None:
    refs = obj.get(field_name, [])
    if refs is None:
        refs = []
    if not isinstance(refs, list):
        issues.append(
            quality_issue(
                "error",
                "wrong_reference_field_type",
                f"{base_path}.{field_name}",
                f"`{field_name}` must be a list.",
                "Use a list of response-local ids, or an empty list when unknown.",
                object_id=object_id,
            )
        )
        return
    for ref_index, ref in enumerate(refs):
        if ref not in valid_ids:
            issues.append(
                quality_issue(
                    "error",
                    "unresolved_object_ref",
                    f"{base_path}.{field_name}[{ref_index}]",
                    f"Reference `{ref}` does not resolve to a {target_name}.",
                    "Use an existing response-local id or remove the invalid reference.",
                    object_id=object_id,
                )
            )


def object_identifier(obj: dict[str, Any]) -> str | None:
    for field_name in [
        "mention_id",
        "event_id",
        "time_expression_id",
        "thread_id",
        "evidence_id",
    ]:
        value = obj.get(field_name)
        if isinstance(value, str) and value:
            return value
    return None


def find_all_spans(text: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return spans
        spans.append((index, index + len(needle)))
        start = index + max(1, len(needle))


def source_windows(
    text: str, spans: list[tuple[int, int]], window_chars: int
) -> list[dict[str, Any]]:
    windows = []
    for start, end in spans:
        window_start = max(0, start - window_chars)
        window_end = min(len(text), end + window_chars)
        windows.append(
            {
                "start": start,
                "end": end,
                "window_start": window_start,
                "window_end": window_end,
                "text": text[window_start:window_end],
            }
        )
    return windows


def guess_source_windows_for_quote(
    text: str, quote: str, window_chars: int
) -> list[dict[str, Any]]:
    for needle in source_window_needles(quote):
        spans = find_all_spans(text, needle)
        if spans:
            return source_windows(text, spans[:3], window_chars)
    return []


def source_window_needles(quote: str) -> list[str]:
    stripped = quote.strip()
    if not stripped:
        return []
    candidates = [
        stripped[:12],
        stripped[:8],
        stripped[:4],
    ]
    for part in re.split(r"[，。！？；：,.!?;:\s]+", stripped):
        if len(part) >= 4:
            candidates.append(part[:12])
            candidates.append(part[:8])
            candidates.append(part[:4])
    unique: list[str] = []
    for candidate in candidates:
        if len(candidate) >= 2 and candidate not in unique:
            unique.append(candidate)
    return unique
