from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


READING_UNIT_SCHEMA_VERSION = "reading-unit-v0.1"
DOCUMENT_STATE_SCHEMA_VERSION = "document-state-v0.1"
REGISTRY_DELTA_SCHEMA_VERSION = "registry-delta-v0.1"

Confidence = Literal["high", "medium", "low", "unknown"]
Grounding = Literal[
    "source_grounded",
    "synthesis",
    "deterministic",
    "llm_inferred",
    "user_corrected",
]

CONFIDENCE_VALUES = frozenset({"high", "medium", "low", "unknown"})
GROUNDING_VALUES = frozenset(
    {
        "source_grounded",
        "synthesis",
        "deterministic",
        "llm_inferred",
        "user_corrected",
    }
)

RECOMMENDED_CONCEPT_TYPES = frozenset(
    {
        "person",
        "place",
        "object",
        "term",
        "method",
        "theme",
        "motif",
        "time_anchor",
        "other",
    }
)

RECOMMENDED_GROUP_TYPES = frozenset(
    {
        "event",
        "claim",
        "argument",
        "observation",
        "description",
        "explanation",
        "question",
        "other",
    }
)

RECOMMENDED_LINK_TYPES = frozenset(
    {
        "mentions",
        "supports",
        "contradicts",
        "causes",
        "precedes",
        "elaborates",
        "part_of",
        "exemplifies",
        "related_to",
        "other",
    }
)

DERIVED_VIEW_TYPES = frozenset(
    {
        "timeline",
        "discourse_graph",
        "claim_evidence_map",
        "theme_map",
        "viewpoint_evolution",
        "open_thread_list",
        "other",
    }
)

REGISTRY_DELTA_OPERATION_TYPES = frozenset(
    {
        "new_canonical_concept",
        "alias_candidate",
        "merge_proposal",
        "split_proposal",
        "summary_update",
        "concept_salience_update",
        "logical_group_continuation",
        "new_cross_unit_link",
        "derived_checkpoint_update",
        "unresolved_ambiguity_item",
        "user_review_needed",
    }
)


def is_open_type_string(value: Any) -> bool:
    """Return True for schema-light extensible type strings.

    The reading schema validates type fields as non-empty strings without
    requiring membership in the recommended sets. Detailed validators may warn
    on unfamiliar strings, but they should not reject justified custom types.
    """
    return isinstance(value, str) and bool(value.strip())


def is_recommended_concept_type(value: str) -> bool:
    return value in RECOMMENDED_CONCEPT_TYPES


def is_recommended_group_type(value: str) -> bool:
    return value in RECOMMENDED_GROUP_TYPES


def is_recommended_link_type(value: str) -> bool:
    return value in RECOMMENDED_LINK_TYPES


def is_confidence(value: Any) -> bool:
    return value in CONFIDENCE_VALUES


def is_grounding(value: Any) -> bool:
    return value in GROUNDING_VALUES


@dataclass(slots=True)
class SourceSpan:
    span_id: str
    unit_id: str
    source_range: dict[str, Any]
    quote: str
    relocation: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceBlock:
    block_id: str
    block_type: str
    span_refs: list[str]
    source_order: int | None = None
    text_digest: str | None = None
    confidence: Confidence = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConceptMention:
    mention_id: str
    surface: str
    concept_type: str
    source_block_refs: list[str]
    source_span_refs: list[str]
    canonical_name: str | None = None
    local_summary: str = ""
    aliases_or_candidates: list[dict[str, Any]] = field(default_factory=list)
    confidence: Confidence = "unknown"
    facets: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CanonicalConcept:
    concept_id: str
    canonical_name: str
    concept_types: list[str]
    facets: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    observed_surfaces: list[str] = field(default_factory=list)
    summary: str = ""
    first_seen_unit: str | None = None
    last_seen_unit: str | None = None
    salience: float | None = None
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    alias_candidates: list[dict[str, Any]] = field(default_factory=list)
    merge_split_uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LogicalGroup:
    group_id: str
    group_type: str
    summary: str
    source_block_refs: list[str]
    concept_refs: list[str] = field(default_factory=list)
    link_refs: list[str] = field(default_factory=list)
    source_order_hints: dict[str, Any] = field(default_factory=dict)
    temporal_hints: list[dict[str, Any]] = field(default_factory=list)
    confidence: Confidence = "unknown"
    uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=lambda: {"grounding": "source_grounded"})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GroupLink:
    link_id: str
    source_ref: str
    target_ref: str
    link_type: str
    evidence_block_refs: list[str] = field(default_factory=list)
    confidence: Confidence = "unknown"
    rationale: str = ""
    grounding: Grounding = "source_grounded"
    uncertainty: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DerivedStructure:
    view_id: str
    view_type: str
    structure: dict[str, Any]
    input_group_refs: list[str] = field(default_factory=list)
    input_link_refs: list[str] = field(default_factory=list)
    confidence: Confidence = "unknown"
    generated_by: str = "deterministic"
    is_source_of_truth: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractionUnitPackage:
    unit_id: str
    source: dict[str, Any]
    source_spans: list[SourceSpan] = field(default_factory=list)
    source_blocks: list[SourceBlock] = field(default_factory=list)
    concept_mentions: list[ConceptMention] = field(default_factory=list)
    logical_groups: list[LogicalGroup] = field(default_factory=list)
    links: list[GroupLink] = field(default_factory=list)
    derived_views: list[DerivedStructure] = field(default_factory=list)
    unresolved_items: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    context_metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = READING_UNIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "unit_id": self.unit_id,
            "source": self.source,
            "source_spans": [item.to_dict() for item in self.source_spans],
            "source_blocks": [item.to_dict() for item in self.source_blocks],
            "concept_mentions": [item.to_dict() for item in self.concept_mentions],
            "logical_groups": [item.to_dict() for item in self.logical_groups],
            "links": [item.to_dict() for item in self.links],
            "derived_views": [item.to_dict() for item in self.derived_views],
            "unresolved_items": self.unresolved_items,
            "validation": self.validation,
            "context_metadata": self.context_metadata,
        }


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


@dataclass(slots=True)
class DocumentStateSnapshot:
    document_id: str
    snapshot_id: str
    canonical_concepts: list[CanonicalConcept] = field(default_factory=list)
    reusable_group_summaries: list[dict[str, Any]] = field(default_factory=list)
    cross_unit_links: list[GroupLink] = field(default_factory=list)
    derived_checkpoints: list[DerivedStructure] = field(default_factory=list)
    ambiguity_queue: list[AmbiguityQueueItem] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = DOCUMENT_STATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "snapshot_id": self.snapshot_id,
            "canonical_concepts": [item.to_dict() for item in self.canonical_concepts],
            "reusable_group_summaries": self.reusable_group_summaries,
            "cross_unit_links": [item.to_dict() for item in self.cross_unit_links],
            "derived_checkpoints": [item.to_dict() for item in self.derived_checkpoints],
            "ambiguity_queue": [item.to_dict() for item in self.ambiguity_queue],
            "transactions": self.transactions,
        }


@dataclass(slots=True)
class RegistryDelta:
    delta_id: str
    base_snapshot_id: str
    unit_id: str
    operations: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    schema_version: str = REGISTRY_DELTA_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
