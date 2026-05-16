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
from .extraction_quality import (
    ExtractionQualityIssue,
    ExtractionQualityReport,
    validate_extraction_quality,
)


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
