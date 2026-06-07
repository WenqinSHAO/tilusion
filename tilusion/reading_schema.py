from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


READING_UNIT_SCHEMA_VERSION = "reading-unit-v0.3"

_CONCEPT_TYPE_NORMALIZATION: dict[str, str] = {
    "thing": "object",
    "substance": "object",
    "format": "object",
    "component": "technical_component",
    "technical_component": "technical_component",
    "work": "source",
    "collection": "source",
    "source_statement": "source",
    "condition": "theme",
    "phenomenon": "theme",
    "event_type": "theme",
    "concept": "theme",
    "role": "social_role",
    "relationship": "social_role",
}


def normalize_concept_type(value: Any) -> str:
    """Normalize known noisy concept type aliases into the coarse schema vocabulary."""
    raw = str(value or "").strip()
    if not raw:
        return "other"
    key = re.sub(r"[\s-]+", "_", raw.lower())
    return _CONCEPT_TYPE_NORMALIZATION.get(key, key)

Grounding = str  # "source_grounded" | "synthesis" | "deterministic" | "llm_inferred" | "user_corrected"

RECOMMENDED_CONCEPT_TYPES = frozenset(
    {
        "person",
        "group",
        "organization",
        "place",
        "object",
        "term",
        "method",
        "theme",
        "motif",
        "time_anchor",
        "emotion",
        "social_role",
        "institution",
        "symbol",
        "scene_element",
        "technical_component",
        "dataset",
        "metric",
        "source",
        "other",
    }
)

RECOMMENDED_ITEM_TYPES = frozenset(
    {
        "event",
        "scene",
        "action",
        "claim",
        "argument",
        "statement",
        "observation",
        "description",
        "method",
        "technique",
        "process",
        "result",
        "limitation",
        "habit",
        "question",
        "unresolved_issue",
        "definition",
        "example",
        "comparison",
        "contrast",
        "background",
        "note",
        "other",
    }
)

RECOMMENDED_GROUP_TYPES = frozenset(
    {
        "timeline",
        "temporal_sequence",
        "theme_set",
        "concept_map",
        "discourse_graph",
        "claim_evidence_map",
        "viewpoint_evolution",
        "open_thread_list",
        "method_example_set",
        "motif_development",
        "contrast_set",
        "other",
    }
)

RECOMMENDED_EDGE_TYPES = frozenset(
    {
        "mentions",
        "refers_to",
        "aliases",
        "same_as_candidate",
        "part_of",
        "elaborates",
        "supports",
        "contradicts",
        "qualifies",
        "contrasts",
        "causes",
        "enables",
        "explains",
        "follows_from",
        "precedes",
        "continues",
        "resolves",
        "raises_question",
        "answers_question",
        "exemplifies",
        "defines",
        "uses_method",
        "produces_result",
        "has_limitation",
        "related_to",
        "other",
    }
)

RECOMMENDED_PROVENANCE_VALUES = frozenset(
    {
        "source_grounded",
        "synthesis",
        "deterministic",
        "llm_inferred",
        "user_corrected",
    }
)

REGISTRY_DELTA_OPERATION_TYPES = frozenset(
    {
        "new_concept",
        "alias_candidate",
        "merge_proposal",
        "summary_update",
        "logical_group_continuation",
        "cross_unit_link",
        "ambiguity_item",
        "user_review_needed",
    }
)


def is_open_type_string(value: Any) -> bool:
    """Return True for schema-light extensible type strings."""
    return isinstance(value, str) and bool(value.strip())


def is_recommended_concept_type(value: str) -> bool:
    return value in RECOMMENDED_CONCEPT_TYPES


def is_recommended_item_type(value: str) -> bool:
    return value in RECOMMENDED_ITEM_TYPES


def is_recommended_group_type(value: str) -> bool:
    return value in RECOMMENDED_GROUP_TYPES


def is_recommended_edge_type(value: str) -> bool:
    return value in RECOMMENDED_EDGE_TYPES


def is_recommended_provenance(value: str) -> bool:
    return value in RECOMMENDED_PROVENANCE_VALUES


# ── Core extraction types ─────────────────────────────────────────────────────


@dataclass(slots=True)
class SourceBlock:
    block_id: str
    unit_id: str
    segment_id: str
    block_index: int
    block_type: str
    start: int
    end: int
    text: str
    text_hash: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Concept:
    concept_id: str
    surface: str
    concept_type: str
    source_block_refs: list[str] = field(default_factory=list)
    canonical_name: str | None = None
    summary: str = ""
    aliases: list[str] = field(default_factory=list)
    observed_surfaces: list[str] = field(default_factory=list)
    facets: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TemporalAttribute:
    kind: str  # "explicit" | "implicit" | "relative" | "none"
    surface: str = ""
    normalized_hint: str = ""
    source_block_ref: str = ""
    uncertainty: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AtomicItem:
    item_id: str
    item_type: str
    summary: str
    source_block_refs: list[str] = field(default_factory=list)
    concept_refs: list[str] = field(default_factory=list)
    temporal_attributes: list[TemporalAttribute] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "summary": self.summary,
            "source_block_refs": self.source_block_refs,
            "concept_refs": self.concept_refs,
            "temporal_attributes": [ta.to_dict() for ta in self.temporal_attributes],
            "attributes": self.attributes,
            "uncertainty": self.uncertainty,
            "provenance": self.provenance,
        }


@dataclass(slots=True)
class GraphNode:
    node_id: str
    item_ref: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GraphEdge:
    source: str  # node_id within the same graph
    target: str  # node_id within the same graph
    edge_type: str
    summary: str = ""
    source_block_refs: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LogicalGroup:
    group_id: str
    group_type: str
    summary: str
    item_refs: list[str] = field(default_factory=list)
    concept_refs: list[str] = field(default_factory=list)
    graph: dict[str, Any] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        graph = self.graph
        result: dict[str, Any] = {
            "group_id": self.group_id,
            "group_type": self.group_type,
            "summary": self.summary,
            "item_refs": self.item_refs,
            "concept_refs": self.concept_refs,
            "graph": {
                "nodes": [n.to_dict() for n in graph.get("nodes", [])],
                "edges": [e.to_dict() for e in graph.get("edges", [])],
            },
            "uncertainty": self.uncertainty,
            "provenance": self.provenance,
        }
        return result


@dataclass(slots=True)
class ExtractionUnitPackage:
    unit_id: str
    source: dict[str, Any]
    source_blocks: list[SourceBlock] = field(default_factory=list)
    concepts: list[Concept] = field(default_factory=list)
    atomic_items: list[AtomicItem] = field(default_factory=list)
    logical_groups: list[LogicalGroup] = field(default_factory=list)
    unresolved_items: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    context_metadata: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    schema_version: str = READING_UNIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "unit_id": self.unit_id,
            "source": self.source,
            "source_blocks": [b.to_dict() for b in self.source_blocks],
            "concepts": [c.to_dict() for c in self.concepts],
            "atomic_items": [a.to_dict() for a in self.atomic_items],
            "logical_groups": [g.to_dict() for g in self.logical_groups],
            "unresolved_items": self.unresolved_items,
            "validation": self.validation,
            "context_metadata": self.context_metadata,
            "metrics": self.metrics,
        }


# ── Document state types ──────────────────────────────────────────────────────


@dataclass(slots=True)
class AmbiguityQueueItem:
    item_id: str
    kind: str
    summary: str
    candidate_refs: list[str] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    severity: str = "info"
    suggested_action: str = "review"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class UserCorrectionOperation:
    operation_id: str
    operation_type: str
    target_ref: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    rationale: str = ""
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
