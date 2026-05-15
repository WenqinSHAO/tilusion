from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from .book_reader import StructureUnit, build_book_index, extract_unit_text


PROMPT_VERSION = "local-bundle-v0.1"
SCHEMA_VERSION = "local-bundle-v0.1"
DEFAULT_MODEL = "deepseek-v4-flash"


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
        max_tokens: int = 4096,
    ) -> None:
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
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("DeepSeek returned empty content for JSON extraction")
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
    cache_key = build_cache_key(envelope, llm.model_identity)
    cache_path = Path(cache_dir) / f"{cache_key}.json"
    if use_cache and cache_path.exists():
        return result_from_json(cache_path.read_text(encoding="utf-8"))

    raw_response = llm.complete_json(LOCAL_BUNDLE_SYSTEM_PROMPT, envelope.to_dict())
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


LOCAL_BUNDLE_SYSTEM_PROMPT = """You extract local narrative structure from one reader unit.

Return only JSON. Do not include prose outside JSON.

Required top-level keys:
- unit_id
- evidence_spans
- entity_mentions
- location_mentions
- event_mentions
- time_expressions
- thread_candidates
- warnings

Rules:
- Every extracted mention, event, time expression, and thread candidate must cite evidence_span_ids.
- Do not canonicalize across chapters.
- Do not infer a global timeline.
- Preserve uncertainty in summaries and warnings.
- Prefer fewer grounded objects over many weak guesses.
"""


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
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_response, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


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
