from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Protocol

# ── Constants ──

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 384_000
DEEPSEEK_CONTEXT_TOKENS = 850_000
DEEPSEEK_MAX_OUTPUT_TOKENS = 384_000
DEEPSEEK_DEFAULT_TIMEOUT = 300
DEEPSEEK_DEFAULT_MAX_RETRIES = 3


# ── Errors ──


class ExtractionError(RuntimeError):
    """Raised when an extraction pass cannot produce valid structured output."""


class ExtractionBudgetError(ExtractionError):
    """Raised when an extraction request is likely to exceed model token limits."""


# ── Backend protocol ──


class LLMBackend(Protocol):
    @property
    def model_identity(self) -> str:
        ...

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        ...


# ── Mock backend ──


class MockExtractionBackend:
    model_identity = "mock-local-bundle-v0"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        if user_payload.get("task") == "overview_segmentation":
            return json.dumps(mock_overview_response(user_payload), ensure_ascii=False)
        if user_payload.get("task") == "unit_finalization":
            return json.dumps(mock_unit_finalization_response(user_payload), ensure_ascii=False)
        if user_payload.get("task") == "unit_repair":
            return json.dumps(mock_unit_repair_response(user_payload), ensure_ascii=False)
        if user_payload.get("task") == "unit_timeline":
            return json.dumps(mock_unit_timeline_response(user_payload), ensure_ascii=False)
        if user_payload.get("task") == "unit_timeline_repair":
            return json.dumps(mock_unit_timeline_repair_response(user_payload), ensure_ascii=False)
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
                "entity_mentions": [
                    {
                        "mention_id": "entity-0001",
                        "surface": evidence[:40] if evidence else "placeholder",
                        "canonical_name": None,
                        "kind": "other",
                        "summary": "Placeholder entity extracted by mock backend.",
                        "alias_candidate_of": None,
                        "alias_confidence": None,
                        "alias_rationale": None,
                        "evidence_span_ids": ["evidence-0001"] if evidence else [],
                    }
                ]
                if evidence
                else [],
                "location_mentions": [],
                "atom_mentions": [
                    {
                        "atom_id": "atom-0001",
                        "atom_kind": "narrative_event",
                        "summary": "Placeholder atom extracted by mock backend.",
                        "participant_mention_ids": [],
                        "location_mention_ids": [],
                        "time_expression_ids": [],
                        "thread_ids": [],
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


# ── DeepSeek backend ──


class DeepSeekBackend:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        thinking: bool = False,
        reasoning_effort: str = "high",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEEPSEEK_DEFAULT_TIMEOUT,
        max_retries: int = DEEPSEEK_DEFAULT_MAX_RETRIES,
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
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DS_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY or DS_API_KEY is required for DeepSeek extraction")
        from openai import OpenAI

        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com",
            timeout=timeout,
            max_retries=0,
        )

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

        last_exception: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_retries and _is_retryable(exc):
                    delay = 2 ** attempt
                    time.sleep(delay)
                    continue
                raise

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            content = choice.message.content
            if not content:
                raise RuntimeError("DeepSeek returned empty content for JSON extraction")
            if finish_reason == "length" and attempt < self.max_retries:
                print(
                    f"  length-retry after {2 ** attempt}s (attempt {attempt + 1}/{self.max_retries + 1})",
                    file=sys.stderr,
                )
                time.sleep(2 ** attempt)
                continue
            if finish_reason == "length":
                raise ExtractionError(
                    "DeepSeek stopped because generation hit max_tokens or context length; "
                    "retry with a higher --max-tokens value or a smaller input segment."
                )
            try:
                parse_json_response(content)
            except ExtractionError as parse_error:
                if attempt < self.max_retries:
                    delay = 2 ** attempt
                    print(
                        f"  parse-retry after {delay}s (attempt {attempt + 1}/{self.max_retries + 1}): {parse_error}",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                raise
            return content

        raise last_exception  # type: ignore[misc]


# ── JSON parsing ──


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


# ── Hashing ──


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


# ── Token estimation ──


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


# ── Text helpers ──


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


# ── Retry helper ──


def _is_retryable(exc: Exception) -> bool:
    """Return True for transient errors worth retrying (network, rate-limit, server)."""
    try:
        from openai import (  # type: ignore[import-untyped]
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:
        return False

    return isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError))


# ── Mock response generators ──


def mock_overview_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    text = str(user_payload.get("text") or "")
    unit_id = str(user_payload.get("unit", {}).get("id") or "")
    first = first_nonempty_line(text)
    last = last_nonempty_line(text)
    segment = {
        "segment_id": "overview-segment-0001",
        "title": first[:80] or "segment",
        "summary": "Placeholder overview segment extracted by mock backend.",
        "start_quote": first[:120],
        "end_quote": last[:120],
        "key_entities": [],
        "key_locations": [],
        "time_hints": [],
        "event_hints": [],
        "extraction_hints": ["mock backend used; detailed extraction should inspect this segment"],
    }
    return {
        "unit_id": unit_id,
        "overview_segments": [segment] if text.strip() else [],
        "warnings": ["mock backend used; overview is structural placeholder only"],
    }


def mock_unit_finalization_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload["unit_id"]
    segment_results = user_payload.get("segment_results", [])
    entity_records = []
    event_records = []
    for index, segment in enumerate(segment_results, start=1):
        segment_id = segment["segment_id"]
        for mention in segment.get("entity_mentions", [])[:1]:
            entity_records.append(
                {
                    "entity_id": f"unit-entity-{len(entity_records) + 1:04d}",
                    "canonical_name": mention.get("canonical_name") or mention.get("surface"),
                    "surfaces": [mention.get("surface")],
                    "kind": mention.get("kind", "other"),
                    "summary": mention.get("summary", "Mock merged entity."),
                    "mention_refs": [
                        {"segment_id": segment_id, "mention_id": mention.get("mention_id")}
                    ],
                    "alias_confidence": "low",
                }
            )
        for atom in segment.get("atom_mentions", [])[:1]:
            time_expr_ids = atom.get("time_expression_ids") or []
            event_records.append(
                {
                    "atom_id": f"unit-atom-{len(event_records) + 1:04d}",
                    "atom_kind": atom.get("atom_kind", "narrative_event"),
                    "summary": atom.get("summary", "Mock merged atom."),
                    "segment_ids": [segment_id],
                    "source_order_hint": index,
                    "participant_entity_ids": [],
                    "location_ids": [],
                    "time_refs": [
                        {"segment_id": segment_id, "time_expression_id": time_id}
                        for time_id in time_expr_ids
                    ],
                    "evidence_refs": [
                        {"segment_id": segment_id, "evidence_id": evidence_id}
                        for evidence_id in atom.get("evidence_span_ids", [])
                    ],
                    "thread_ids": [],
                    "duplicate_of": None,
                    "qc_notes": ["mock finalization"],
                }
            )
    return {
        "unit_id": unit_id,
        "entity_records": entity_records,
        "location_records": [],
        "atom_records": event_records,
        "thread_records": [],
        "unresolved_items": [],
        "quality_notes": {
            "summary": "Mock unit finalization completed with placeholder merged records.",
            "blocking_concerns": [],
        },
        "warnings": ["mock backend used; finalization output is structural placeholder only"],
    }


def mock_unit_repair_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_records = user_payload.get("unit_records", {})
    unresolved = list(unit_records.get("unresolved_items", []))
    resolved_count = 0
    remaining = []
    for item in unresolved:
        if isinstance(item, dict) and item.get("severity") == "error":
            resolved_count += 1
        else:
            remaining.append(item)
    quality_notes = dict(unit_records.get("quality_notes", {}))
    if resolved_count:
        quality_notes["repair_summary"] = (
            f"Mock repair resolved {resolved_count} blocking concern(s); "
            f"{len(remaining)} unresolved items remain."
        )
    return {
        "unit_id": user_payload["unit_id"],
        "entity_records": unit_records.get("entity_records", []),
        "location_records": unit_records.get("location_records", []),
        "event_records": unit_records.get("event_records", []),
        "thread_records": unit_records.get("thread_records", []),
        "unresolved_items": remaining,
        "quality_notes": quality_notes,
        "warnings": unit_records.get("warnings", [])
        + ["mock backend used; repair output is structural placeholder only"],
    }


def mock_unit_timeline_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_records = user_payload.get("unit_records", {})
    events = unit_records.get("atom_records", [])

    ordered = []
    for i, event in enumerate(events):
        entry: dict[str, Any] = {
            "atom_id": event["atom_id"],
        }
        if i + 1 < len(events):
            entry["before_atoms"] = [events[i + 1]["atom_id"]]
            entry["rationale"] = f"source_order_hint {event.get('source_order_hint', i+1)} < {events[i+1].get('source_order_hint', i+2)}"
        ordered.append(entry)

    timeline = {
        "timeline_id": "unit-timeline-0001",
        "summary": "Mock timeline: all events in source order",
        "confidence": "medium",
        "ordered_atoms": ordered,
    }

    return {
        "timelines": [timeline],
    }


def mock_unit_timeline_repair_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_records = user_payload.get("unit_records", {})
    timelines = user_payload.get("timelines", [])
    missing_events = user_payload.get("repair_targets", {}).get("missing_events", [])

    events = unit_records.get("atom_records", [])
    if missing_events and timelines:
        timeline = timelines[0]
        ordered = timeline.get("ordered_atoms", [])
        for eid in missing_events:
            ordered.append({"atom_id": eid})
        timeline["ordered_atoms"] = ordered

    return {
        "timelines": timelines,
        "quality_notes": {
            "summary": "Mock timeline repair completed.",
            "blocking_concerns": [],
        },
        "unresolved_items": [],
        "warnings": ["mock backend used; timeline repair is structural placeholder only"],
    }
