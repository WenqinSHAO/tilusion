from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from .book_reader import StructureUnit, build_book_index, extract_unit_text


PROMPT_VERSION = "segment-extraction-v0.4"
SCHEMA_VERSION = "segment-extraction-v0.2"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 32768
DEEPSEEK_CONTEXT_TOKENS = 1_000_000
DEEPSEEK_MAX_OUTPUT_TOKENS = 384_000
PROMPT_RESOURCE = "segment_extraction_v0.4.md"


class ExtractionError(RuntimeError):
    """Raised when an extraction pass cannot produce valid structured output."""


class ExtractionBudgetError(ExtractionError):
    """Raised when an extraction request is likely to exceed model token limits."""


@dataclass(slots=True)
class ExtractionContext:
    confirmed_entities: list[dict[str, Any]] = field(default_factory=list)
    confirmed_locations: list[dict[str, Any]] = field(default_factory=list)
    active_threads: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    temporal_constraints: list[dict[str, Any]] = field(default_factory=list)
    frontier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PromptEnvelope:
    task: str
    prompt_version: str
    schema_version: str
    unit: dict[str, Any]
    context: dict[str, Any]
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_model_payload(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "prior_context": self.context,
            "text": self.text,
        }


@dataclass(slots=True)
class LocalBundleResult:
    task: str
    prompt_version: str
    schema_version: str
    unit_id: str
    source_text_hash: str
    context_hash: str
    model: str
    raw_response: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractionQualityReport:
    unit_id: str
    issue_count: int
    error_count: int
    warning_count: int
    issues: list[ExtractionQualityIssue]

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
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_repair_payload(self, *, max_issues: int = 40) -> dict[str, Any]:
        selected = self.issues[:max_issues]
        return {
            "unit_id": self.unit_id,
            "quality_summary": {
                "passed": self.passed,
                "issue_count": self.issue_count,
                "error_count": self.error_count,
                "warning_count": self.warning_count,
                "truncated": len(self.issues) > len(selected),
            },
            "repair_instructions": [
                "Fix the listed issues while preserving valid extracted objects whenever possible.",
                "Evidence quotes must be exact substrings of the provided source text.",
                "Do not introduce global canonical records; use local mentions and alias candidates only.",
                "Return the complete corrected extraction JSON, not a patch.",
            ],
            "issues": [issue.to_dict() for issue in selected],
        }

class LLMBackend(Protocol):
    @property
    def model_identity(self) -> str:
        ...

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        ...


class MockExtractionBackend:
    model_identity = "mock-local-bundle-v0"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        text = user_payload["text"]
        unit_id = user_payload["unit"]["id"]
        evidence = first_nonempty_line(text)
        return json.dumps(
            {
                "unit_id": unit_id,
                "evidence_spans": [
                    {
                        "evidence_id": "evidence-0001",
                        "unit_id": unit_id,
                        "quote": evidence[:240],
                        "start_hint": "first non-empty line",
                        "end_hint": "first non-empty line",
                    }
                ]
                if evidence
                else [],
                "entity_mentions": [],
                "location_mentions": [],
                "event_mentions": [
                    {
                        "event_id": "event-0001",
                        "summary": "Placeholder event extracted by mock backend.",
                        "participant_mention_ids": [],
                        "location_mention_ids": [],
                        "time_expression_ids": [],
                        "evidence_span_ids": ["evidence-0001"] if evidence else [],
                    }
                ]
                if evidence
                else [],
                "time_expressions": [],
                "thread_candidates": [],
                "warnings": ["mock backend used; output is structural placeholder only"],
            },
            ensure_ascii=False,
        )


class DeepSeekBackend:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        thinking: bool = False,
        reasoning_effort: str = "high",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if max_tokens > DEEPSEEK_MAX_OUTPUT_TOKENS:
            raise ValueError(
                f"max_tokens={max_tokens} exceeds DeepSeek V4 max output "
                f"limit of {DEEPSEEK_MAX_OUTPUT_TOKENS}"
            )
        self.model = model
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DS_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY or DS_API_KEY is required for DeepSeek extraction")
        from openai import OpenAI

        self.client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")

    @property
    def model_identity(self) -> str:
        thinking_mode = "thinking" if self.thinking else "no-thinking"
        return f"deepseek:{self.model}:{thinking_mode}:effort={self.reasoning_effort}:max={self.max_tokens}"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "stream": False,
            "extra_body": {"thinking": {"type": "enabled" if self.thinking else "disabled"}},
        }
        if self.thinking:
            kwargs["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        content = choice.message.content
        if not content:
            raise RuntimeError("DeepSeek returned empty content for JSON extraction")
        if finish_reason == "length":
            raise ExtractionError(
                "DeepSeek stopped because generation hit max_tokens or context length; "
                "retry with a higher --max-tokens value or a smaller input segment."
            )
        return content


def run_local_bundle_extraction(
    book_path: str | Path,
    unit_id: str,
    *,
    context: ExtractionContext | None = None,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache/extraction",
    use_cache: bool = True,
) -> LocalBundleResult:
    index = build_book_index(book_path)
    unit = index.unit_map().get(unit_id)
    if unit is None:
        raise ValueError(f"unknown unit_id: {unit_id}")
    text = extract_unit_text(book_path, unit)
    extraction_context = context or ExtractionContext(frontier=unit_id)
    llm = backend or MockExtractionBackend()
    envelope = build_local_bundle_prompt(unit, text, extraction_context)
    check_extraction_budget(
        LOCAL_BUNDLE_SYSTEM_PROMPT,
        envelope.to_model_payload(),
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )
    cache_key = build_cache_key(envelope, llm.model_identity)
    cache_path = Path(cache_dir) / f"{cache_key}.json"
    if use_cache and cache_path.exists():
        return result_from_json(cache_path.read_text(encoding="utf-8"))

    raw_response = llm.complete_json(LOCAL_BUNDLE_SYSTEM_PROMPT, envelope.to_model_payload())
    data = parse_json_response(raw_response)
    validate_local_bundle(data)
    result = LocalBundleResult(
        task=envelope.task,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        unit_id=unit.id,
        source_text_hash=sha256_text(text),
        context_hash=sha256_json(extraction_context.to_dict()),
        model=llm.model_identity,
        raw_response=raw_response,
        data=data,
    )
    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(result.to_json(), encoding="utf-8")
    return result


def build_local_bundle_prompt(
    unit: StructureUnit, text: str, context: ExtractionContext
) -> PromptEnvelope:
    return PromptEnvelope(
        task="local_bundle_extraction",
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        unit={
            "id": unit.id,
            "label": unit.label,
            "kind": unit.kind,
            "title_path": unit.title_path,
            "content_kind": unit.content_kind,
            "source_kind": unit.source_kind,
            "source_range": unit.source_range,
        },
        context=context.to_dict(),
        text=text,
    )


LOCAL_BUNDLE_SYSTEM_PROMPT = resources.files("tilusion.prompts").joinpath(PROMPT_RESOURCE).read_text(encoding="utf-8")


PLACEHOLDER_PASSES = [
    "event_grouping",
    "temporal_claim_extraction",
    "thread_candidate_refinement",
    "alias_candidate_generation",
    "parent_unit_verification",
]


def build_cache_key(envelope: PromptEnvelope, model_identity: str) -> str:
    payload = {
        "task": envelope.task,
        "prompt_version": envelope.prompt_version,
        "schema_version": envelope.schema_version,
        "unit_id": envelope.unit["id"],
        "source_text_hash": sha256_text(envelope.text),
        "context_hash": sha256_json(envelope.context),
        "model_identity": model_identity,
    }
    return sha256_json(payload)


def parse_json_response(raw_response: str) -> dict[str, Any]:
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError as first_error:
        match = re.search(r"\{.*\}", raw_response, flags=re.DOTALL)
        if not match:
            raise ExtractionError(
                "LLM response was not valid JSON. This often means the response was "
                "cut off by output truncation, ignored JSON mode, or included non-JSON text. "
                f"JSON error: {first_error.msg} at char {first_error.pos}."
            ) from first_error
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as second_error:
            tail = raw_response[-240:].replace("\n", "\\n")
            raise ExtractionError(
                "LLM response looked like JSON but could not be parsed. This is often "
                "caused by output truncation; retry with a higher --max-tokens value "
                "or a smaller input segment. "
                f"JSON error: {second_error.msg} at char {second_error.pos}. "
                f"Response tail: {tail}"
            ) from second_error


def check_extraction_budget(
    system_prompt: str,
    model_payload: dict[str, Any],
    *,
    max_output_tokens: int,
    context_tokens: int = DEEPSEEK_CONTEXT_TOKENS,
) -> None:
    if max_output_tokens <= 0:
        raise ExtractionBudgetError("max_output_tokens must be positive")
    if max_output_tokens > DEEPSEEK_MAX_OUTPUT_TOKENS:
        raise ExtractionBudgetError(
            f"max_output_tokens={max_output_tokens} exceeds DeepSeek V4 max output "
            f"limit of {DEEPSEEK_MAX_OUTPUT_TOKENS}"
        )
    input_tokens = estimate_deepseek_tokens(system_prompt) + estimate_deepseek_tokens(
        json.dumps(model_payload, ensure_ascii=False)
    )
    if input_tokens + max_output_tokens > context_tokens:
        raise ExtractionBudgetError(
            "Extraction request is likely to exceed model context. "
            f"Estimated input tokens: {input_tokens}; requested max output tokens: "
            f"{max_output_tokens}; context limit: {context_tokens}. "
            "Use a smaller reader unit/chunk or lower --max-tokens."
        )


def estimate_deepseek_tokens(text: str) -> int:
    cjk_chars = sum(1 for char in text if is_cjk(char))
    other_chars = len(text) - cjk_chars
    return max(1, int((cjk_chars * 0.6) + (other_chars * 0.3)) + 1)


def is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2A6DF
        or 0x2A700 <= codepoint <= 0x2B73F
        or 0x2B740 <= codepoint <= 0x2B81F
        or 0x2B820 <= codepoint <= 0x2CEAF
        or 0x2CEB0 <= codepoint <= 0x2EBEF
    )


def validate_local_bundle(data: dict[str, Any]) -> None:
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
        if key not in data:
            raise ValueError(f"missing extraction field: {key}")
        if not isinstance(data[key], expected_type):
            raise ValueError(f"field {key} must be {expected_type.__name__}")


def validate_extraction_quality(
    data: dict[str, Any],
    unit_text: str,
    *,
    expected_unit_id: str | None = None,
    max_evidence_chars: int = 320,
    source_window_chars: int = 120,
) -> ExtractionQualityReport:
    issues: list[ExtractionQualityIssue] = []
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
                    "Use a short exact quote from the source text.",
                    object_id=evidence_id or None,
                    evidence_id=evidence_id or None,
                )
            )
            continue
        locations = find_all_spans(unit_text, quote)
        quote_locations[evidence_id] = locations
        if not locations:
            issues.append(
                quality_issue(
                    "error",
                    "evidence_quote_not_found",
                    f"{path}.quote",
                    "Evidence quote is not an exact substring of the source text.",
                    "Replace this quote with an exact source substring, preserving note markers and punctuation.",
                    object_id=evidence_id,
                    evidence_id=evidence_id,
                    source_windows=guess_source_windows_for_quote(
                        unit_text, quote, source_window_chars
                    ),
                )
            )
        elif len(locations) > 1:
            issues.append(
                quality_issue(
                    "warning",
                    "evidence_quote_ambiguous",
                    f"{path}.quote",
                    f"Evidence quote appears {len(locations)} times in the source text.",
                    "Use a longer exact quote or clearer local hints so the locator can be reconstructed.",
                    object_id=evidence_id,
                    evidence_id=evidence_id,
                    source_windows=source_windows(unit_text, locations[:3], source_window_chars),
                )
            )
        if len(quote) > max_evidence_chars:
            issues.append(
                quality_issue(
                    "warning",
                    "evidence_quote_too_long",
                    f"{path}.quote",
                    f"Evidence quote has {len(quote)} characters, above limit {max_evidence_chars}.",
                    "Shorten evidence to the minimal exact phrase, line, or sentence needed for support.",
                    object_id=evidence_id,
                    evidence_id=evidence_id,
                    source_windows=source_windows(unit_text, locations[:1], source_window_chars),
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

    validate_surface_grounding(data, "entity_mentions", evidence_by_id, unit_text, issues)
    validate_surface_grounding(data, "location_mentions", evidence_by_id, unit_text, issues)

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
        quotes = [
            evidence_by_id[ref].get("quote", "")
            for ref in refs
            if ref in evidence_by_id and isinstance(evidence_by_id[ref].get("quote"), str)
        ]
        if quotes and surface not in "".join(quotes):
            object_id = object_identifier(obj)
            issues.append(
                quality_issue(
                    "warning",
                    "surface_not_in_cited_evidence",
                    f"{collection}[{index}].surface",
                    f"Surface `{surface}` does not appear in cited evidence quotes.",
                    "Cite evidence containing the surface form, adjust the surface, or keep only if alias/context makes this unavoidable.",
                    object_id=object_id,
                    source_windows=guess_source_windows_for_quote(unit_text, surface, 80),
                )
            )


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
    for field_name in ["mention_id", "event_id", "time_expression_id", "thread_id", "evidence_id"]:
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
    for part in re.split(r"[，。！？；：,.!?;:\\s]+", stripped):
        if len(part) >= 4:
            candidates.append(part[:12])
            candidates.append(part[:8])
            candidates.append(part[:4])
    unique: list[str] = []
    for candidate in candidates:
        if len(candidate) >= 2 and candidate not in unique:
            unique.append(candidate)
    return unique


def result_from_json(payload: str) -> LocalBundleResult:
    data = json.loads(payload)
    return LocalBundleResult(**data)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
