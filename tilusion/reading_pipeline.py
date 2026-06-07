from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backend import (
    LLMBackend,
    MockExtractionBackend,
    parse_json_response,
    sha256_text,
)
from .cache_layout import (
    book_root,
    compute_cross_unit_run_hash,
    compute_unit_run_hash,
    cross_unit_run_dir,
    model_config_for_cache,
    prepend_to_runs_catalog,
    unit_run_dir,
    write_run_manifest,
)
from .extraction_prompts import build_overview_composition
from .overview import (
    ResolvedOverviewSegment,
    resolve_overview_segments,
    run_overview_segmentation_pass,
    segment_hint_payload,
)
from .pass_utils import (
    build_pass_cache_key,
    pass_artifact_paths,
)
from .reading_payloads import (
    build_concept_resolution_payload,
    build_group_resolution_payload,
    build_language_policy,
    build_per_segment_extraction_payload,
    build_unit_logical_grouping_payload,
    build_unit_logical_grouping_payload_v0_2,
    merge_segment_extraction_results,
)
from .reading_schema import SourceBlock, normalize_concept_type
from .reading_prompts import (
    build_concept_resolution_composition,
    build_concept_resolution_v0_2_composition,
    build_group_resolution_composition,
    build_group_resolution_v0_2_composition,
    build_per_segment_extraction_composition,
    build_unit_logical_grouping_composition,
    build_unit_logical_grouping_v0_2_composition,
)
from .reading_schema import READING_UNIT_SCHEMA_VERSION
from .source_blocks import MAX_BLOCK_CHARS, SourceBlockMetrics, split_source_blocks
from .source_index import (
    blocks_for_unit_range,
    build_book_source_index,
    load_book_source_index,
    save_book_source_index,
    source_index_block_to_source_block,
    source_index_cache_path,
)
from .reading_validation import (
    ReadingValidationReport,
    validate_extraction_unit_package,
)
from .book_digest import build_book_digest, make_context_dict
from .book_registry import BookRegistry, find_registry_duplicates
from .registry_index import (
    build_registry_index,
    init_embedding_cache,
    known_concepts_for_blocks,
    select_concept_candidates,
    select_group_candidates,
)
from .registry_delta import RegistryDeltaResult, apply_registry_delta, compute_registry_delta
from .reading_quality import compute_quality_metrics, log_quality_metrics

# re-export for convenience
__all__ = [
    "MockReadingBackend",
    "ReadingPassRecord",
    "ReadingPipelineRecord",
    "run_per_segment_extraction_pass",
    "run_reading_pipeline",
    "write_reading_unit_package",
]


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _last_nonempty_line(text: str) -> str:
    result = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            result = stripped
    return result


def _elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _log_progress(step: int, total: int, description: str, status: str, elapsed_ms: int) -> None:
    print(f"  [{step}/{total}] {description}: {status} ({elapsed_ms}ms)", file=sys.stderr)


def _log_preview(value: Any, *, limit: int = 96) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _overview_segment_count(overview_data: Any) -> int:
    """Return the raw overview segment count from current or legacy payload keys."""
    if not isinstance(overview_data, dict):
        return 0
    segments = overview_data.get("overview_segments")
    if not isinstance(segments, list):
        segments = overview_data.get("segments")
    return len(segments) if isinstance(segments, list) else 0


def _metrics_for_preselected_blocks(segment_id: str, blocks: list[SourceBlock], segment_text: str) -> SourceBlockMetrics:
    covered = sum(block.end - block.start for block in blocks)
    total = len(segment_text)
    oversized = [block.block_id for block in blocks if len(block.text.strip()) > MAX_BLOCK_CHARS]
    return SourceBlockMetrics(
        segment_id=segment_id,
        block_count=len(blocks),
        covered_chars=covered,
        total_chars=total,
        coverage_pct=round((covered / total * 100) if total else 100.0, 2),
        avg_block_size=round(covered / len(blocks), 2) if blocks else 0.0,
        oversized_count=len(oversized),
        oversized_block_ids=oversized,
    )


def _aggregate_per_segment_counts(
    segment_records: list[ReadingPassRecord],
) -> dict[str, Any]:
    """Aggregate factual per-segment extraction counts across all segments."""
    total_blocks = 0
    total_concepts = 0
    total_items = 0
    total_source_block_refs = 0
    per_segment: list[dict[str, Any]] = []

    for record in segment_records:
        counts = record.data.get("metrics", {}).get("counts", {})
        local = counts.get("per_segment", {})
        source_blocks = int(local.get("source_blocks", 0) or 0)
        concepts = int(local.get("concepts", 0) or 0)
        atomic_items = int(local.get("atomic_items", 0) or 0)
        source_block_refs = int(local.get("source_block_refs", 0) or 0)
        total_blocks += source_blocks
        total_concepts += concepts
        total_items += atomic_items
        total_source_block_refs += source_block_refs
        per_segment.append(dict(local))

    return {
        "segment_count": len(segment_records),
        "total_source_blocks": total_blocks,
        "total_concepts": total_concepts,
        "total_atomic_items": total_items,
        "total_source_block_refs": total_source_block_refs,
        "concepts_per_block": round(total_concepts / total_blocks, 2) if total_blocks else 0.0,
        "items_per_block": round(total_items / total_blocks, 2) if total_blocks else 0.0,
        "avg_source_blocks_per_item": round(total_source_block_refs / total_items, 2) if total_items else 0.0,
        "per_segment": per_segment,
    }


def _raise_on_validation_errors(pass_name: str, report: ReadingValidationReport) -> None:
    if report.passed:
        return
    issue_summary = "; ".join(
        f"{issue.code} at {issue.path}: {issue.message}"
        for issue in report.issues
        if issue.severity == "error"
    )
    raise ValueError(f"{pass_name} validation failed: {issue_summary}")


# ── Human-readable merge/proposal logging ───────────────────────────────────


def _log_concept_resolution_preview(
    proposals: list[dict[str, Any]],
    unit_concepts: list[dict[str, Any]],
    registry: BookRegistry | None,
    *,
    limit: int = 8,
) -> None:
    if not proposals:
        return
    unit_by_id = {str(c.get("concept_id", "")): c for c in unit_concepts if isinstance(c, dict)}
    interesting = [p for p in proposals if p.get("proposal_type") in {"link", "merge", "reclassify", "refine"}]
    if not interesting:
        return
    print(f"    concept proposals preview ({min(len(interesting), limit)}/{len(interesting)}):", file=sys.stderr)
    for prop in interesting[:limit]:
        refs = [str(ref) for ref in prop.get("target_refs", [])]
        unit = unit_by_id.get(refs[0], {}) if refs else {}
        reg_id = str(prop.get("registry_ref") or "")
        reg = registry.get_concept(reg_id).to_dict() if registry is not None and reg_id and registry.get_concept(reg_id) else {}
        print(
            f"      {prop.get('proposal_type')} {refs or ['?']} -> {reg_id or '-'} | "
            f"unit {_concept_brief(unit)} | registry {_concept_brief(reg)} | "
            f"why={_log_preview(prop.get('rationale', ''), limit=100)}",
            file=sys.stderr,
        )
    if len(interesting) > limit:
        print(f"      ... {len(interesting) - limit} more concept proposal(s)", file=sys.stderr)


def _log_group_resolution_preview(
    proposals: list[dict[str, Any]],
    unit_groups: list[dict[str, Any]],
    registry: BookRegistry | None,
    *,
    limit: int = 8,
) -> None:
    if not proposals:
        return
    unit_by_id = {str(g.get("group_id", "")): g for g in unit_groups if isinstance(g, dict)}
    interesting = [p for p in proposals if p.get("proposal_type") in {"continue", "mutate", "merge_groups", "cross_group_edge"}]
    if not interesting:
        return
    print(f"    group proposals preview ({min(len(interesting), limit)}/{len(interesting)}):", file=sys.stderr)
    for prop in interesting[:limit]:
        unit_id = str(prop.get("unit_group_ref") or "")
        reg_id = str(prop.get("registry_group_ref") or "")
        unit = unit_by_id.get(unit_id, {})
        reg = registry._groups.get(reg_id, {}) if registry is not None and reg_id else {}
        print(
            f"      {prop.get('proposal_type')} {unit_id or '-'} -> {reg_id or '-'} | "
            f"unit {_group_brief(unit)} | registry {_group_brief(reg)} | "
            f"why={_log_preview(prop.get('rationale', ''), limit=100)}",
            file=sys.stderr,
        )
    if len(interesting) > limit:
        print(f"      ... {len(interesting) - limit} more group proposal(s)", file=sys.stderr)


def _log_registry_delta_preview(
    delta: RegistryDeltaResult,
    registry: BookRegistry,
    *,
    limit: int = 10,
) -> None:
    interesting = [
        op for op in delta.operations
        if op.get("op_type") in {"merge_concepts", "continue_group", "mutate_group"}
    ]
    if not interesting:
        return
    print(f"  [book] merge preview ({min(len(interesting), limit)}/{len(interesting)}):", file=sys.stderr)
    for op in interesting[:limit]:
        op_type = op.get("op_type", "")
        if op_type == "merge_concepts":
            unit = op.get("unit_concept", {})
            reg_id = str(op.get("book_concept_id") or "")
            reg = registry.get_concept(reg_id).to_dict() if reg_id and registry.get_concept(reg_id) else {}
            print(
                f"    concept {unit.get('concept_id', '?')} -> {reg_id} "
                f"({op.get('match_reason', '')}) | unit {_concept_brief(unit)} | registry {_concept_brief(reg)}",
                file=sys.stderr,
            )
        else:
            group = op.get("group", {})
            reg_id = str(op.get("book_group_id") or "")
            reg = registry._groups.get(reg_id, {}) if reg_id else {}
            print(
                f"    group {op_type} {group.get('group_id', '?')} -> {reg_id} | "
                f"unit {_group_brief(group)} | registry {_group_brief(reg)}",
                file=sys.stderr,
            )
    if len(interesting) > limit:
        print(f"    ... {len(interesting) - limit} more merge operation(s)", file=sys.stderr)


def _concept_brief(concept: dict[str, Any]) -> str:
    if not concept:
        return "<missing>"
    name = concept.get("canonical_name") or concept.get("surface") or concept.get("concept_id") or "?"
    return (
        f"{concept.get('concept_id', '?')} {concept.get('concept_type', '?')} "
        f"{_log_preview(name, limit=32)} — {_log_preview(concept.get('summary', ''), limit=72)}"
    )


def _group_brief(group: dict[str, Any]) -> str:
    if not group:
        return "<missing>"
    return (
        f"{group.get('group_id', '?')} {group.get('group_type', '?')} "
        f"items={len(group.get('item_refs', []) or [])} concepts={len(group.get('concept_refs', []) or [])} — "
        f"{_log_preview(group.get('summary', ''), limit=90)}"
    )


# ── Pass record ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ReadingPassRecord:
    pass_name: str
    cache_key: str
    cache_dir: str
    cache_hit: bool
    raw_response: str
    data: dict[str, Any]
    validation_report: ReadingValidationReport
    artifact_paths: dict[str, str]
    conversation: Any | None = None
    pre_fallback_conversation: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "pass_name": self.pass_name,
            "cache_key": self.cache_key,
            "cache_dir": self.cache_dir,
            "cache_hit": self.cache_hit,
            "artifact_paths": self.artifact_paths,
            "data": self.data,
            "validation_report": self.validation_report.to_dict(),
        }
        if self.conversation is not None and hasattr(self.conversation, "to_dict"):
            d["conversation_id"] = self.conversation.conversation_id
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class ReadingPipelineRecord:
    unit_id: str
    elapsed_ms: int
    unit_package_path: str
    passes: dict[str, dict[str, Any]]
    data: dict[str, Any]
    validation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "elapsed_ms": self.elapsed_ms,
            "unit_package_path": self.unit_package_path,
            "passes": self.passes,
            "data": self.data,
            "validation": self.validation,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class AgenticResolutionResult:
    """Result of a multi-round registry resolution pass.

    raw_data is the model's final proposal JSON. applied_subject is the
    validated extraction-package-shaped view built from those proposals. Keeping
    them separate prevents proposal lists from being lost when the applied
    subject is used for validation.
    """

    raw_data: dict[str, Any]
    applied_subject: dict[str, Any]
    conversation: Any
    validation_report: ReadingValidationReport
    turns_used: int
    exhausted: bool = False
    fallback_used: bool = False
    failure_reason: str = ""
    agentic_trace: dict[str, Any] | None = None
    pre_fallback_conversation: Any | None = None


# ── Mock response functions ──────────────────────────────────────────────────


def mock_per_segment_extraction_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    segment = user_payload.get("segment", {})
    segment_id = segment.get("segment_id", "seg-0001")
    blocks = user_payload.get("source_blocks", [])
    first_block_id = blocks[0]["block_id"] if blocks else f"{segment_id}-block-0000"

    concepts = [
        {
            "concept_id": "concept-0001",
            "surface": "mock surface",
            "concept_type": "other",
            "source_block_refs": [first_block_id],
            "canonical_name": "",
            "summary": f"Mock concept from {segment_id}.",
            "aliases": [],
            "observed_surfaces": ["mock surface"],
            "facets": [],
            "uncertainty": [],
            "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"},
        }
    ] if blocks else []
    atomic_items = [
        {
            "item_id": "item-0001",
            "item_type": "observation",
            "summary": f"Mock item from {segment_id}.",
            "source_block_refs": [first_block_id],
            "concept_refs": ["concept-0001"],
            "temporal_attributes": [],
            "attributes": {},
            "uncertainty": [],
            "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"},
        }
    ] if blocks else []

    return {
        "unit_id": unit_id,
        "segment_id": segment_id,
        "concepts": concepts,
        "atomic_items": atomic_items,
        "warnings": ["mock per-segment extraction: placeholder records"],
    }


def mock_unit_logical_grouping_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    concepts = user_payload.get("concepts", [])
    items = user_payload.get("atomic_items", [])
    concept_ids = [c["concept_id"] for c in concepts if isinstance(c, dict)]
    item_ids = [it["item_id"] for it in items if isinstance(it, dict)]

    concept_deltas: list[dict[str, Any]] = []
    logical_groups: list[dict[str, Any]] = []

    if items:
        logical_groups.append(
            {
                "group_id": "group-0001",
                "group_type": "other",
                "summary": "Mock logical group from all items.",
                "item_refs": item_ids[:],
                "concept_refs": concept_ids[:],
                "graph": {},
                "uncertainty": [],
                "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            }
        )

    return {
        "unit_id": unit_id,
        "concept_deltas": concept_deltas,
        "logical_groups": logical_groups,
        "unresolved_items": [],
        "warnings": ["mock unit logical grouping: placeholder records"],
    }


def mock_book_digest_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    digest = (
        "# Book Context Digest\n\n"
        "## Narrative State\n"
        "Mock narrative state for the upcoming unit.\n\n"
        "## Extraction Guidance\n"
        "Mock extraction guidance: watch for character development and thematic patterns."
    )
    return {
        "digest": digest,
        "warnings": ["mock book digest: placeholder"],
    }


def mock_book_digest_update_response(
    previous_digest: str,
    conversation: Any,
) -> dict[str, Any]:
    """Mock response for the Conversation C digest update turn.

    Produces an updated digest that references the previous digest when
    available, simulating the real digest evolution behavior.
    """
    if previous_digest:
        digest = (
            f"{previous_digest}\n\n"
            "## Updated (mock)\n"
            "New entities from this unit: mock-concept-new (person). "
            "The narrative continues — watch for developments."
        )
    else:
        digest = (
            "# Book Context Digest\n\n"
            "## Known Entities\n"
            "| Name | Type | Notes |\n"
            "|---|---|---|\n"
            "| mock-entity | person | First unit extraction |\n\n"
            "## Attention Guidance\n"
            "First unit digest — continue extraction."
        )
    return {
        "digest": digest,
        "entity_count": 1,
        "warnings": ["mock digest update: placeholder"],
    }


# ── Mock backend ─────────────────────────────────────────────────────────────


class MockReadingBackend:
    model_identity = "mock-reading-v0"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        task = user_payload.get("task", "")

        if task == "per_segment_extraction":
            return json.dumps(mock_per_segment_extraction_response(user_payload), ensure_ascii=False)
        if task == "unit_logical_grouping":
            return json.dumps(mock_unit_logical_grouping_response(user_payload), ensure_ascii=False)
        if task == "cross_unit_concept_resolution":
            return json.dumps(mock_concept_resolution_response(user_payload), ensure_ascii=False)
        if task == "cross_unit_group_resolution":
            return json.dumps(mock_group_resolution_response(user_payload), ensure_ascii=False)
        if task == "book_digest":
            return json.dumps(mock_book_digest_response(user_payload), ensure_ascii=False)

        raise ValueError(f"MockReadingBackend: unknown task {task!r}")

    def start_conversation(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        *,
        pass_name: str = "",
    ) -> Any:
        from .conversation import ConversationContext, TurnMetadata

        raw = self.complete_json(system_prompt, user_payload)
        ctx = ConversationContext.create(
            model_identity=self.model_identity,
            pass_name=pass_name,
            system_prompt=system_prompt,
            user_payload=user_payload,
        )
        ctx.record_turn(
            assistant_response=raw,
            metadata=TurnMetadata(
                turn_index=1,
                turn_type="initial",
                elapsed_ms=0,
            ),
        )
        return ctx

    def continue_conversation(
        self,
        conversation: Any,
        user_message: str,
    ) -> Any:
        from .conversation import TurnMetadata

        conversation.append_user_message(user_message)

        # Detect tool result messages (agentic v0.2)
        try:
            msg_data = json.loads(user_message)
            if isinstance(msg_data, dict) and "tool_results" in msg_data:
                task = conversation.initial_payload.get("task", "")
                if task == "cross_unit_concept_resolution":
                    resp = mock_concept_resolution_response(
                        conversation.initial_payload
                    )
                    resp["status"] = "complete"
                    assistant_response = json.dumps(resp, ensure_ascii=False)
                elif task == "cross_unit_group_resolution":
                    resp = mock_group_resolution_response(
                        conversation.initial_payload
                    )
                    resp["status"] = "complete"
                    assistant_response = json.dumps(resp, ensure_ascii=False)
                else:
                    assistant_response = json.dumps(
                        {"status": "complete", "warnings": ["mock: unknown task"]},
                        ensure_ascii=False,
                    )
                conversation.record_turn(
                    assistant_response=assistant_response,
                    metadata=TurnMetadata(
                        turn_index=conversation.turn_count + 1,
                        turn_type="tool_result_response",
                        elapsed_ms=0,
                    ),
                )
                return conversation
        except (json.JSONDecodeError, ValueError):
            pass

        # Detect digest update turn
        try:
            msg_data = json.loads(user_message)
            if isinstance(msg_data, dict) and msg_data.get("task") == "update_book_digest":
                prev = msg_data.get("previous_digest", "")
                updated = mock_book_digest_update_response(prev, conversation)
                conversation.record_turn(
                    assistant_response=json.dumps(updated, ensure_ascii=False),
                    metadata=TurnMetadata(
                        turn_index=conversation.turn_count + 1,
                        turn_type="digest_update",
                        elapsed_ms=0,
                    ),
                )
                return conversation
        except (json.JSONDecodeError, ValueError):
            pass

        conversation.record_turn(
            assistant_response=json.dumps(
                {"repairs": [], "explanation": "mock repair — no fixes needed"},
                ensure_ascii=False,
            ),
            metadata=TurnMetadata(
                turn_index=conversation.turn_count + 1,
                turn_type="repair",
                elapsed_ms=0,
            ),
        )
        return conversation


# ── Pass: per-segment extraction ─────────────────────────────────────────────


def run_per_segment_extraction_pass(
    *,
    unit_id: str,
    segment: ResolvedOverviewSegment,
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool = True,
    context: dict[str, Any] | None = None,
    unit_text: str | None = None,
    source_blocks: list[SourceBlock] | None = None,
    source_index_id: str | None = None,
    language_policy: dict[str, str] | None = None,
) -> ReadingPassRecord:
    """Run the reading per-segment extraction pass on one segment.

    Splits the segment text into deterministic source blocks, builds a
    v0.3 per-segment payload with inline block markers, calls the LLM,
    and validates the returned concepts and atomic_items against the
    authoritative source blocks.
    """
    # Deterministic source block selection/splitting. When source-index blocks
    # are provided, expand the LLM segment to full block boundaries so stable
    # book-scoped block IDs are not clipped into transient sub-block IDs.
    if source_blocks is not None:
        blocks = source_blocks
        block_unit_text = unit_text if unit_text is not None else segment.text
        if blocks and unit_text is not None:
            block_unit_offset = min(block.start for block in blocks)
            expanded_end = max(block.end for block in blocks)
            segment_text = unit_text[block_unit_offset:expanded_end]
        else:
            block_unit_offset = segment.start if unit_text is not None else 0
            segment_text = segment.text
        block_metrics = _metrics_for_preselected_blocks(segment.segment_id, blocks, segment_text)
    else:
        segment_text = segment.text
        block_unit_text = unit_text if unit_text is not None else segment_text
        block_unit_offset = segment.start if unit_text is not None else 0
        blocks, block_metrics = split_source_blocks(
            segment_text,
            segment_id=segment.segment_id,
            unit_id=unit_id,
            unit_text=block_unit_text,
            unit_offset=block_unit_offset,
        )

    prompt = build_per_segment_extraction_composition()
    payload = build_per_segment_extraction_payload(
        unit_id=unit_id,
        segment={
            "segment_id": segment.segment_id,
            "title": segment.title,
            "summary": segment.summary,
            "source_index_id": source_index_id or "",
        },
        text=segment_text,
        source_blocks=blocks,
        segment_offset=block_unit_offset,
        context=context,
        language_policy=language_policy,
    )

    cache_key = build_pass_cache_key(
        pass_name="per-segment-extraction",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
        data = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        block_dicts = [b.to_dict() for b in blocks]

        def _build_subject(llm_data: dict[str, Any]) -> dict[str, Any]:
            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": unit_id,
                "source": {"unit_text": block_unit_text} if unit_text is not None else {},
                "source_blocks": block_dicts,
                "concepts": llm_data.get("concepts", []),
                "atomic_items": llm_data.get("atomic_items", []),
                "logical_groups": [],
                "unresolved_items": [],
                "validation": {},
                "context_metadata": {"source_index_id": source_index_id} if source_index_id else {},
            }

        from .repair import run_agentic_pass

        data, conversation, validation_report = run_agentic_pass(
            backend=backend,
            prompt=prompt,
            payload=payload,
            validation_subject_builder=_build_subject,
            pass_name="per-segment-extraction",
        )
        raw_response = _last_assistant_content(conversation)

    _normalize_uncertainty_fields(data)

    # Re-validate if cache hit (no agentic loop ran)
    if cache_hit:
        validation_subject = {
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": unit_id,
            "source": {"unit_text": block_unit_text} if unit_text is not None else {},
            "source_blocks": [b.to_dict() for b in blocks],
            "concepts": data.get("concepts", []),
            "atomic_items": data.get("atomic_items", []),
            "logical_groups": [],
            "unresolved_items": [],
            "validation": {},
            "context_metadata": {"source_index_id": source_index_id} if source_index_id else {},
        }
        validation_report = validate_extraction_unit_package(validation_subject)
        _raise_on_validation_errors("per-segment-extraction", validation_report)

    # Compute factual per-segment counts for logging and final aggregation.
    llm_concepts = data.get("concepts", [])
    llm_items = data.get("atomic_items", [])
    n_blocks = len(blocks)
    n_concepts = len(llm_concepts)
    n_items = len(llm_items)
    total_source_block_refs = sum(
        len(_as_list(it.get("source_block_refs"))) for it in llm_items if isinstance(it, dict)
    )

    per_segment_counts = {
        "segment_id": segment.segment_id,
        "source_blocks": n_blocks,
        "concepts": n_concepts,
        "atomic_items": n_items,
        "source_block_refs": total_source_block_refs,
        "concepts_per_block": round(n_concepts / n_blocks, 2) if n_blocks else 0.0,
        "items_per_block": round(n_items / n_blocks, 2) if n_blocks else 0.0,
        "avg_source_blocks_per_item": round(total_source_block_refs / n_items, 2) if n_items else 0.0,
        "source_block_splitter": block_metrics.to_dict(),
    }

    enriched_data = {
        **data,
        "source_blocks": [b.to_dict() for b in blocks],
        "context_metadata": {"source_index_id": source_index_id} if source_index_id else {},
        "metrics": {"counts": {"per_segment": per_segment_counts}},
    }

    record = ReadingPassRecord(
        pass_name="per-segment-extraction",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data=enriched_data,
        validation_report=validation_report,
        artifact_paths=paths,
        conversation=locals().get("conversation"),
    )

    if use_cache:
        _write_reading_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=record.data,
            validation_report=validation_report,
            record=record,
        )

    return record


# ── Pass: unit logical grouping ────────────────────────────────────────────────


def _validate_merge_deltas(
    deltas: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Screen LLM merge deltas for unsafe patterns before application.

    Returns ``(safe_deltas, rejected_deltas)``. Rejected deltas become
    ``unresolved_items`` with kind ``merge_proposal_rejected``.

    Rules are generic and data-driven — they do not hardcode specific
    surface values, languages, or domain concepts.
    """
    if not deltas:
        return deltas, []

    concept_by_id = {c["concept_id"]: c for c in concepts}
    safe: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for delta in deltas:
        if delta.get("delta_type") != "merge":
            safe.append(delta)
            continue

        target_refs: list[str] = _as_list(delta.get("target_refs"))
        changes: dict[str, Any] = delta.get("changes") or {}
        targets = [concept_by_id[ref] for ref in target_refs if ref in concept_by_id]

        if len(targets) < 2:
            safe.append(delta)
            continue

        reason = _classify_merge_risk(targets, changes)
        if reason:
            rejected.append({
                "delta": delta,
                "reason": reason,
            })
        else:
            safe.append(delta)

    return safe, rejected


def _classify_merge_risk(
    targets: list[dict[str, Any]],
    changes: dict[str, Any],
) -> str | None:
    """Return rejection reason if merge is unsafe, None if it should proceed.

    A merge is safe when the targets share an identity signal:
    same canonical_name, same surface, or the proposed merged name
    matches an attested name of one of the targets.

    A merge is rejected when distinct entities are being collapsed
    into a synthetic collection or category concept.
    """
    surfaces: set[str] = {str(c.get("surface", "")) for c in targets}
    cnames: set[str] = {
        str(c.get("canonical_name", "")) for c in targets if c.get("canonical_name")
    }
    types: set[str] = {str(c.get("concept_type", "")) for c in targets}
    proposed_surface = str(changes.get("surface", "")).strip()
    proposed_cname = str(changes.get("canonical_name", "")).strip()

    known_names: set[str] = surfaces | cnames
    known_names.discard("")

    # Shared non-empty canonical_name → same identity already established
    if len(cnames) == 1 and "" not in cnames:
        return None

    # Same surface across all targets → probable duplicate extraction
    if len(surfaces) == 1 and "" not in surfaces:
        return None

    # Proposed name matches a known target name → LLM picking canonical form
    if proposed_surface and proposed_surface in known_names:
        return None
    if proposed_cname and proposed_cname in known_names:
        return None

    # time_anchor with different surfaces → distinct temporal references
    if "time_anchor" in types:
        return "merge_rejected: merging distinct time_anchor concepts with different surfaces"

    # place with different surfaces → distinct locations
    if "place" in types:
        return "merge_rejected: merging distinct place concepts with different surfaces"

    # source with different surfaces → distinct cited works
    if "source" in types:
        return "merge_rejected: merging distinct source concepts with different surfaces"

    # Proposed name is new (not attested in any target) → synthetic collection
    if proposed_surface or proposed_cname:
        return "merge_rejected: proposed merged name does not match any attested target name"

    # Different surfaces, different canonical_names, no proposed name → unclear
    if len(surfaces) > 1:
        return "merge_rejected: targets have distinct surfaces and no shared identity signal"

    return None


def _build_merge_rejection_items(
    rejected: list[dict[str, Any]],
    existing_unresolved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert rejected merge deltas into unresolved_items entries."""
    if not rejected:
        return []
    start_index = len(existing_unresolved)
    items: list[dict[str, Any]] = []
    for i, entry in enumerate(rejected):
        delta = entry["delta"]
        items.append({
            "item_id": f"unresolved-{start_index + i + 1:04d}",
            "kind": "merge_proposal_rejected",
            "target_refs": delta.get("target_refs", []),
            "delta_id": delta.get("delta_id", ""),
            "changes": delta.get("changes", {}),
            "rationale": delta.get("rationale", ""),
            "rejection_reason": entry["reason"],
            "summary": f"LLM proposed merging concepts but the merge was rejected: {entry['reason']}.",
        })
    return items


def _apply_concept_deltas(
    concepts: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
    *,
    unit_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Apply concept deltas to the concept list.

    Returns ``(updated_concepts, remap_dict)`` where *remap_dict* maps
    old concept IDs to new concept IDs for downstream ref rewriting.
    """
    if not deltas:
        return concepts, {}

    concept_by_id = {c["concept_id"]: c for c in concepts}
    remap: dict[str, str] = {}
    next_index = len(concepts)

    for delta in deltas:
        delta_type = delta.get("delta_type", "")
        target_refs = delta.get("target_refs", [])
        changes = delta.get("changes", {})

        if delta_type == "merge":
            known_refs = [ref for ref in target_refs if ref in concept_by_id]
            if not known_refs:
                continue
            primary_id = known_refs[0]
            primary = concept_by_id[primary_id]
            for ref in known_refs:
                remap[ref] = primary_id

            for field in ("canonical_name", "summary", "surface", "concept_type"):
                if changes.get(field):
                    primary[field] = changes[field]

            for field in ("aliases", "observed_surfaces", "source_block_refs", "facets", "uncertainty"):
                seen = set()
                values: list[Any] = []
                for value in _as_list(primary.get(field)):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
                for ref in known_refs[1:]:
                    for value in _as_list(concept_by_id[ref].get(field)):
                        if value not in seen:
                            seen.add(value)
                            values.append(value)
                for value in _as_list(changes.get(field)):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
                if values:
                    primary[field] = values

            merged_from = []
            for ref in known_refs:
                merged_from.extend(_as_list(concept_by_id[ref].get("merged_from")) or [ref])
            primary["merged_from"] = list(dict.fromkeys(merged_from))

            for ref in known_refs[1:]:
                del concept_by_id[ref]

        elif delta_type == "split":
            original_id = target_refs[0] if target_refs else ""
            split_into = changes.get("split_into", [])
            if original_id in concept_by_id:
                del concept_by_id[original_id]
            for i, new_concept in enumerate(split_into):
                next_index += 1
                new_id = f"concept-{next_index:04d}"
                new_concept["concept_id"] = new_id
                new_concept.setdefault("provenance", {"grounding": "synthesis", "created_by": "llm_inferred"})
                concept_by_id[new_id] = new_concept
                if i == 0 and original_id:
                    remap[original_id] = new_id

        elif delta_type == "refine":
            for ref in target_refs:
                if ref in concept_by_id:
                    c = concept_by_id[ref]
                    for field in ("canonical_name", "summary", "aliases", "observed_surfaces", "facets", "uncertainty"):
                        if field in changes:
                            c[field] = changes[field]

        elif delta_type == "reclassify":
            for ref in target_refs:
                if ref in concept_by_id and "concept_type" in changes:
                    concept_by_id[ref]["concept_type"] = changes["concept_type"]

    return list(concept_by_id.values()), remap


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _last_assistant_content(conversation: Any) -> str:
    """Return the content of the last assistant message in the conversation."""
    for msg in reversed(conversation.messages):
        if msg.get("role") == "assistant":
            return str(msg.get("content", ""))
    return ""


def _load_cached_digest(registry_cache_root: Path) -> str | None:
    """Load the cached book digest from the registry directory."""
    path = registry_cache_root / "book_digest.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("digest") if isinstance(data, dict) else None
    return None


def _save_cached_digest(registry_cache_root: Path, digest: str) -> None:
    """Save the book digest to the registry directory."""
    registry_cache_root.mkdir(parents=True, exist_ok=True)
    path = registry_cache_root / "book_digest.json"
    path.write_text(
        json.dumps({"digest": digest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_uncertainty_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce non-string uncertainty list items to strings in-place.

    LLMs occasionally return structured objects in uncertainty lists
    (e.g. ``{"note": "ambiguous"}``) instead of plain strings. This
    normalizes them so downstream validation passes.
    """
    for concept in _as_list(data.get("concepts")):
        concept["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(concept.get("uncertainty"))]

    for item in _as_list(data.get("atomic_items")):
        item["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(item.get("uncertainty"))]
        for attr in _as_list(item.get("temporal_attributes")):
            attr["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(attr.get("uncertainty"))]

    for group in _as_list(data.get("logical_groups")):
        group["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(group.get("uncertainty"))]
        for node in _as_list((group.get("graph") or {}).get("nodes")):
            node["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(node.get("uncertainty"))]
        for edge in _as_list((group.get("graph") or {}).get("edges")):
            edge["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(edge.get("uncertainty"))]

    for unresolved in _as_list(data.get("unresolved_items")):
        unresolved["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(unresolved.get("uncertainty"))]

    for delta in _as_list(data.get("concept_deltas")):
        delta["uncertainty"] = [_uncertainty_to_str(v) for v in _as_list(delta.get("uncertainty"))]

    return data


def _uncertainty_to_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _dedupe_equivalent_concepts(
    concepts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Merge concepts that became equivalent after unit-level deltas."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    remap: dict[str, str] = {}

    for concept in concepts:
        current = dict(concept)
        concept_id = current.get("concept_id", "")
        normalized_type = normalize_concept_type(current.get("concept_type", ""))
        current["concept_type"] = normalized_type
        identity = str(current.get("canonical_name") or current.get("surface") or "").strip()
        if not identity or not concept_id:
            deduped.append(current)
            continue

        key = (identity, normalized_type)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = current
            deduped.append(current)
            continue

        target_id = existing.get("concept_id", "")
        if target_id:
            remap[concept_id] = target_id
        _merge_concept_into(existing, current)

    return deduped, remap


def _merge_concept_into(target: dict[str, Any], source: dict[str, Any]) -> None:
    """In-place union for deterministic post-delta concept dedupe."""
    for field in ("canonical_name", "surface", "summary"):
        if not target.get(field) and source.get(field):
            target[field] = source[field]

    for field in ("aliases", "observed_surfaces", "source_block_refs", "facets", "uncertainty", "merged_from"):
        values = list(_as_list(target.get(field)))
        if field == "merged_from" and not values and target.get("concept_id"):
            values = [target["concept_id"]]
        seen = set(values)
        source_values = _as_list(source.get(field))
        if field == "merged_from" and not source_values and source.get("concept_id"):
            source_values = [source["concept_id"]]
        for value in source_values:
            if value not in seen:
                seen.add(value)
                values.append(value)
        if values:
            target[field] = values


def _compose_concept_remaps(
    first: dict[str, str], second: dict[str, str]
) -> dict[str, str]:
    """Compose LLM-delta remaps with deterministic dedupe remaps."""
    composed = dict(second)
    for old, mid in first.items():
        composed[old] = second.get(mid, mid)
    return composed


def _compute_grouping_counts(
    groups: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute factual counts from the logical grouping pass output."""
    group_count = len(groups)
    singleton_count = 0
    groups_with_graph = 0
    total_edges = 0

    items_in_groups: set[str] = set()
    for group in groups:
        refs = _as_list(group.get("item_refs"))
        if len(refs) == 1:
            singleton_count += 1
        for ref in refs:
            if isinstance(ref, str):
                items_in_groups.add(ref)
        graph = group.get("graph")
        if isinstance(graph, dict):
            edges = _as_list(graph.get("edges"))
            if edges:
                groups_with_graph += 1
                total_edges += len(edges)

    all_item_ids = {
        it["item_id"] for it in items if isinstance(it, dict) and "item_id" in it
    }
    ungrouped_count = len(all_item_ids - items_in_groups)

    temporal_event_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("item_type") not in ("event", "action", "scene"):
            continue
        tas = item.get("temporal_attributes")
        if isinstance(tas, list) and any(
            isinstance(ta, dict) and ta.get("kind") in ("explicit", "implicit", "relative")
            for ta in tas
        ):
            temporal_event_count += 1

    return {
        "logical_groups": group_count,
        "singleton_groups": singleton_count,
        "groups_with_graph": groups_with_graph,
        "graph_edges": total_edges,
        "atomic_items_grouped": len(items_in_groups),
        "atomic_items_ungrouped": ungrouped_count,
        "event_like_items_with_temporal_hints": temporal_event_count,
        "timeline_or_temporal_sequence_groups": sum(
            1 for g in groups if g.get("group_type") in ("timeline", "temporal_sequence")
        ),
    }


# ── Cross-unit resolution application functions ───────────────────────────


def _apply_concept_resolution(
    concepts: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    *,
    unit_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, dict[str, Any]]]:
    """Apply LLM concept resolution proposals.

    Returns ``(updated_concepts, concept_remap, implicit_ref_map)`` where
    *concept_remap* maps old concept IDs to new concept IDs, and
    *implicit_ref_map* collects implicit item references per concept.
    """
    if not proposals:
        return concepts, {}, {}

    concept_by_id = {c["concept_id"]: c for c in concepts}
    remap: dict[str, str] = {}
    implicit_ref_map: dict[str, dict[str, Any]] = {}
    next_index = len(concepts)

    for prop in proposals:
        prop_type = prop.get("proposal_type", "")
        target_refs = prop.get("target_refs", [])
        changes = prop.get("changes", {})
        registry_ref = prop.get("registry_ref", "")

        # Collect implicit_refs from link proposals
        implicit_refs = prop.get("implicit_refs", [])
        if implicit_refs and prop_type == "link" and registry_ref:
            implicit_ref_map[registry_ref] = {
                "unit_concept_ref": target_refs[0] if target_refs else "",
                "implicit_refs": implicit_refs,
            }

        if prop_type == "link":
            # Cross-unit identity: mark the unit concept as linked to registry
            for ref in target_refs:
                if ref in concept_by_id:
                    concept_by_id[ref]["registry_ref"] = registry_ref
                    # Update with LLM-refined fields if provided
                    for field in ("canonical_name", "summary", "surface"):
                        if changes.get(field):
                            concept_by_id[ref][field] = changes[field]

        elif prop_type == "merge":
            known_refs = [ref for ref in target_refs if ref in concept_by_id]
            if len(known_refs) < 2:
                continue
            primary_id = known_refs[0]
            primary = concept_by_id[primary_id]
            for ref in known_refs:
                remap[ref] = primary_id

            for field in ("canonical_name", "summary", "surface", "concept_type"):
                if changes.get(field):
                    primary[field] = changes[field]

            for field in ("aliases", "observed_surfaces", "source_block_refs", "facets", "uncertainty"):
                seen = set()
                values: list[Any] = []
                for value in _as_list(primary.get(field)):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
                for ref in known_refs[1:]:
                    for value in _as_list(concept_by_id[ref].get(field)):
                        if value not in seen:
                            seen.add(value)
                            values.append(value)
                for value in _as_list(changes.get(field)):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
                if values:
                    primary[field] = values

            merged_from = []
            for ref in known_refs:
                merged_from.extend(_as_list(concept_by_id[ref].get("merged_from")) or [ref])
            primary["merged_from"] = list(dict.fromkeys(merged_from))

            for ref in known_refs[1:]:
                del concept_by_id[ref]

        elif prop_type == "split":
            original_id = target_refs[0] if target_refs else ""
            split_into = changes.get("split_into", [])
            if original_id in concept_by_id:
                del concept_by_id[original_id]
            for i, new_concept in enumerate(split_into):
                next_index += 1
                new_id = f"concept-{next_index:04d}"
                new_concept["concept_id"] = new_id
                new_concept.setdefault("provenance", {"grounding": "synthesis", "created_by": "llm_inferred"})
                concept_by_id[new_id] = new_concept
                if i == 0 and original_id:
                    remap[original_id] = new_id

        elif prop_type == "refine":
            for ref in target_refs:
                if ref in concept_by_id:
                    c = concept_by_id[ref]
                    for field in ("canonical_name", "summary", "aliases", "observed_surfaces", "facets", "uncertainty"):
                        if field in changes:
                            c[field] = changes[field]

        elif prop_type == "reclassify":
            for ref in target_refs:
                if ref in concept_by_id and "concept_type" in changes:
                    concept_by_id[ref]["concept_type"] = changes["concept_type"]

        elif prop_type == "new_concept":
            # Re-confirm registry_ref is empty — these stay local
            for ref in target_refs:
                if ref in concept_by_id:
                    concept_by_id[ref].pop("registry_ref", None)

    return list(concept_by_id.values()), remap, implicit_ref_map


def _apply_group_resolution(
    groups: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    *,
    unit_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply LLM group resolution proposals.

    Returns ``(updated_groups, cross_group_edges)``.
    """
    if not proposals:
        return groups, []

    group_by_id = {g["group_id"]: g for g in groups}
    cross_group_edges: list[dict[str, Any]] = []
    next_index = len(groups)

    for prop in proposals:
        prop_type = prop.get("proposal_type", "")
        unit_group_ref = prop.get("unit_group_ref", "")
        registry_group_ref = prop.get("registry_group_ref", "")
        changes = prop.get("changes", {})

        if prop_type == "continue":
            if unit_group_ref in group_by_id:
                group_by_id[unit_group_ref]["registry_group_ref"] = registry_group_ref
                group_by_id[unit_group_ref]["_continuation"] = "continue"
                if changes.get("summary"):
                    group_by_id[unit_group_ref]["summary"] = changes["summary"]

        elif prop_type == "mutate":
            if unit_group_ref in group_by_id:
                group_by_id[unit_group_ref]["registry_group_ref"] = registry_group_ref
                group_by_id[unit_group_ref]["_continuation"] = "mutate"
                for field in ("summary", "group_type"):
                    if changes.get(field):
                        group_by_id[unit_group_ref][field] = changes[field]

        elif prop_type == "new_thread":
            if unit_group_ref in group_by_id:
                group_by_id[unit_group_ref]["_continuation"] = "new_thread"

        elif prop_type == "cross_group_edge":
            edge = prop.get("edge", {})
            if edge:
                cross_group_edges.append({
                    "source_group": edge.get("source_group", ""),
                    "target_group": edge.get("target_group", ""),
                    "edge_type": edge.get("edge_type", "related_to"),
                    "summary": edge.get("summary", ""),
                    "provenance": prop.get("provenance", {}),
                    "uncertainty": prop.get("uncertainty", []),
                })

        elif prop_type == "merge_groups":
            target_refs = prop.get("target_refs", [])
            known_refs = [ref for ref in target_refs if ref in group_by_id]
            if len(known_refs) < 2:
                continue
            primary_id = known_refs[0]
            primary = group_by_id[primary_id]
            for ref in known_refs[1:]:
                group_by_id[ref]["_merged_into"] = primary_id
                primary.setdefault("_merged_from", []).append(ref)
            for field in ("summary", "group_type"):
                if changes.get(field):
                    primary[field] = changes[field]
            for ref in known_refs[1:]:
                del group_by_id[ref]

    return list(group_by_id.values()), cross_group_edges


def run_unit_logical_grouping_pass(
    *,
    unit_id: str,
    unit_text: str,
    source: dict[str, Any],
    segments: list[ResolvedOverviewSegment],
    source_blocks: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    unresolved_items: list[dict[str, Any]],
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool = True,
    implicit_refs: dict[str, dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    language_policy: dict[str, str] | None = None,
) -> ReadingPassRecord:
    """Run the unit-level logical grouping pass (v0.3).

    Concepts have already been resolved by a prior cross-unit pass.
    This pass only builds logical groups — it does not emit concept deltas.
    """
    prompt = build_unit_logical_grouping_v0_2_composition()
    payload = build_unit_logical_grouping_payload_v0_2(
        unit_id=unit_id,
        unit_text=unit_text,
        source=source,
        segments=[
            {
                "segment_id": s.segment_id,
                "title": s.title,
                "summary": s.summary,
            }
            for s in segments
        ],
        concepts=concepts,
        atomic_items=atomic_items,
        unresolved_items=unresolved_items,
        implicit_refs=implicit_refs,
        context=context,
        language_policy=language_policy,
    )

    cache_key = build_pass_cache_key(
        pass_name="unit-logical-grouping-v0.3",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
        data = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        # v0.2: concepts are already resolved — no concept delta processing.
        # The validation_subject_builder preserves concepts/items as-is and
        # only extracts logical_groups from the LLM response.
        def _build_grouping_subject(llm_data: dict[str, Any]) -> dict[str, Any]:
            _normalize_uncertainty_fields(llm_data)

            groups = llm_data.get("logical_groups", [])
            llm_unresolved = llm_data.get("unresolved_items", [])

            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": unit_id,
                "source": {**source, "unit_text": unit_text},
                "source_blocks": source_blocks,
                "concepts": concepts,
                "atomic_items": atomic_items,
                "logical_groups": groups,
                "unresolved_items": llm_unresolved,
                "validation": {},
                "context_metadata": {},
            }

        from .repair import run_agentic_pass

        data, conversation, validation_report = run_agentic_pass(
            backend=backend,
            prompt=prompt,
            payload=payload,
            validation_subject_builder=_build_grouping_subject,
            pass_name="unit-logical-grouping-v0.3",
            return_subject=True,
        )
        raw_response = _last_assistant_content(conversation)

    # When return_subject=True, data IS the final validation subject.
    # v0.2: concepts/items are preserved as-is — no delta application needed.
    if not cache_hit:
        updated_concepts = data.get("concepts", [])
        updated_items = data.get("atomic_items", [])
        updated_groups = data.get("logical_groups", [])
        all_unresolved = data.get("unresolved_items", [])
    else:
        _normalize_uncertainty_fields(data)
        updated_concepts = concepts
        updated_items = atomic_items
        updated_groups = data.get("logical_groups", [])
        all_unresolved = data.get("unresolved_items", [])

        validation_subject = {
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": unit_id,
            "source": {**source, "unit_text": unit_text},
            "source_blocks": source_blocks,
            "concepts": updated_concepts,
            "atomic_items": updated_items,
            "logical_groups": updated_groups,
            "unresolved_items": all_unresolved,
            "validation": {},
            "context_metadata": {},
        }
        validation_report = validate_extraction_unit_package(validation_subject)
        _raise_on_validation_errors("unit-logical-grouping-v0.3", validation_report)

    # ── Compute factual grouping counts ──
    grouping_counts = _compute_grouping_counts(updated_groups, updated_items)

    record = ReadingPassRecord(
        pass_name="unit-logical-grouping-v0.3",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data={
            **data,
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": unit_id,
            "source": source,
            "source_blocks": source_blocks,
            "concepts": updated_concepts,
            "atomic_items": updated_items,
            "logical_groups": updated_groups,
            "unresolved_items": all_unresolved,
            "validation": validation_report.to_dict(),
            "context_metadata": {"context_injection": bool(context)},
            "metrics": {"counts": {"grouping": grouping_counts}},
        },
        validation_report=validation_report,
        artifact_paths=paths,
        conversation=locals().get("conversation"),
    )

    if use_cache:
        _write_reading_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=record.data,
            validation_report=validation_report,
            record=record,
        )

    return record


# ── Agentic resolution pass (multi-round with tool calling) ────────────────


def _summarize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"tool": result.get("tool", "")}
    if result.get("error"):
        summary["error"] = result.get("error")
        return summary
    payload = result.get("result")
    if isinstance(payload, list):
        summary["result_count"] = len(payload)
        ids: list[str] = []
        scores: list[Any] = []
        methods: list[str] = []
        for item in payload[:10]:
            if isinstance(item, dict):
                ids.append(str(item.get("concept_id") or item.get("group_id") or item.get("block_id") or ""))
                if "_match_score" in item:
                    scores.append(item.get("_match_score"))
                if item.get("_match_method"):
                    methods.append(str(item.get("_match_method")))
        summary["result_ids"] = [item_id for item_id in ids if item_id]
        if scores:
            summary["top_scores"] = scores[:5]
        if methods:
            summary["methods"] = sorted(set(methods))
    elif isinstance(payload, dict):
        summary["result_id"] = str(payload.get("concept_id") or payload.get("group_id") or payload.get("block_id") or "")
        summary["result_keys"] = sorted(str(k) for k in payload.keys())[:12]
    elif isinstance(payload, str):
        summary["result_chars"] = len(payload)
    elif payload is None:
        summary["result"] = None
    else:
        summary["result_type"] = type(payload).__name__
    return summary


def _summarize_agentic_response(data: dict[str, Any], proposal_key: str) -> dict[str, Any]:
    proposals = data.get(proposal_key, [])
    tool_calls = data.get("tool_calls", [])
    return {
        "status": data.get("status", ""),
        "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "proposal_count": len(proposals) if isinstance(proposals, list) else 0,
        "warning_count": len(data.get("warnings", [])) if isinstance(data.get("warnings", []), list) else 0,
        "keys": sorted(str(k) for k in data.keys()),
    }


def _resolution_proposal_key(payload: dict[str, Any]) -> str:
    task = payload.get("task", "")
    if task == "cross_unit_group_resolution":
        return "group_resolution_proposals"
    return "resolution_proposals"


def _is_complete_resolution_response(
    data: dict[str, Any],
    proposal_key: str,
) -> tuple[bool, str]:
    if data.get("tool_calls"):
        return False, "response still contains tool_calls"
    if data.get("status") != "complete":
        return False, "response missing status=complete"
    if proposal_key not in data:
        return False, f"response missing {proposal_key}"
    if not isinstance(data.get(proposal_key), list):
        return False, f"{proposal_key} must be a list"
    return True, ""


def _run_resolution_fallback(
    *,
    backend: LLMBackend,
    fallback_prompt: Any,
    payload: dict[str, Any],
    validation_subject_builder: Any,
    pass_name: str,
    failure_reason: str,
    agentic_trace: dict[str, Any] | None = None,
    pre_fallback_conversation: Any | None = None,
) -> AgenticResolutionResult:
    from .repair import run_agentic_pass

    raw_data, conversation, report = run_agentic_pass(
        backend=backend,
        prompt=fallback_prompt,
        payload=payload,
        validation_subject_builder=validation_subject_builder,
        max_repair_turns=3,
        pass_name=f"{pass_name}-fallback",
        return_subject=False,
    )
    applied_subject = validation_subject_builder(raw_data)
    return AgenticResolutionResult(
        raw_data=raw_data,
        applied_subject=applied_subject,
        conversation=conversation,
        validation_report=report,
        turns_used=1,
        fallback_used=True,
        failure_reason=failure_reason,
        agentic_trace=agentic_trace,
        pre_fallback_conversation=pre_fallback_conversation,
    )


def run_agentic_resolution_pass(
    *,
    backend: LLMBackend,
    prompt: Any,
    payload: dict[str, Any],
    tool_context: dict[str, Any],
    validation_subject_builder: Any,
    max_turns: int = 10,
    pass_name: str = "",
    fallback_prompt: Any | None = None,
) -> AgenticResolutionResult:
    """Run a registry resolution pass with multi-turn tool-calling support."""
    from .registry_tools import execute_tool_call

    proposal_key = _resolution_proposal_key(payload)
    trace: dict[str, Any] = {
        "pass_name": pass_name,
        "proposal_key": proposal_key,
        "max_turns": max_turns,
        "turns": [],
        "fallback_used": False,
        "failure_reason": "",
    }

    conversation = backend.start_conversation(
        system_prompt=prompt.content,
        user_payload=payload,
        pass_name=pass_name,
    )
    assistant_response = _last_assistant_content(conversation)
    data = parse_json_response(assistant_response)
    trace["turns"].append({
        "turn_index": 1,
        "assistant": _summarize_agentic_response(data, proposal_key),
    })

    tool_calls = data.get("tool_calls", [])
    turn_count = 1
    while tool_calls and turn_count < max_turns:
        tool_results: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []
        print(
            f"  {pass_name}: agentic turn {turn_count} requested {len(tool_calls)} tool call(s)",
            file=sys.stderr,
        )
        for tc in tool_calls:
            action = tc.get("action", "") if isinstance(tc, dict) else ""
            args = tc.get("args", {}) if isinstance(tc, dict) else {}
            t_tool = time.monotonic()
            result = execute_tool_call(tc, tool_context)
            elapsed_ms = _elapsed_ms(t_tool)
            tool_results.append(result)
            summary = _summarize_tool_result(result)
            summary.update({"action": action, "args": args, "elapsed_ms": elapsed_ms})
            tool_trace.append(summary)
            if summary.get("error"):
                detail = f"error={summary['error']}"
            elif "result_count" in summary:
                detail = f"{summary['result_count']} results {summary.get('result_ids', [])[:5]}"
            elif summary.get("result_id"):
                detail = f"result={summary['result_id']}"
            else:
                detail = f"result_keys={summary.get('result_keys', [])}"
            print(
                f"    tool {action} {args} -> {detail} ({elapsed_ms}ms)",
                file=sys.stderr,
            )
        trace["turns"][-1]["tool_calls"] = tool_trace

        tool_msg = json.dumps(
            {
                "tool_results": tool_results,
                "remaining_turns": max_turns - turn_count - 1,
            },
            ensure_ascii=False,
        )

        conversation = backend.continue_conversation(conversation, tool_msg)
        assistant_response = _last_assistant_content(conversation)

        try:
            data = parse_json_response(assistant_response)
            trace["turns"].append({
                "turn_index": turn_count + 1,
                "assistant": _summarize_agentic_response(data, proposal_key),
            })
        except Exception as exc:
            reason = f"turn {turn_count + 1} response unparseable: {exc}"
            print(f"  {pass_name}: {reason}", file=sys.stderr)
            if fallback_prompt is not None:
                return _run_resolution_fallback(
                    backend=backend,
                    fallback_prompt=fallback_prompt,
                    payload=payload,
                    validation_subject_builder=validation_subject_builder,
                    pass_name=pass_name,
                    failure_reason=reason,
                    agentic_trace={**trace, "fallback_used": True, "failure_reason": reason},
                    pre_fallback_conversation=conversation,
                )
            applied_subject = validation_subject_builder({})
            report = validate_extraction_unit_package(applied_subject)
            return AgenticResolutionResult(
                raw_data={},
                applied_subject=applied_subject,
                conversation=conversation,
                validation_report=report,
                turns_used=turn_count + 1,
                failure_reason=reason,
                agentic_trace={**trace, "failure_reason": reason},
            )

        tool_calls = data.get("tool_calls", [])
        turn_count += 1

    complete, reason = _is_complete_resolution_response(data, proposal_key)
    if not complete:
        exhausted = bool(data.get("tool_calls")) and turn_count >= max_turns
        if exhausted:
            reason = f"max turns exhausted with pending tool_calls ({max_turns})"
        print(
            f"  {pass_name}: agentic resolution incomplete ({reason}), falling back",
            file=sys.stderr,
        )
        if fallback_prompt is not None:
            result = _run_resolution_fallback(
                backend=backend,
                fallback_prompt=fallback_prompt,
                payload=payload,
                validation_subject_builder=validation_subject_builder,
                pass_name=pass_name,
                failure_reason=reason,
                agentic_trace={**trace, "fallback_used": True, "failure_reason": reason},
                pre_fallback_conversation=conversation,
            )
            result.exhausted = exhausted
            if result.agentic_trace is not None:
                result.agentic_trace["fallback_used"] = True
                result.agentic_trace["failure_reason"] = reason
                result.agentic_trace["exhausted"] = exhausted
            return result
        applied_subject = validation_subject_builder(data)
        report = validate_extraction_unit_package(applied_subject)
        return AgenticResolutionResult(
            raw_data=data,
            applied_subject=applied_subject,
            conversation=conversation,
            validation_report=report,
            turns_used=turn_count,
            exhausted=exhausted,
            failure_reason=reason,
            agentic_trace={**trace, "failure_reason": reason},
        )

    applied_subject = validation_subject_builder(data)
    report = validate_extraction_unit_package(applied_subject)
    if report.passed:
        return AgenticResolutionResult(
            raw_data=data,
            applied_subject=applied_subject,
            conversation=conversation,
            validation_report=report,
            turns_used=turn_count,
            agentic_trace={**trace, "turns_used": turn_count},
        )

    reason = "agentic final response failed validation"
    print(f"  {pass_name}: {reason}, falling back", file=sys.stderr)
    if fallback_prompt is not None:
        return _run_resolution_fallback(
            backend=backend,
            fallback_prompt=fallback_prompt,
            payload=payload,
            validation_subject_builder=validation_subject_builder,
            pass_name=pass_name,
            failure_reason=reason,
            agentic_trace={**trace, "fallback_used": True, "failure_reason": reason},
            pre_fallback_conversation=conversation,
        )
    return AgenticResolutionResult(
        raw_data=data,
        applied_subject=applied_subject,
        conversation=conversation,
        validation_report=report,
        turns_used=turn_count,
        failure_reason=reason,
        agentic_trace={**trace, "failure_reason": reason},
    )


# ── Pass: cross-unit concept resolution ────────────────────────────────────


def run_cross_unit_concept_resolution_pass(
    *,
    unit_id: str,
    concepts: list[dict[str, Any]],
    registry_index: list[dict[str, Any]],
    unresolved_items: list[dict[str, Any]],
    backend: LLMBackend,
    cache_dir: Path,
    use_cache: bool = True,
    source_blocks: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    registry: Any | None = None,  # BookRegistry for agentic tool execution
    selection_trace: dict[str, Any] | None = None,
    candidate_map: list[dict[str, Any]] | None = None,
    language_policy: dict[str, str] | None = None,
) -> ReadingPassRecord:
    """Run cross-unit concept identity resolution (Conversation D).

    LLM reviews unit concepts against the registry index and emits
    resolution proposals: link, merge, split, refine, reclassify, new_concept.

    When *registry* is provided, uses the v0.2 agentic prompt with multi-round
    tool calling. When None, uses the v0.1 single-pass prompt.
    """
    agentic = registry is not None
    if agentic:
        from .reading_prompts import build_concept_resolution_v0_2_composition

        prompt = build_concept_resolution_v0_2_composition()
    else:
        prompt = build_concept_resolution_composition()
    payload = build_concept_resolution_payload(
        unit_id=unit_id,
        concepts=concepts,
        registry_index=registry_index,
        candidate_map=candidate_map,
        unresolved_items=unresolved_items,
        context=context,
        language_policy=language_policy,
    )
    blocks = source_blocks or []

    cache_key = build_pass_cache_key(
        pass_name="cross-unit-concept-resolution",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
        data = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        def _build_subject(llm_data: dict[str, Any]) -> dict[str, Any]:
            _normalize_uncertainty_fields(llm_data)
            proposals = llm_data.get("resolution_proposals", [])
            uc, concept_remap, implicit_refs = _apply_concept_resolution(
                concepts, proposals, unit_id=unit_id,
            )
            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": unit_id,
                "source": {},
                "source_blocks": blocks,
                "concepts": uc,
                "atomic_items": [],
                "logical_groups": [],
                "unresolved_items": llm_data.get("unresolved_items", []),
                "validation": {},
                "context_metadata": {},
            }

        from .repair import run_agentic_pass

        agentic_status = "not_used"
        agentic_turns_used = 0
        agentic_failure_reason = ""
        if agentic:
            tool_context = {
                "registry": registry,
                "source_blocks": blocks,
                "book_summary": context.get("digest", "") if context else "",
            }
            result = run_agentic_resolution_pass(
                backend=backend,
                prompt=prompt,
                payload=payload,
                tool_context=tool_context,
                validation_subject_builder=_build_subject,
                max_turns=10,
                pass_name="cross-unit-concept-resolution",
                fallback_prompt=build_concept_resolution_composition(),
            )
            data = result.raw_data
            applied_subject = result.applied_subject
            conversation = result.conversation
            validation_report = result.validation_report
            agentic_status = "fallback" if result.fallback_used else "complete"
            agentic_turns_used = result.turns_used
            agentic_failure_reason = result.failure_reason
            agentic_trace = result.agentic_trace or {}
            pre_fallback_conversation = result.pre_fallback_conversation
        else:
            data, conversation, validation_report = run_agentic_pass(
                backend=backend,
                prompt=prompt,
                payload=payload,
                validation_subject_builder=_build_subject,
                pass_name="cross-unit-concept-resolution",
                return_subject=False,
            )
            applied_subject = _build_subject(data)
        raw_response = _last_assistant_content(conversation)

    if not cache_hit:
        updated_concepts = applied_subject.get("concepts", [])
        all_unresolved = applied_subject.get("unresolved_items", [])
    else:
        _normalize_uncertainty_fields(data)
        proposals = data.get("resolution_proposals", [])
        updated_concepts, concept_remap, implicit_refs = _apply_concept_resolution(
            concepts, proposals, unit_id=unit_id,
        )
        all_unresolved = data.get("unresolved_items", [])

    # Build implicit_ref_map from link proposals (needed for both cache paths)
    raw_proposals = data.get("resolution_proposals", [])
    implicit_ref_map: dict[str, dict[str, Any]] = {}
    for prop in raw_proposals:
        if prop.get("proposal_type") == "link" and prop.get("registry_ref"):
            refs = prop.get("implicit_refs", [])
            if refs:
                implicit_ref_map[prop["registry_ref"]] = {
                    "unit_concept_ref": (prop.get("target_refs") or [""])[0],
                    "implicit_refs": refs,
                }

    record = ReadingPassRecord(
        pass_name="cross-unit-concept-resolution",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data={
            **data,
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": unit_id,
            "concepts": updated_concepts,
            "resolution_proposals": raw_proposals,
            "implicit_refs": implicit_ref_map,
            "unresolved_items": all_unresolved,
            "validation": validation_report.to_dict() if not cache_hit else {},
            "agentic_status": locals().get("agentic_status", "cache_hit" if cache_hit else "not_used"),
            "agentic_turns_used": locals().get("agentic_turns_used", 0),
            "agentic_failure_reason": locals().get("agentic_failure_reason", ""),
            "agentic_trace": locals().get("agentic_trace", data.get("agentic_trace", {})),
            "selection_trace": selection_trace or data.get("selection_trace", {}),
        },
        validation_report=validation_report if not cache_hit else ReadingValidationReport(True, 0, 0, 0, []),
        artifact_paths=paths,
        conversation=locals().get("conversation"),
        pre_fallback_conversation=locals().get("pre_fallback_conversation"),
    )

    if use_cache:
        _write_reading_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=record.data,
            validation_report=record.validation_report,
            record=record,
        )

    return record


# ── Pass: cross-unit group resolution ──────────────────────────────────────


def run_cross_unit_group_resolution_pass(
    *,
    unit_id: str,
    concepts: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    registry_groups: list[dict[str, Any]],
    backend: LLMBackend,
    atomic_items: list[dict[str, Any]] | None = None,
    cache_dir: Path,
    use_cache: bool = True,
    source_blocks: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    registry: Any | None = None,  # BookRegistry for agentic tool execution
    selection_trace: dict[str, Any] | None = None,
    language_policy: dict[str, str] | None = None,
) -> ReadingPassRecord:
    """Run cross-unit group resolution (Conversation E).

    LLM reviews unit groups against candidate registry groups and emits
    group resolution proposals: continue, mutate, new_thread, cross_group_edge,
    merge_groups.

    When *registry* is provided, uses the v0.2 agentic prompt with multi-round
    tool calling. When None, uses the v0.1 single-pass prompt.
    """
    agentic = registry is not None
    if agentic:
        from .reading_prompts import build_group_resolution_v0_2_composition

        prompt = build_group_resolution_v0_2_composition()
    else:
        prompt = build_group_resolution_composition()
    payload = build_group_resolution_payload(
        unit_id=unit_id,
        concepts=concepts,
        groups=groups,
        registry_groups=registry_groups,
        context=context,
        language_policy=language_policy,
    )
    blocks = source_blocks or []
    items = atomic_items or []

    cache_key = build_pass_cache_key(
        pass_name="cross-unit-group-resolution",
        prompt=prompt,
        user_payload=payload,
        model_identity=backend.model_identity,
    )
    pass_dir = cache_dir / cache_key
    paths = pass_artifact_paths(pass_dir)
    result_path = Path(paths["result"])
    cache_hit = use_cache and result_path.exists()

    if cache_hit:
        raw_response = Path(paths["raw_response"]).read_text(encoding="utf-8")
        data = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        def _build_subject(llm_data: dict[str, Any]) -> dict[str, Any]:
            _normalize_uncertainty_fields(llm_data)
            proposals = llm_data.get("group_resolution_proposals", [])
            ug, cross_edges = _apply_group_resolution(
                groups, proposals, unit_id=unit_id,
            )
            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": unit_id,
                "source": {},
                "source_blocks": blocks,
                "concepts": concepts,
                "atomic_items": items,
                "logical_groups": ug,
                "unresolved_items": [],
                "validation": {},
                "context_metadata": {},
            }

        from .repair import run_agentic_pass

        agentic_status = "not_used"
        agentic_turns_used = 0
        agentic_failure_reason = ""
        if agentic:
            tool_context = {
                "registry": registry,
                "source_blocks": blocks,
                "book_summary": context.get("digest", "") if context else "",
            }
            result = run_agentic_resolution_pass(
                backend=backend,
                prompt=prompt,
                payload=payload,
                tool_context=tool_context,
                validation_subject_builder=_build_subject,
                max_turns=10,
                pass_name="cross-unit-group-resolution",
                fallback_prompt=build_group_resolution_composition(),
            )
            data = result.raw_data
            applied_subject = result.applied_subject
            conversation = result.conversation
            validation_report = result.validation_report
            agentic_status = "fallback" if result.fallback_used else "complete"
            agentic_turns_used = result.turns_used
            agentic_failure_reason = result.failure_reason
            agentic_trace = result.agentic_trace or {}
            pre_fallback_conversation = result.pre_fallback_conversation
        else:
            data, conversation, validation_report = run_agentic_pass(
                backend=backend,
                prompt=prompt,
                payload=payload,
                validation_subject_builder=_build_subject,
                pass_name="cross-unit-group-resolution",
                return_subject=False,
            )
            applied_subject = _build_subject(data)
        raw_response = _last_assistant_content(conversation)

    if not cache_hit:
        updated_groups = applied_subject.get("logical_groups", [])
    else:
        _normalize_uncertainty_fields(data)
        proposals = data.get("group_resolution_proposals", [])
        updated_groups, cross_group_edges = _apply_group_resolution(
            groups, proposals, unit_id=unit_id,
        )

    raw_proposals = data.get("group_resolution_proposals", [])
    _, cross_group_edges = _apply_group_resolution(
        groups if cache_hit else groups, raw_proposals, unit_id=unit_id,
    )

    record = ReadingPassRecord(
        pass_name="cross-unit-group-resolution",
        cache_key=cache_key,
        cache_dir=str(pass_dir),
        cache_hit=cache_hit,
        raw_response=raw_response,
        data={
            **data,
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": unit_id,
            "concepts": concepts,
            "atomic_items": items,
            "logical_groups": updated_groups,
            "group_resolution_proposals": raw_proposals,
            "cross_group_edges": cross_group_edges,
            "warnings": data.get("warnings", []),
            "agentic_status": locals().get("agentic_status", "cache_hit" if cache_hit else "not_used"),
            "agentic_turns_used": locals().get("agentic_turns_used", 0),
            "agentic_failure_reason": locals().get("agentic_failure_reason", ""),
            "agentic_trace": locals().get("agentic_trace", data.get("agentic_trace", {})),
            "selection_trace": selection_trace or data.get("selection_trace", {}),
        },
        validation_report=validation_report if not cache_hit else ReadingValidationReport(True, 0, 0, 0, []),
        artifact_paths=paths,
        conversation=locals().get("conversation"),
        pre_fallback_conversation=locals().get("pre_fallback_conversation"),
    )

    if use_cache:
        _write_reading_pass_artifacts(
            pass_dir=pass_dir,
            paths=paths,
            prompt=prompt,
            user_payload=payload,
            raw_response=raw_response,
            data=record.data,
            validation_report=record.validation_report,
            record=record,
        )

    return record


# ── Mock responses for new passes ──────────────────────────────────────────


def mock_concept_resolution_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    concepts = user_payload.get("concepts", [])
    registry_index = user_payload.get("registry_index", [])

    proposals: list[dict[str, Any]] = []
    if registry_index:
        # Link each unit concept to first matching-type registry concept
        for i, uc in enumerate(concepts):
            uc_type = uc.get("concept_type", "other")
            matched = next(
                (r for r in registry_index if r.get("concept_type") == uc_type),
                None,
            )
            if matched:
                proposals.append({
                    "proposal_id": f"res-{i + 1:04d}",
                    "proposal_type": "link",
                    "target_refs": [uc["concept_id"]],
                    "registry_ref": matched["concept_id"],
                    "changes": {},
                    "rationale": "Mock: matching type in registry index.",
                    "implicit_refs": [],
                    "uncertainty": [],
                    "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
                })
            else:
                proposals.append({
                    "proposal_id": f"res-{i + 1:04d}",
                    "proposal_type": "new_concept",
                    "target_refs": [uc["concept_id"]],
                    "registry_ref": "",
                    "changes": {},
                    "rationale": "Mock: no matching type in registry.",
                    "implicit_refs": [],
                    "uncertainty": [],
                    "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
                })
    else:
        # Empty registry — all new concepts
        for i, uc in enumerate(concepts):
            proposals.append({
                "proposal_id": f"res-{i + 1:04d}",
                "proposal_type": "new_concept",
                "target_refs": [uc["concept_id"]],
                "registry_ref": "",
                "changes": {},
                "rationale": "Mock: first unit, no registry.",
                "implicit_refs": [],
                "uncertainty": [],
                "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            })

    return {
        "unit_id": unit_id,
        "resolution_proposals": proposals,
        "unresolved_items": [],
        "warnings": ["mock concept resolution: placeholder"],
    }


def mock_group_resolution_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    groups = user_payload.get("groups", [])
    registry_groups = user_payload.get("registry_groups", [])

    proposals: list[dict[str, Any]] = []
    if registry_groups:
        # Continue each unit group as continuation of first matching registry group
        for i, g in enumerate(groups):
            g_type = g.get("group_type", "other")
            matched = next(
                (r for r in registry_groups if r.get("group_type") == g_type),
                None,
            )
            if matched:
                proposals.append({
                    "proposal_id": f"grp-res-{i + 1:04d}",
                    "proposal_type": "continue",
                    "unit_group_ref": g["group_id"],
                    "registry_group_ref": matched["group_id"],
                    "changes": {},
                    "rationale": "Mock: matching group type in registry.",
                    "uncertainty": [],
                    "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
                })
            else:
                proposals.append({
                    "proposal_id": f"grp-res-{i + 1:04d}",
                    "proposal_type": "new_thread",
                    "unit_group_ref": g["group_id"],
                    "registry_group_ref": "",
                    "changes": {},
                    "rationale": "Mock: no matching group type in registry.",
                    "uncertainty": [],
                    "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
                })
    else:
        for i, g in enumerate(groups):
            proposals.append({
                "proposal_id": f"grp-res-{i + 1:04d}",
                "proposal_type": "new_thread",
                "unit_group_ref": g["group_id"],
                "registry_group_ref": "",
                "changes": {},
                "rationale": "Mock: first unit, no registry groups.",
                "uncertainty": [],
                "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            })

    return {
        "unit_id": unit_id,
        "group_resolution_proposals": proposals,
        "warnings": ["mock group resolution: placeholder"],
    }


# ── Orchestrator ─────────────────────────────────────────────────────────────


def run_reading_pipeline(
    book_path: str | Path,
    unit_id: str,
    *,
    backend: LLMBackend | None = None,
    cache_dir: str | Path = ".tilusion_cache",
    use_cache: bool = True,
    context: dict[str, Any] | None = None,
    scope: str = "unit",
    source_language: str = "auto",
    reader_language: str = "zh-Hans",
    normalized_language: str = "normalized",
) -> ReadingPipelineRecord:
    """Run the full reading pipeline (Phase 3): overview → per-segment → concept
    resolution → logical grouping → group resolution.

    When *scope* is ``"book"``, loads the BookRegistry before extraction,
    builds a book context digest from prior registry state (if any), injects
    it as extraction context, runs cross-unit concept and group resolution,
    and computes/applies a registry delta after extraction.
    The default ``"unit"`` scope is unchanged (no registry, no cross-unit passes).
    """
    if scope not in ("unit", "book"):
        raise ValueError(f"scope must be 'unit' or 'book', got {scope!r}")

    from .book_reader import build_book_index, extract_unit_text

    total_start = time.monotonic()
    llm = backend or MockReadingBackend()
    overview_backend = backend or MockExtractionBackend()
    pass_summaries: dict[str, dict[str, Any]] = {}
    TOTAL_STEPS = 5

    book_path = Path(book_path).resolve()
    cache_base = Path(cache_dir)
    book_cache_root = book_root(cache_base, book_path)
    index = build_book_index(book_path)
    unit = index.unit_map()[unit_id]
    text = extract_unit_text(book_path, unit)
    language_policy = build_language_policy(
        source_language=source_language,
        reader_language=reader_language,
        normalized_language=normalized_language,
    )
    source = {
        "book_path": str(book_path),
        "book_title": index.title or "",
        "unit_id": unit_id,
        "unit_label": unit.label,
        "unit_kind": unit.kind,
    }

    # Build/load the deterministic book source index before LLM extraction.
    source_index_path = source_index_cache_path(book_path, cache_base)
    if source_index_path.exists():
        book_source_index = load_book_source_index(source_index_path)
        source_index_status = "loaded"
    else:
        book_source_index = build_book_source_index(book_path)
        source_index_path = save_book_source_index(book_source_index, book_path, cache_root=cache_base)
        source_index_status = "built"
    source_index_id = str(book_source_index.get("source_index_id", ""))
    print(
        f"  [source-index] {source_index_status}: {source_index_id} "
        f"({book_source_index.get('metrics', {}).get('block_count', 0)} blocks)",
        file=sys.stderr,
    )

    # ── Scope "book" pre-extraction: load registry, build digest ──
    registry: BookRegistry | None = None
    registry_digest_dir: Path | None = None
    registry_head_commit = ""
    digest = ""
    if scope == "book":
        registry = BookRegistry.load_or_init(book_path, cache_root=cache_base)
        registry.ensure_source_index_id(source_index_id)
        init_embedding_cache(registry.embedding_cache_dir)
        registry_digest_dir = registry.cache_dir
        registry_head_commit = registry.head_commit_hash()
        digest = _load_cached_digest(registry_digest_dir)
        digest_source = "cached" if digest else ""
        if not digest and registry.has_concepts():
            digest = build_book_digest(
                llm,
                registry,
                unit_id,
                cache_dir=book_cache_root / "book_digest",
                use_cache=use_cache,
            )
            if digest:
                _save_cached_digest(registry_digest_dir, digest)
                digest_source = "generated"
        context = make_context_dict(digest)
        if digest:
            preview = digest.replace("\n", " ")[:300]
            if len(digest) > 300:
                preview += "..."
            print(
                f"  [book] digest {digest_source}: {len(digest)} chars — "
                f"{preview}",
                file=sys.stderr,
            )
        elif registry.has_concepts():
            print(
                f"  [book] registry loaded: {len(registry._concepts)} concepts, "
                f"{len(registry._items)} items, {len(registry._groups)} groups; "
                "no digest context",
                file=sys.stderr,
            )
        else:
            print("  [book] first unit — no prior context", file=sys.stderr)

    model_config = model_config_for_cache(llm)
    context_identity = {
        "registry_commit": registry_head_commit,
        "book_digest_hash": f"digest-{sha256_text(digest)[:16]}" if digest else "",
        "context_pack_hash": "",
        "language_policy": language_policy,
    }
    unit_prompt_versions = {
        "overview": build_overview_composition().composition_id,
        "per_segment": build_per_segment_extraction_composition().composition_id,
        "logical_grouping": build_unit_logical_grouping_v0_2_composition().composition_id,
    }
    unit_run_hash = compute_unit_run_hash(
        source_index_id=source_index_id,
        unit_id=unit_id,
        scope=scope,
        model_identity=llm.model_identity,
        model_config=model_config,
        context_identity=context_identity,
        prompt_versions=unit_prompt_versions,
    )
    unit_cache_root = unit_run_dir(cache_base, book_path, unit_id, unit_run_hash)
    cross_unit_run_hash = ""
    cross_unit_cache_root: Path | None = None
    if scope == "book":
        cross_prompt_versions = {
            "concept_resolution": build_concept_resolution_v0_2_composition().composition_id,
            "group_resolution": build_group_resolution_v0_2_composition().composition_id,
        }
        cross_unit_run_hash = compute_cross_unit_run_hash(
            source_index_id=source_index_id,
            triggering_run_hash=unit_run_hash,
            triggering_unit_id=unit_id,
            registry_state_hash=registry_head_commit,
            model_identity=llm.model_identity,
            model_config=model_config,
            prompt_versions=cross_prompt_versions,
        )
        cross_unit_cache_root = cross_unit_run_dir(cache_base, book_path, cross_unit_run_hash)

    # ── Step 1: Overview segmentation ──
    step = 1
    t0 = time.monotonic()
    try:
        overview_record = run_overview_segmentation_pass(
            unit=unit,
            text=text,
            backend=overview_backend,
            cache_dir=unit_cache_root / "overview",
            use_cache=use_cache,
            context=context,
            language_policy=language_policy,
        )
        segments, overview_repairs = resolve_overview_segments(overview_record.data, text)
        pass_summaries["overview_segmentation"] = {
            "cache_key": overview_record.cache_key,
            "cache_dir": overview_record.cache_dir,
            "cache_hit": overview_record.cache_hit,
            "artifact_paths": overview_record.artifact_paths,
            "elapsed_ms": _elapsed_ms(t0),
            "resolved_segment_count": len(segments),
            "repair_hint_count": len(overview_repairs),
        }
        _log_progress(step, TOTAL_STEPS, "Overview segmentation", f"{len(segments)} segments OK", _elapsed_ms(t0))
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Overview segmentation", "FAILED", _elapsed_ms(t0))
        raise

    # ── Step 2: Per-segment reading extraction (parallel) ──
    step = 2
    t0 = time.monotonic()
    segment_records: list[ReadingPassRecord] = []
    max_workers = min(len(segments), 4)
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_seg: dict[Any, Any] = {}
            for seg in segments:
                seg_context = segment_hint_payload(seg)
                indexed_blocks = [
                    source_index_block_to_source_block(block)
                    for block in blocks_for_unit_range(book_source_index, unit_id, seg.start, seg.end)
                ]
                if indexed_blocks:
                    block_ids = [block.block_id for block in indexed_blocks]
                    print(
                        f"    hint {seg.segment_id}: {len(indexed_blocks)} source-index blocks "
                        f"{block_ids[0]}..{block_ids[-1]}",
                        file=sys.stderr,
                    )
                # Enrich segment context with known registry concepts
                if scope == "book" and registry is not None and registry.has_concepts() and indexed_blocks:
                    seg_block_ids = {b.block_id for b in indexed_blocks}
                    known = known_concepts_for_blocks(
                        registry._concepts, seg_block_ids
                    )
                    if known:
                        seg_context["known_concepts"] = known
                future = executor.submit(
                    run_per_segment_extraction_pass,
                    unit_id=unit_id,
                    segment=seg,
                    backend=llm,
                    cache_dir=unit_cache_root / "per_segment",
                    use_cache=use_cache,
                    context=seg_context,
                    unit_text=text,
                    source_blocks=indexed_blocks or None,
                    source_index_id=source_index_id if indexed_blocks else None,
                    language_policy=language_policy,
                )
                future_to_seg[future] = seg

            for i, future in enumerate(as_completed(future_to_seg)):
                seg = future_to_seg[future]
                seg_record = future.result()
                segment_records.append(seg_record)
                counts = seg_record.data.get("metrics", {}).get("counts", {}).get("per_segment", {})
                seg_ctx = segment_hint_payload(seg)
                hint_str = ""
                if seg.title:
                    hint_str += f" \"{seg.title}\""
                hints = seg_ctx.get("extraction_hints") or []
                if hints:
                    first_hint = hints[0]
                    hint_str += f" {len(hints)} hints: {_log_preview(first_hint, limit=88)}"
                print(
                    f"    [{i + 1}/{len(segments)}] {seg.segment_id}"
                    f"{hint_str}  "
                    f"{counts.get('source_blocks', 0)} blocks  "
                    f"{counts.get('concepts', 0)} concepts  "
                    f"{counts.get('atomic_items', 0)} items",
                    file=sys.stderr,
                )
        pass_summaries["per_segment_extraction"] = {
            "segment_count": len(segments),
            "elapsed_ms": _elapsed_ms(t0),
            "segment_cache_keys": [r.cache_key for r in segment_records],
        }
        seg_agg = _aggregate_per_segment_counts(segment_records)
        _log_progress(step, TOTAL_STEPS, "Per-segment extraction", f"{len(segments)} segments OK", _elapsed_ms(t0))
        print(
            f"    = {seg_agg['total_source_blocks']} blocks, "
            f"{seg_agg['total_concepts']} concepts, "
            f"{seg_agg['total_atomic_items']} items "
            f"({seg_agg['concepts_per_block']} concepts/block, "
            f"{seg_agg['items_per_block']} items/block)",
            file=sys.stderr,
        )
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Per-segment extraction", "FAILED", _elapsed_ms(t0))
        raise

    # ── Segment merge: merge concepts and reindex items ──
    stabilized = merge_segment_extraction_results(
        [r.data for r in segment_records], unit_id=unit_id
    )
    merge_counts = stabilized.get("metrics", {}).get("counts", {}).get("segment_merge", {})
    print(
        f"    merge: {merge_counts.get('concepts_before_merge', '?')} -> "
        f"{merge_counts.get('concepts_after_merge', '?')} concepts, "
        f"{merge_counts.get('unresolved_items', 0)} unresolved",
        file=sys.stderr,
    )

    # ── Aggregate factual stage counts ──
    metrics: dict[str, Any] = {
        "validation": {},
        "counts": {
            "overview": {
                "segment_count": _overview_segment_count(overview_record.data),
                "resolved_segment_count": len(segments),
                "repair_hint_count": len(overview_repairs),
                "unit_char_count": len(text),
            },
            "per_segment": _aggregate_per_segment_counts(segment_records),
            "segment_merge": stabilized.get("metrics", {}).get("counts", {}).get("segment_merge", {}),
        },
    }

    # ── Phase 3: resolve concepts (book scope) before grouping ──
    step = 3
    concept_resolution_record: ReadingPassRecord | None = None
    concept_resolution_proposals: list[dict[str, Any]] = []
    implicit_ref_map: dict[str, dict[str, Any]] = {}
    if scope == "book" and registry is not None:
        t0 = time.monotonic()
        try:
            reg_index = build_registry_index(registry)
            concept_selection_trace: dict[str, Any] = {}
            candidate_index = select_concept_candidates(
                stabilized["concepts"], reg_index, trace=concept_selection_trace,
            )
            concept_resolution_record = run_cross_unit_concept_resolution_pass(
                unit_id=unit_id,
                concepts=stabilized["concepts"],
                registry_index=candidate_index,
                unresolved_items=stabilized.get("unresolved_items", []),
                backend=llm,
                cache_dir=(cross_unit_cache_root or unit_cache_root) / "concept_resolution",
                use_cache=use_cache,
                source_blocks=stabilized.get("source_blocks", []),
                context=None,
                registry=registry,
                selection_trace=concept_selection_trace,
                candidate_map=concept_selection_trace.get("candidate_map", []),
                language_policy=language_policy,
            )
            pass_summaries["cross_unit_concept_resolution"] = {
                "cache_key": concept_resolution_record.cache_key,
                "cache_dir": concept_resolution_record.cache_dir,
                "cache_hit": concept_resolution_record.cache_hit,
                "artifact_paths": concept_resolution_record.artifact_paths,
                "elapsed_ms": _elapsed_ms(t0),
            }
            resolved_concepts = concept_resolution_record.data["concepts"]
            implicit_ref_map = concept_resolution_record.data.get("implicit_refs", {})
            concept_resolution_proposals = concept_resolution_record.data.get("resolution_proposals", [])
            n_links = sum(1 for p in concept_resolution_proposals if p.get("proposal_type") == "link")
            n_new = sum(1 for p in concept_resolution_proposals if p.get("proposal_type") == "new_concept")
            _log_progress(step, TOTAL_STEPS, "Concept resolution", f"{n_links} links, {n_new} new", _elapsed_ms(t0))
            print(
                f"    = {n_links} links, {n_new} new, {len(implicit_ref_map)} implicit ref maps",
                file=sys.stderr,
            )
            _log_concept_resolution_preview(
                concept_resolution_proposals,
                stabilized["concepts"],
                registry,
            )
        except Exception:
            _log_progress(step, TOTAL_STEPS, "Concept resolution", "FAILED", _elapsed_ms(t0))
            raise
    else:
        # Unit scope: no cross-unit resolution — skip step
        resolved_concepts = stabilized["concepts"]
        _log_progress(step, TOTAL_STEPS, "Concept resolution", "skipped (unit scope)", 0)

    # ── Step 4: Unit logical grouping (v0.2 — concepts already resolved) ──
    step = 4
    t0 = time.monotonic()
    try:
        grouping_record = run_unit_logical_grouping_pass(
            unit_id=unit_id,
            unit_text=text,
            source=source,
            segments=segments,
            source_blocks=stabilized["source_blocks"],
            concepts=resolved_concepts,
            atomic_items=stabilized["atomic_items"],
            unresolved_items=stabilized.get("unresolved_items", []),
            backend=llm,
            cache_dir=unit_cache_root / "logical_grouping",
            use_cache=use_cache,
            implicit_refs=implicit_ref_map if implicit_ref_map else None,
            context=None,
            language_policy=language_policy,
        )
        pass_summaries["unit_logical_grouping"] = {
            "cache_key": grouping_record.cache_key,
            "cache_dir": grouping_record.cache_dir,
            "cache_hit": grouping_record.cache_hit,
            "artifact_paths": grouping_record.artifact_paths,
            "elapsed_ms": _elapsed_ms(t0),
        }
        gdata = grouping_record.data
        n_groups = len(gdata.get("logical_groups", []))
        n_unresolved = len(gdata.get("unresolved_items", []))
        _log_progress(step, TOTAL_STEPS, "Unit logical grouping", f"{n_groups} groups", _elapsed_ms(t0))
        print(
            f"    = {n_groups} groups, {n_unresolved} unresolved",
            file=sys.stderr,
        )
    except Exception:
        _log_progress(step, TOTAL_STEPS, "Unit logical grouping", "FAILED", _elapsed_ms(t0))
        raise

    metrics["counts"]["grouping"] = grouping_record.data.get("metrics", {}).get("counts", {}).get("grouping", {})

    # ── Phase 3: resolve groups (book scope, skip for unit 1) ──
    step = 5
    group_resolution_record: ReadingPassRecord | None = None
    group_resolution_proposals: list[dict[str, Any]] = []
    cross_group_edges: list[dict[str, Any]] = []
    if scope == "book" and registry is not None and registry.has_concepts():
        t0 = time.monotonic()
        try:
            registry_groups_list = list(registry._groups.values())
            group_selection_trace: dict[str, Any] = {}
            candidate_groups = select_group_candidates(
                grouping_record.data["logical_groups"],
                registry_groups_list,
                resolved_concepts,
                trace=group_selection_trace,
            )
            group_resolution_record = run_cross_unit_group_resolution_pass(
                unit_id=unit_id,
                concepts=resolved_concepts,
                groups=grouping_record.data["logical_groups"],
                atomic_items=grouping_record.data["atomic_items"],
                registry_groups=candidate_groups,
                backend=llm,
                cache_dir=(cross_unit_cache_root or unit_cache_root) / "group_resolution",
                use_cache=use_cache,
                source_blocks=stabilized.get("source_blocks", []),
                context=None,
                registry=registry,
                selection_trace=group_selection_trace,
                language_policy=language_policy,
            )
            pass_summaries["cross_unit_group_resolution"] = {
                "cache_key": group_resolution_record.cache_key,
                "cache_dir": group_resolution_record.cache_dir,
                "cache_hit": group_resolution_record.cache_hit,
                "artifact_paths": group_resolution_record.artifact_paths,
                "elapsed_ms": _elapsed_ms(t0),
            }
            group_resolution_proposals = group_resolution_record.data.get("group_resolution_proposals", [])
            cross_group_edges = group_resolution_record.data.get("cross_group_edges", [])
            n_continues = sum(1 for p in group_resolution_proposals if p.get("proposal_type") == "continue")
            n_new = sum(1 for p in group_resolution_proposals if p.get("proposal_type") == "new_thread")
            _log_progress(step, TOTAL_STEPS, "Group resolution", f"{n_continues} continues, {n_new} new", _elapsed_ms(t0))
            print(
                f"    = {n_continues} continues, {n_new} new, {len(cross_group_edges)} cross-group edges",
                file=sys.stderr,
            )
            _log_group_resolution_preview(
                group_resolution_proposals,
                grouping_record.data["logical_groups"],
                registry,
            )
        except Exception:
            _log_progress(step, TOTAL_STEPS, "Group resolution", "FAILED", _elapsed_ms(t0))
            raise
    else:
        _log_progress(step, TOTAL_STEPS, "Group resolution", "skipped (first unit or unit scope)", 0)

    # ── Refresh segment_merge counts ──
    final_concepts = grouping_record.data["concepts"]
    final_unresolved = grouping_record.data.get("unresolved_items", [])
    segment_merge_before = metrics["counts"]["segment_merge"]["concepts_before_merge"]
    segment_merge = metrics["counts"]["segment_merge"]
    segment_merge["concepts_after_merge"] = len(final_concepts)
    segment_merge["concept_merge_count"] = segment_merge_before - len(final_concepts)
    segment_merge["unresolved_items"] = len(final_unresolved)
    segment_merge["ambiguous_surface_count"] = sum(
        1 for u in final_unresolved if u.get("kind") == "ambiguous_concept_surface"
    )

    final_data = {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": source,
        "source_blocks": grouping_record.data["source_blocks"],
        "concepts": grouping_record.data["concepts"],
        "atomic_items": grouping_record.data["atomic_items"],
        "logical_groups": grouping_record.data["logical_groups"],
        "unresolved_items": grouping_record.data.get("unresolved_items", []),
        "validation": grouping_record.validation_report.to_dict(),
        "context_metadata": {
            "context_injection": bool(context),
            "source_index_id": source_index_id,
            "source_index_path": str(source_index_path),
            "run_hash": unit_run_hash,
            "language_policy": language_policy,
        },
        "metrics": metrics,
    }
    metrics["quality"] = compute_quality_metrics(final_data, reader_language=reader_language)
    final_data["metrics"] = metrics
    log_quality_metrics(metrics["quality"])
    final_validation_report = validate_extraction_unit_package(final_data)
    _raise_on_validation_errors("reading-unit-package", final_validation_report)
    final_validation = final_validation_report.to_dict()
    metrics["validation_counts"] = {
        "error_count": final_validation["error_count"],
        "warning_count": final_validation["warning_count"],
        "issue_count": final_validation["issue_count"],
    }
    final_data["metrics"] = metrics
    final_data["validation"] = final_validation

    # ── Scope "book" post-extraction: compute delta, apply, save ──
    delta_result: RegistryDeltaResult | None = None
    if scope == "book" and registry is not None:
        delta_result = compute_registry_delta(
            final_data, registry, unit_id=unit_id,
            concept_resolution_proposals=concept_resolution_proposals,
            group_resolution_proposals=group_resolution_proposals,
        )
        _log_registry_delta_preview(delta_result, registry)
        applied = apply_registry_delta(registry, delta_result)

        # ── Deterministic registry dedup ──
        dup_pairs = find_registry_duplicates(registry._concepts)
        if dup_pairs:
            print(
                f"  [registry-dedup] found {len(dup_pairs)} duplicate pair(s)",
                file=sys.stderr,
            )
            for id_a, id_b, reason in dup_pairs[:5]:
                print(
                    f"    merge {id_b} -> {id_a} ({reason})",
                    file=sys.stderr,
                )
            if len(dup_pairs) > 5:
                print(
                    f"    ... {len(dup_pairs) - 5} more pair(s)",
                    file=sys.stderr,
                )
            for id_a, id_b, _reason in dup_pairs:
                try:
                    registry.merge_concepts([id_a, id_b])
                except Exception:
                    pass

        # ── Post-extraction digest update ──
        # Keep digest generation on the dedicated digest prompt. Reusing the
        # grouping conversation drifted language/style and polluted the cache.
        digest_for_next = ""
        if registry.has_concepts():
            digest_for_next = build_book_digest(
                llm,
                registry,
                unit_id,
                previous_digest=context.get("digest", "") if context else None,
                cache_dir=book_cache_root / "book_digest",
                use_cache=use_cache,
            ) or ""
        if digest_for_next and registry_digest_dir is not None:
            _save_cached_digest(registry_digest_dir, digest_for_next)
            print(
                f"  [book] digest updated: {len(digest_for_next)} chars",
                file=sys.stderr,
            )

        commit_hash = registry.save(run_hash=cross_unit_run_hash or None)
        print(
            f"  [book] delta: {len(delta_result.operations)} ops "
            f"({delta_result.stats}), "
            f"{len(delta_result.ambiguity_items)} ambiguities — "
            f"saved at {commit_hash}",
            file=sys.stderr,
        )

    # ── Write unit package ──
    package_path = write_reading_unit_package(
        unit_id=unit_id,
        source=source,
        data=final_data,
        validation=final_validation,
        passes=pass_summaries,
        run_hash=unit_run_hash,
        run_dir=unit_cache_root,
    )

    unit_manifest = {
        "run_hash": unit_run_hash,
        "run_type": "unit_extraction",
        "unit_id": unit_id,
        "scope": scope,
        "source_index_id": source_index_id,
        "model_identity": llm.model_identity,
        "model_config": model_config,
        "context_identity": context_identity,
        "prompt_versions": unit_prompt_versions,
        "pass_cache_keys": {
            "overview": pass_summaries.get("overview_segmentation", {}).get("cache_key", ""),
            "per_segment": pass_summaries.get("per_segment_extraction", {}).get("segment_cache_keys", []),
            "logical_grouping": pass_summaries.get("unit_logical_grouping", {}).get("cache_key", ""),
            "cross_unit_concept_resolution": pass_summaries.get("cross_unit_concept_resolution", {}).get("cache_key", ""),
            "cross_unit_group_resolution": pass_summaries.get("cross_unit_group_resolution", {}).get("cache_key", ""),
        },
        "passes": pass_summaries,
        "cross_unit": {
            "run_hash": cross_unit_run_hash,
            "run_dir": str(cross_unit_cache_root) if cross_unit_cache_root is not None else "",
            "concept_resolution": pass_summaries.get("cross_unit_concept_resolution", {}),
            "group_resolution": pass_summaries.get("cross_unit_group_resolution", {}),
        } if scope == "book" else {},
        "unit_package_path": package_path,
        "validation_passed": bool(final_validation.get("passed", False)),
    }
    write_run_manifest(unit_cache_root, unit_manifest)

    total_elapsed = _elapsed_ms(total_start)
    unit_manifest["elapsed_ms"] = total_elapsed
    write_run_manifest(unit_cache_root, unit_manifest)
    if scope == "book":
        unit_catalog_entry = {
            "run_hash": unit_run_hash,
            "run_type": "unit_extraction",
            "unit_id": unit_id,
            "source_index_id": source_index_id,
            "model_identity": llm.model_identity,
            "elapsed_ms": total_elapsed,
            "validation_passed": bool(final_validation.get("passed", False)),
            "triggered_cross_unit": cross_unit_run_hash,
        }
        prepend_to_runs_catalog(cache_base, book_path, unit_catalog_entry)
        if cross_unit_cache_root is not None and cross_unit_run_hash:
            cross_manifest = {
                "run_hash": cross_unit_run_hash,
                "run_type": "cross_unit_resolution",
                "triggered_by": {
                    "run_hash": unit_run_hash,
                    "unit_id": unit_id,
                },
                "source_index_id": source_index_id,
                "registry_state_hash": registry_head_commit,
                "model_identity": llm.model_identity,
                "model_config": model_config,
                "prompt_versions": {
                    "concept_resolution": build_concept_resolution_v0_2_composition().composition_id,
                    "group_resolution": build_group_resolution_v0_2_composition().composition_id,
                },
                "pass_cache_keys": {
                    "concept_resolution": pass_summaries.get("cross_unit_concept_resolution", {}).get("cache_key", ""),
                    "group_resolution": pass_summaries.get("cross_unit_group_resolution", {}).get("cache_key", ""),
                },
                "passes": {
                    "concept_resolution": pass_summaries.get("cross_unit_concept_resolution", {}),
                    "group_resolution": pass_summaries.get("cross_unit_group_resolution", {}),
                },
                "registry_commit": registry.head_commit_hash() if registry is not None else "",
            }
            write_run_manifest(cross_unit_cache_root, cross_manifest)
            prepend_to_runs_catalog(
                cache_base,
                book_path,
                {
                    "run_hash": cross_unit_run_hash,
                    "run_type": "cross_unit_resolution",
                    "triggered_by": {
                        "run_hash": unit_run_hash,
                        "unit_id": unit_id,
                    },
                    "source_index_id": source_index_id,
                    "registry_commit": cross_manifest["registry_commit"],
                },
            )

    print(f"Reading pipeline complete: {package_path} ({total_elapsed}ms)", file=sys.stderr)

    return ReadingPipelineRecord(
        unit_id=unit_id,
        elapsed_ms=total_elapsed,
        unit_package_path=package_path,
        passes=pass_summaries,
        data=final_data,
        validation=final_validation,
    )


# ── Artifact writers ─────────────────────────────────────────────────────────


def _write_reading_pass_artifacts(
    *,
    pass_dir: Path,
    paths: dict[str, str],
    prompt: Any,
    user_payload: dict[str, Any],
    raw_response: str,
    data: dict[str, Any],
    validation_report: ReadingValidationReport,
    record: ReadingPassRecord,
) -> None:
    pass_dir.mkdir(parents=True, exist_ok=True)

    if hasattr(prompt, "to_dict"):
        Path(paths["prompt_composition"]).write_text(
            json.dumps(prompt.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    Path(paths["system_prompt"]).write_text(
        prompt.content if hasattr(prompt, "content") else str(prompt), encoding="utf-8"
    )
    Path(paths["request_payload"]).write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(paths["raw_response"]).write_text(raw_response, encoding="utf-8")
    Path(paths["result"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(paths["validation_report"]).write_text(
        json.dumps(validation_report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(paths["validated_result"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if paths.get("selection_trace") and data.get("selection_trace"):
        Path(paths["selection_trace"]).write_text(
            json.dumps(data.get("selection_trace", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if paths.get("agentic_trace") and data.get("agentic_trace"):
        Path(paths["agentic_trace"]).write_text(
            json.dumps(data.get("agentic_trace", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    Path(paths["manifest"]).write_text(record.to_json(), encoding="utf-8")
    if record.conversation is not None and hasattr(record.conversation, "to_dict"):
        Path(paths["conversation"]).write_text(
            json.dumps(record.conversation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if (
        paths.get("pre_fallback_conversation")
        and record.pre_fallback_conversation is not None
        and hasattr(record.pre_fallback_conversation, "to_dict")
    ):
        Path(paths["pre_fallback_conversation"]).write_text(
            json.dumps(record.pre_fallback_conversation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def write_reading_unit_package(
    *,
    unit_id: str,
    source: dict[str, Any],
    data: dict[str, Any],
    validation: dict[str, Any],
    passes: dict[str, dict[str, Any]],
    run_hash: str,
    run_dir: Path,
) -> str:
    """Write the ExtractionUnitPackage into the unit run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    package_path = run_dir / "unit_package.json"

    package = dict(data)
    package.setdefault("schema_version", READING_UNIT_SCHEMA_VERSION)
    package["unit_id"] = unit_id
    package["source"] = data.get("source") or source
    package["passes"] = passes
    package["validation"] = validation
    package["run_hash"] = run_hash
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")

    return str(package_path)
