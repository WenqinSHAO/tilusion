from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import resources
import json
from pathlib import Path
from typing import Any

from .book_reader import build_book_index, extract_unit_text
from .extraction import (
    DEFAULT_MAX_TOKENS,
    LLMBackend,
    LOCAL_BUNDLE_SYSTEM_PROMPT,
    PROMPT_RESOURCE,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractionContext,
    LocalBundleResult,
    MockExtractionBackend,
    build_local_bundle_prompt,
    check_extraction_budget,
    parse_json_response,
    result_from_json,
    sha256_json,
    sha256_text,
    validate_local_bundle,
)
from .extraction_quality import ExtractionQualityReport, validate_extraction_quality


@dataclass(slots=True)
class PromptPart:
    part_id: str
    role: str
    source: str
    content: str
    generated_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["content_hash"] = self.content_hash
        return data


@dataclass(slots=True)
class PromptComposition:
    composition_id: str
    parts: list[PromptPart]

    @property
    def content(self) -> str:
        if len(self.parts) == 1:
            return self.parts[0].content
        sections = []
        for part in self.parts:
            sections.append(f"<!-- prompt-part:{part.part_id} role:{part.role} -->\n{part.content}")
        return "\n\n".join(sections)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "content_hash": self.content_hash,
            "parts": [part.to_dict() for part in self.parts],
        }


@dataclass(slots=True)
class ExtractionPassRecord:
    pass_name: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    result: LocalBundleResult
    validation_report: ExtractionQualityReport
    artifact_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_name": self.pass_name,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "result": self.result.to_dict(),
            "validation_report": self.validation_report.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def run_segment_extraction_pass(
    book_path: str | Path,
    unit_id: str,
    *,
    context: ExtractionContext | None = None,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache/extraction_passes",
    use_cache: bool = True,
    generated_prompt_parts: list[PromptPart] | None = None,
) -> ExtractionPassRecord:
    index = build_book_index(book_path)
    unit = index.unit_map().get(unit_id)
    if unit is None:
        raise ValueError(f"unknown unit_id: {unit_id}")
    text = extract_unit_text(book_path, unit)
    extraction_context = context or ExtractionContext(frontier=unit_id)
    llm = backend or MockExtractionBackend()
    envelope = build_local_bundle_prompt(unit, text, extraction_context)
    prompt = build_segment_extraction_composition(generated_prompt_parts or [])
    payload = envelope.to_model_payload()
    check_extraction_budget(
        prompt.content,
        payload,
        max_output_tokens=getattr(llm, "max_tokens", DEFAULT_MAX_TOKENS),
    )

    cache_key = build_pass_cache_key(
        pass_name="segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity=llm.model_identity,
    )
    pass_dir = Path(cache_dir) / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        result = result_from_json(result_path.read_text(encoding="utf-8"))
    else:
        raw_response = llm.complete_json(prompt.content, payload)
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

    validation_report = validate_extraction_quality(result.data, text, expected_unit_id=unit.id)
    record = ExtractionPassRecord(
        pass_name="segment-extraction",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        result=result,
        validation_report=validation_report,
        artifact_paths=paths,
    )
    if use_cache:
        write_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=result.raw_response,
            result=result,
            validation_report=validation_report,
            record=record,
        )
    return record


def build_segment_extraction_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        PromptPart(
            part_id="segment-extraction-contract",
            role="static_task_contract",
            source=f"resource:tilusion.prompts/{PROMPT_RESOURCE}",
            content=LOCAL_BUNDLE_SYSTEM_PROMPT,
            metadata={"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=PROMPT_VERSION, parts=parts)


def generated_prompt_part(
    part_id: str,
    *,
    role: str,
    content: str,
    generated_by: str,
    metadata: dict[str, Any] | None = None,
) -> PromptPart:
    return PromptPart(
        part_id=part_id,
        role=role,
        source="generated",
        content=content,
        generated_by=generated_by,
        metadata=metadata or {},
    )


def load_static_prompt_part(
    part_id: str,
    *,
    role: str,
    resource_name: str,
    metadata: dict[str, Any] | None = None,
) -> PromptPart:
    content = resources.files("tilusion.prompts").joinpath(resource_name).read_text(encoding="utf-8")
    return PromptPart(
        part_id=part_id,
        role=role,
        source=f"resource:tilusion.prompts/{resource_name}",
        content=content,
        metadata=metadata or {},
    )


def build_pass_cache_key(
    *,
    pass_name: str,
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    model_identity: str,
) -> str:
    return sha256_json(
        {
            "pass_name": pass_name,
            "prompt_composition": prompt.to_dict(),
            "user_payload_hash": sha256_json(user_payload),
            "model_identity": model_identity,
        }
    )


def pass_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
    }


def write_pass_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    raw_response: str,
    result: LocalBundleResult,
    validation_report: ExtractionQualityReport,
    record: ExtractionPassRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["prompt_composition"]).write_text(
        json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["system_prompt"]).write_text(prompt.content, encoding="utf-8")
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(result.to_json(), encoding="utf-8")
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(paths["manifest"]).write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
