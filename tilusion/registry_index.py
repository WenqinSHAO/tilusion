from __future__ import annotations

import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .book_registry import BookRegistry
from .reading_schema import normalize_concept_type


@dataclass(slots=True)
class CompactGroup:
    """Compact one-line-per-group entry for the group resolution LLM prompt."""

    group_id: str
    group_type: str
    summary: str  # truncated to ~120 chars
    key_concept_ids: list[str]  # first 5 concept_refs
    item_count: int


# ── Lazy-loaded embedding model ────────────────────────────────────────────

_embedding_model: Any = None
_embedding_model_load_attempted: bool = False


def _get_embedding_model() -> Any | None:
    """Lazy-load Qwen3-Embedding-0.6B for semantic similarity.

    Returns None if the model can't be loaded (dual-signal degrades to
    BM25-only in that case).
    """
    global _embedding_model, _embedding_model_load_attempted
    if _embedding_model_load_attempted:
        return _embedding_model
    _embedding_model_load_attempted = True
    start = time.monotonic()
    try:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(
            "Qwen/Qwen3-Embedding-0.6B",
            trust_remote_code=True,
        )
        print(
            f"  [registry-index] embedding model loaded in {int((time.monotonic() - start) * 1000)}ms",
            file=sys.stderr,
        )
    except Exception as exc:
        _embedding_model = None
        print(
            f"  [registry-index] embedding model unavailable after {int((time.monotonic() - start) * 1000)}ms: {exc}",
            file=sys.stderr,
        )
    return _embedding_model


# ── BM25 ───────────────────────────────────────────────────────────────────


class BM25:
    """Minimal BM25 implementation for lexical concept retrieval.

    Indexes a corpus of text documents and scores them against queries
    using the standard BM25 formula with k1=1.5, b=0.75.
    """

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)

        self._docs: list[list[str]] = [_tokenize(doc) for doc in corpus]
        self._doc_len = [len(doc) for doc in self._docs]
        self._avgdl = sum(self._doc_len) / max(self.corpus_size, 1)

        # IDF pre-computation
        df: dict[str, int] = defaultdict(int)
        for doc in self._docs:
            for term in set(doc):
                df[term] += 1
        n = self.corpus_size
        self._idf: dict[str, float] = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """Return top-K (corpus_index, score) pairs for *query*."""
        query_tokens = _tokenize(query)
        scores = [0.0] * self.corpus_size

        for term in query_tokens:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, doc in enumerate(self._docs):
                f = doc.count(term)
                if f == 0:
                    continue
                doc_len = self._doc_len[i]
                numerator = f * (self.k1 + 1)
                denominator = f + self.k1 * (1 - self.b + self.b * doc_len / max(self._avgdl, 1))
                scores[i] += idf * numerator / denominator

        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, s) for idx, s in indexed[:top_k] if s > 0]


def _tokenize(text: str) -> list[str]:
    """Whitespace + punctuation tokenization with lowercasing."""
    return re.findall(r"\w+", text.lower())


# ── Text builders for concept indexing ─────────────────────────────────────


def _build_concept_text(reg_concept: dict[str, Any]) -> str:
    """Build searchable text for a registry concept."""
    parts: list[str] = []
    cname = reg_concept.get("canonical_name", "")
    if cname:
        parts.append(cname)
    summary = reg_concept.get("summary", "")
    if summary:
        parts.append(summary)
    for s in reg_concept.get("observed_surfaces", [])[:5]:
        if s and s not in parts:
            parts.append(s)
    return " ".join(parts)


def _build_unit_concept_text(uc: dict[str, Any]) -> str:
    """Build searchable text for a unit concept."""
    parts: list[str] = []
    surface = uc.get("surface", "")
    if surface:
        parts.append(surface)
    cname = uc.get("canonical_name", "")
    if cname and cname != surface:
        parts.append(cname)
    summary = uc.get("summary", "")
    if summary:
        parts.append(summary)
    return " ".join(parts)


def _build_group_text(cg: CompactGroup) -> str:
    """Build searchable text for a registry group's compact index entry."""
    parts: list[str] = []
    if cg.summary:
        parts.append(cg.summary)
    if cg.group_type:
        parts.append(cg.group_type)
    return " ".join(parts)


def _trace_preview(text: str, *, limit: int = 96) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def _build_unit_group_text(ug: dict[str, Any]) -> str:
    """Build searchable text for a unit group."""
    parts: list[str] = []
    summary = ug.get("summary", "")
    if summary:
        parts.append(summary)
    gtype = ug.get("group_type", "")
    if gtype:
        parts.append(gtype)
    return " ".join(parts)


# ── Reciprocal Rank Fusion ─────────────────────────────────────────────────


def _reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked lists of concept_ids with RRF.

    Args:
        rankings: List of ranked lists (concept_ids in rank order).
        k: RRF constant (default 60, per standard practice).

    Returns:
        Sorted list of (concept_id, fused_score) tuples, highest first.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, concept_id in enumerate(ranking, start=1):
            scores[concept_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ── Dual-signal candidate selection ────────────────────────────────────────


def _dual_signal_select(
    unit_concepts: list[dict[str, Any]],
    registry_index: list[dict[str, Any]],
    *,
    top_k: int = 20,
) -> set[str]:
    """BM25 + embedding similarity + RRF candidate selection."""
    if not unit_concepts or not registry_index:
        return set()

    total_start = time.monotonic()
    reg_texts = [_build_concept_text(c) for c in registry_index]
    reg_ids = [c["concept_id"] for c in registry_index]
    unit_texts = [_build_unit_concept_text(uc) for uc in unit_concepts]
    print(
        f"  [registry-index] concept selection: {len(unit_concepts)} queries, {len(registry_index)} registry concepts",
        file=sys.stderr,
    )

    bm25 = BM25(reg_texts)
    model = _get_embedding_model()

    reg_embeddings = None
    unit_embeddings = None
    reg_norms = None
    unit_norms = None
    if model is not None:
        try:
            import numpy as np

            t0 = time.monotonic()
            reg_embeddings = model.encode(reg_texts, convert_to_numpy=True)
            reg_norms = np.linalg.norm(reg_embeddings, axis=1)
            reg_ms = int((time.monotonic() - t0) * 1000)
            t0 = time.monotonic()
            unit_embeddings = model.encode(unit_texts, convert_to_numpy=True)
            unit_norms = np.linalg.norm(unit_embeddings, axis=1)
            unit_ms = int((time.monotonic() - t0) * 1000)
            print(
                f"  [registry-index] concept embeddings: registry {reg_ms}ms, unit batch {unit_ms}ms",
                file=sys.stderr,
            )
        except Exception as exc:
            reg_embeddings = None
            unit_embeddings = None
            print(
                f"  [registry-index] concept embeddings unavailable: {exc}; using BM25 only",
                file=sys.stderr,
            )

    candidate_ids: set[str] = set()
    traced = 0
    for idx, (uc, uc_text) in enumerate(zip(unit_concepts, unit_texts)):
        bm25_results = bm25.search(uc_text, top_k=top_k)
        bm25_ranking = [reg_ids[i] for i, _ in bm25_results]

        embedding_ranking: list[str] = []
        if reg_embeddings is not None and unit_embeddings is not None:
            try:
                import numpy as np

                uc_emb = unit_embeddings[idx]
                uc_norm = float(unit_norms[idx]) if unit_norms is not None else float(np.linalg.norm(uc_emb))
                sims = np.dot(reg_embeddings, uc_emb) / (reg_norms * uc_norm + 1e-8)
                top_indices = np.argsort(sims)[::-1][:top_k]
                embedding_ranking = [reg_ids[int(i)] for i in top_indices if float(sims[int(i)]) > 0.3]
            except Exception as exc:
                print(
                    f"  [registry-index] concept query {uc.get('concept_id', idx)} embedding failed: {exc}",
                    file=sys.stderr,
                )

        rankings: list[list[str]] = [bm25_ranking]
        if embedding_ranking:
            rankings.append(embedding_ranking)

        fused = _reciprocal_rank_fusion(rankings)
        selected = [concept_id for concept_id, _ in fused[:top_k]]
        candidate_ids.update(selected)
        if traced < 20:
            query = _trace_preview(uc_text, limit=72)
            print(
                f"    [registry-index] query {uc.get('concept_id', idx)} {query!r}: "
                f"bm25={bm25_ranking[:3]} embed={embedding_ranking[:3]} selected={selected[:5]}",
                file=sys.stderr,
            )
            traced += 1

    if len(unit_concepts) > traced:
        print(
            f"    [registry-index] ... {len(unit_concepts) - traced} more concept queries omitted",
            file=sys.stderr,
        )
    print(
        f"  [registry-index] concept selection picked {len(candidate_ids)} candidates in {int((time.monotonic() - total_start) * 1000)}ms",
        file=sys.stderr,
    )
    return candidate_ids


def _dual_signal_select_groups(
    unit_groups: list[dict[str, Any]],
    compact_groups: list[CompactGroup],
    *,
    top_k: int = 20,
) -> set[str]:
    """BM25 + embedding similarity + RRF for group candidate selection."""
    if not unit_groups or not compact_groups:
        return set()

    total_start = time.monotonic()
    reg_texts = [_build_group_text(cg) for cg in compact_groups]
    reg_ids = [cg.group_id for cg in compact_groups]
    unit_texts = [_build_unit_group_text(ug) for ug in unit_groups]
    print(
        f"  [registry-index] group selection: {len(unit_groups)} queries, {len(compact_groups)} registry groups",
        file=sys.stderr,
    )

    bm25 = BM25(reg_texts)
    model = _get_embedding_model()

    reg_embeddings = None
    unit_embeddings = None
    reg_norms = None
    unit_norms = None
    if model is not None:
        try:
            import numpy as np

            t0 = time.monotonic()
            reg_embeddings = model.encode(reg_texts, convert_to_numpy=True)
            reg_norms = np.linalg.norm(reg_embeddings, axis=1)
            reg_ms = int((time.monotonic() - t0) * 1000)
            t0 = time.monotonic()
            unit_embeddings = model.encode(unit_texts, convert_to_numpy=True)
            unit_norms = np.linalg.norm(unit_embeddings, axis=1)
            unit_ms = int((time.monotonic() - t0) * 1000)
            print(
                f"  [registry-index] group embeddings: registry {reg_ms}ms, unit batch {unit_ms}ms",
                file=sys.stderr,
            )
        except Exception as exc:
            reg_embeddings = None
            unit_embeddings = None
            print(
                f"  [registry-index] group embeddings unavailable: {exc}; using BM25 only",
                file=sys.stderr,
            )

    candidate_ids: set[str] = set()
    for idx, (ug, ug_text) in enumerate(zip(unit_groups, unit_texts)):
        bm25_results = bm25.search(ug_text, top_k=top_k)
        bm25_ranking = [reg_ids[i] for i, _ in bm25_results]

        embedding_ranking: list[str] = []
        if reg_embeddings is not None and unit_embeddings is not None:
            try:
                import numpy as np

                ug_emb = unit_embeddings[idx]
                ug_norm = float(unit_norms[idx]) if unit_norms is not None else float(np.linalg.norm(ug_emb))
                sims = np.dot(reg_embeddings, ug_emb) / (reg_norms * ug_norm + 1e-8)
                top_indices = np.argsort(sims)[::-1][:top_k]
                embedding_ranking = [reg_ids[int(i)] for i in top_indices if float(sims[int(i)]) > 0.3]
            except Exception as exc:
                print(
                    f"  [registry-index] group query {ug.get('group_id', idx)} embedding failed: {exc}",
                    file=sys.stderr,
                )

        rankings: list[list[str]] = [bm25_ranking]
        if embedding_ranking:
            rankings.append(embedding_ranking)

        fused = _reciprocal_rank_fusion(rankings)
        selected = [group_id for group_id, _ in fused[:top_k]]
        candidate_ids.update(selected)
        query = _trace_preview(ug_text, limit=72)
        print(
            f"    [registry-index] group query {ug.get('group_id', idx)} {query!r}: "
            f"bm25={bm25_ranking[:3]} embed={embedding_ranking[:3]} selected={selected[:5]}",
            file=sys.stderr,
        )

    print(
        f"  [registry-index] group selection picked {len(candidate_ids)} candidates in {int((time.monotonic() - total_start) * 1000)}ms",
        file=sys.stderr,
    )
    return candidate_ids


# ── Deterministic pre-filter ───────────────────────────────────────────────


def _deterministic_filter(
    unit_concepts: list[dict[str, Any]],
    registry_index: list[dict[str, Any]],
) -> set[str]:
    """Deterministic pre-filter by surface collision + type family + canonical_name.

    Returns set of registry concept_ids that match deterministically.
    """
    type_families: dict[str, set[str]] = {
        "person": {"person", "group", "organization", "social_role"},
        "group": {"person", "group", "organization"},
        "organization": {"person", "group", "organization", "institution"},
        "institution": {"organization", "institution"},
        "place": {"place", "scene_element"},
        "scene_element": {"place", "scene_element"},
    }

    def _relaxed_types(concept_type: str) -> set[str]:
        t = normalize_concept_type(concept_type)
        return type_families.get(t, {t})

    candidate_ids: set[str] = set()

    for uc in unit_concepts:
        uc_type = uc.get("concept_type", "")
        relaxed = _relaxed_types(uc_type)
        uc_surface = (uc.get("surface") or "").lower()
        uc_cname = (uc.get("canonical_name") or "").lower()

        for reg in registry_index:
            rid = reg["concept_id"]
            if rid in candidate_ids:
                continue
            # Type family match
            if reg["concept_type"] not in relaxed:
                continue
            # Surface or canonical_name collision
            reg_surfaces = {s.lower() for s in reg.get("observed_surfaces", [])}
            reg_cname = (reg.get("canonical_name") or "").lower()
            if uc_surface and uc_surface in reg_surfaces:
                candidate_ids.add(rid)
            elif uc_cname and uc_cname == reg_cname:
                candidate_ids.add(rid)
            elif uc_surface and reg_cname and uc_surface == reg_cname:
                candidate_ids.add(rid)

        # Also match by canonical_name across any type
        if uc_cname:
            for reg in registry_index:
                rid = reg["concept_id"]
                if rid in candidate_ids:
                    continue
                if (reg.get("canonical_name") or "").lower() == uc_cname:
                    candidate_ids.add(rid)

    return candidate_ids


# ── Public API ─────────────────────────────────────────────────────────────


def build_registry_index(registry: BookRegistry) -> list[dict[str, Any]]:
    """Build a compact concept index for the LLM concept resolution pass.

    One line per concept: concept_id, canonical_name, concept_type,
    summary (truncated to ~120 chars), observed_surfaces (first 10).
    """
    if not registry.has_concepts():
        return []
    index: list[dict[str, Any]] = []
    for concept in registry._concepts.values():
        summary = concept.summary or ""
        if len(summary) > 120:
            summary = summary[:117] + "..."
        index.append({
            "concept_id": concept.concept_id,
            "canonical_name": concept.canonical_name or "",
            "concept_type": concept.concept_type or "other",
            "summary": summary,
            "observed_surfaces": concept.observed_surfaces[:10],
        })
    return index


def build_group_index(registry: BookRegistry) -> list[CompactGroup]:
    """Build a compact group index for the LLM group resolution pass.

    One line per group: group_id, group_type, summary (truncated ~120 chars),
    key_concept_ids (first 5 concept_refs), item_count.
    """
    if not registry.has_groups():
        return []
    index: list[CompactGroup] = []
    for gid, g in registry._groups.items():
        summary = g.get("summary", "")
        if len(summary) > 120:
            summary = summary[:117] + "..."
        concept_refs = g.get("concept_refs", [])
        index.append(CompactGroup(
            group_id=gid,
            group_type=g.get("group_type", "other"),
            summary=summary,
            key_concept_ids=list(dict.fromkeys(concept_refs))[:5],
            item_count=len(g.get("item_refs", [])),
        ))
    return index


def select_concept_candidates(
    unit_concepts: list[dict[str, Any]],
    registry_index: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select candidate registry concepts for LLM concept resolution.

    When the registry is small (≤50 concepts), the full index is returned.
    When larger, a hybrid approach is used:

    1. **Deterministic pre-filter**: surface collision + type family +
       canonical_name exact match — catches the easy cases.
    2. **Dual-signal retrieval**: BM25 lexical + Qwen3-Embedding-0.6B
       semantic similarity + Reciprocal Rank Fusion — catches the
       "new surface" case where a known entity appears under a
       completely different name (e.g., "the old man" ↔ "Shen Fu").

    The two candidate sets are unioned so neither pathway misses
    valid matches.
    """
    if not registry_index or not unit_concepts:
        return registry_index

    if len(registry_index) <= 50:
        return registry_index

    # Deterministic pre-filter: surface collision + type family
    det_ids = _deterministic_filter(unit_concepts, registry_index)

    # Dual-signal: BM25 + embedding + RRF
    dual_ids = _dual_signal_select(unit_concepts, registry_index)

    all_ids = det_ids | dual_ids

    if not all_ids:
        return []

    return [r for r in registry_index if r["concept_id"] in all_ids]


def select_group_candidates(
    unit_groups: list[dict[str, Any]],
    registry_groups: list[dict[str, Any]],
    resolved_concepts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pre-filter registry groups by concept overlap + dual-signal retrieval.

    Always includes groups with concept overlap (deterministic). When the
    registry has >50 groups, also applies BM25 + embedding + RRF dual-signal
    retrieval for semantic matches without concept overlap.
    """
    if not registry_groups or not unit_groups:
        return registry_groups

    if len(registry_groups) <= 50:
        return registry_groups

    # Collect registry concept IDs that unit groups reference (via resolution)
    unit_registry_refs: set[str] = set()
    for c in resolved_concepts:
        ref = c.get("registry_ref", "")
        if ref:
            unit_registry_refs.add(ref)

    # Deterministic pre-filter: concept overlap
    overlap_ids: set[str] = set()
    for rg in registry_groups:
        rg_concepts = set(rg.get("concept_refs", []))
        if rg_concepts & unit_registry_refs:
            overlap_ids.add(rg["group_id"])

    # Dual-signal retrieval (when registry is large)
    compact_groups = [
        CompactGroup(
            group_id=rg["group_id"],
            group_type=rg.get("group_type", "other"),
            summary=(
                rg.get("summary", "")[:117] + "..."
                if len(rg.get("summary", "")) > 120
                else rg.get("summary", "")
            ),
            key_concept_ids=list(dict.fromkeys(rg.get("concept_refs", [])))[:5],
            item_count=len(rg.get("item_refs", [])),
        )
        for rg in registry_groups
    ]
    dual_ids = _dual_signal_select_groups(unit_groups, compact_groups)

    all_ids = overlap_ids | dual_ids

    if not all_ids:
        # Fallback: return groups matching by type
        unit_group_types = {g.get("group_type", "") for g in unit_groups}
        return [
            rg for rg in registry_groups
            if rg.get("group_type", "") in unit_group_types
        ]

    return [rg for rg in registry_groups if rg["group_id"] in all_ids]
