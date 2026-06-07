from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any


READING_UNIT_SCHEMA_VERSION = "reading-unit-v0.3"


def _load_type_config() -> dict[str, Any]:
    """Load type vocabularies from the shared JSON config file."""
    raw = (
        resources.files("tilusion.prompts")
        .joinpath("type_vocabularies.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


_TYPE_CONFIG = _load_type_config()


def _frozen(config: dict[str, Any], *keys: str) -> frozenset[str]:
    """Extract a frozenset from nested config keys."""
    node: Any = config
    for k in keys:
        node = node[k]
    if not isinstance(node, list):
        raise TypeError(f"Expected list at {'.'.join(keys)}, got {type(node).__name__}")
    return frozenset(node)


# ── Type vocabularies (derived from tilusion/prompts/type_vocabularies.json) ──

_CONCEPT_TYPE_NORMALIZATION: dict[str, str] = _TYPE_CONFIG["concept"].get(
    "normalization", {}
)

RECOMMENDED_CONCEPT_TYPES = _frozen(_TYPE_CONFIG, "concept", "all")
RECOMMENDED_ITEM_TYPES = _frozen(_TYPE_CONFIG, "item", "all")
RECOMMENDED_GROUP_TYPES = _frozen(_TYPE_CONFIG, "group", "all")
RECOMMENDED_EDGE_TYPES = _frozen(_TYPE_CONFIG, "edge", "all")
RECOMMENDED_PROVENANCE_VALUES = _frozen(_TYPE_CONFIG, "provenance", "all")


def get_domain_config(category: str, domain: str) -> dict[str, Any]:
    """Return the domain-specific configuration for a type category.

    Returns a dict with ``preferred``, ``extended``, and ``definitions`` keys,
    or an empty dict if the domain is not configured.
    """
    return _TYPE_CONFIG.get(category, {}).get(domain, {})


def normalize_concept_type(value: Any) -> str:
    """Normalize known noisy concept type aliases into the coarse schema vocabulary."""
    raw = str(value or "").strip()
    if not raw:
        return "other"
    key = re.sub(r"[\s-]+", "_", raw.lower())
    return _CONCEPT_TYPE_NORMALIZATION.get(key, key)

Grounding = str  # "source_grounded" | "synthesis" | "deterministic" | "llm_inferred" | "user_corrected"

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
