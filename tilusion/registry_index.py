from __future__ import annotations

import hashlib
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
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


# ── Embedding cache (two-layer: memory + disk) ─────────────────────────────

_mem_embeddings: dict[str, Any] = {}
"""In-memory cache: survives across tool calls within a single pipeline run."""

_embedding_cache: EmbeddingCache | None = None  # forward reference, defined below


class EmbeddingCache:
    """Two-layer embedding cache: memory (fast) + disk (persistent).

    Keys are ``sha256(text)`` hex digests. The in-memory layer is checked
    first; disk is checked on miss; ``model.encode()`` is needed only when
    both layers miss.

    Parameters:
        cache_dir: Directory for ``.npy`` files. When ``None``, only the
                   in-memory layer is used (disk persistence disabled).
    """

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else None

    @staticmethod
    def key_for(text: str) -> str:
        """Return a stable cache key for *text* (sha256 hex digest)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        """Return cached embedding for *key*, or ``None`` on miss."""
        arr = _mem_embeddings.get(key)
        if arr is not None:
            return arr
        if self._cache_dir is not None:
            path = self._cache_dir / f"{key}.npy"
            if path.exists():
                import numpy as np

                arr = np.load(path)
                _mem_embeddings[key] = arr  # promote to memory
                return arr
        return None

    def put(self, key: str, embedding: Any) -> None:
        """Store *embedding* in both memory and disk layers."""
        _mem_embeddings[key] = embedding
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            import numpy as np

            np.save(str(self._cache_dir / f"{key}.npy"), embedding)

    def batch_get(
        self, texts: list[str]
    ) -> tuple[dict[int, Any], list[int]]:
        """Return ``{index: embedding}`` for cache hits and list of miss indices.

        The caller is responsible for encoding only the miss indices, then
        calling :meth:`put` for each newly-computed embedding.
        """
        hits: dict[int, Any] = {}
        misses: list[int] = []
        for i, text in enumerate(texts):
            key = self.key_for(text)
            emb = self.get(key)
            if emb is not None:
                hits[i] = emb
            else:
                misses.append(i)
        return hits, misses


def _get_embedding_cache(cache_dir: str | Path | None = None) -> EmbeddingCache:
    """Return the module-level ``EmbeddingCache`` singleton, creating it on first call."""
    global _embedding_cache
    if _embedding_cache is None:
        _embedding_cache = EmbeddingCache(cache_dir)
    return _embedding_cache


def init_embedding_cache(cache_dir: str | Path | None) -> EmbeddingCache:
    """Initialize (or re-initialize) the module-level embedding cache with *cache_dir*."""
    global _embedding_cache
    _embedding_cache = EmbeddingCache(cache_dir)
    return _embedding_cache


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
    for a in reg_concept.get("aliases", [])[:5]:
        if a and a not in parts:
            parts.append(a)
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
    for a in uc.get("aliases", [])[:5]:
        if a and a not in parts:
            parts.append(a)
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
    type_filter: bool = False,
    trace: dict[str, Any] | None = None,
) -> set[str]:
    """BM25 + embedding similarity + RRF candidate selection.

    When *type_filter* is True, restricts each query to registry
    concepts in the same type family, avoiding meaningless cross-type
    comparisons and reducing noise in the candidate set.
    """
    if not unit_concepts or not registry_index:
        return set()

    total_start = time.monotonic()
    reg_texts = [_build_concept_text(c) for c in registry_index]
    reg_ids = [c["concept_id"] for c in registry_index]
    unit_texts = [_build_unit_concept_text(uc) for uc in unit_concepts]
    filter_label = "type-filtered " if type_filter else ""
    print(
        f"  [registry-index] concept selection: {len(unit_concepts)} queries, "
        f"{len(registry_index)} registry concepts ({filter_label}dual-signal)",
        file=sys.stderr,
    )
    if trace is not None:
        trace.update({
            "kind": "concept_dual_signal",
            "query_count": len(unit_concepts),
            "registry_count": len(registry_index),
            "top_k": top_k,
            "type_filter": type_filter,
            "queries": [],
        })

    # Pre-compute type-family masks for each unique unit concept type
    type_mask: dict[str, list[int]] = {}
    if type_filter:
        all_indices = list(range(len(registry_index)))
        for uc in unit_concepts:
            uc_type = normalize_concept_type(uc.get("concept_type", ""))
            if uc_type not in type_mask:
                relaxed = _relaxed_types(uc_type)
                if "*" in relaxed:
                    type_mask[uc_type] = all_indices
                else:
                    type_mask[uc_type] = [
                        i for i, r in enumerate(registry_index)
                        if r.get("concept_type", "") in relaxed
                    ]

    bm25 = BM25(reg_texts)
    model = _get_embedding_model()

    reg_embeddings = None
    unit_embeddings = None
    reg_norms = None
    unit_norms = None
    cache_hit_reg = 0
    cache_hit_unit = 0
    if model is not None:
        try:
            import numpy as np

            cache = _get_embedding_cache()

            # ── Registry embeddings with cache ──────────────────────────
            t0 = time.monotonic()
            reg_hits, reg_misses = cache.batch_get(reg_texts)
            cache_hit_reg = len(reg_hits)
            if reg_misses:
                miss_texts = [reg_texts[i] for i in reg_misses]
                miss_embs = model.encode(miss_texts, convert_to_numpy=True)
                for i, emb in zip(reg_misses, miss_embs):
                    cache.put(cache.key_for(reg_texts[i]), emb)
                    reg_hits[i] = emb
            reg_embeddings = np.stack([reg_hits[i] for i in range(len(reg_texts))])
            reg_norms = np.linalg.norm(reg_embeddings, axis=1)
            reg_ms = int((time.monotonic() - t0) * 1000)

            # ── Unit embeddings with cache ──────────────────────────────
            t0 = time.monotonic()
            unit_hits, unit_misses = cache.batch_get(unit_texts)
            cache_hit_unit = len(unit_hits)
            if unit_misses:
                miss_texts = [unit_texts[i] for i in unit_misses]
                miss_embs = model.encode(miss_texts, convert_to_numpy=True)
                for i, emb in zip(unit_misses, miss_embs):
                    cache.put(cache.key_for(unit_texts[i]), emb)
                    unit_hits[i] = emb
            unit_embeddings = np.stack([unit_hits[i] for i in range(len(unit_texts))])
            unit_norms = np.linalg.norm(unit_embeddings, axis=1)
            unit_ms = int((time.monotonic() - t0) * 1000)

            print(
                f"  [registry-index] concept embeddings: registry {reg_ms}ms "
                f"(cache {cache_hit_reg}/{len(reg_texts)} hits), "
                f"unit {unit_ms}ms (cache {cache_hit_unit}/{len(unit_texts)} hits)",
                file=sys.stderr,
            )
            if trace is not None:
                trace["embedding"] = {
                    "available": True,
                    "registry_ms": reg_ms,
                    "unit_batch_ms": unit_ms,
                    "registry_cache_hits": cache_hit_reg,
                    "registry_total": len(reg_texts),
                    "unit_cache_hits": cache_hit_unit,
                    "unit_total": len(unit_texts),
                }
        except Exception as exc:
            reg_embeddings = None
            unit_embeddings = None
            print(
                f"  [registry-index] concept embeddings unavailable: {exc}; using BM25 only",
                file=sys.stderr,
            )
            if trace is not None:
                trace["embedding"] = {"available": False, "error": str(exc)}

    # ── Per-concept query loop ────────────────────────────────────────────
    bm25_total_ms = 0
    cosine_total_ms = 0
    candidate_ids: set[str] = set()
    traced = 0
    for idx, (uc, uc_text) in enumerate(zip(unit_concepts, unit_texts)):
        # Type-family filter mask for this concept (used by both BM25 and embedding)
        allowed_indices: set[int] | None = None
        if type_filter:
            uc_type = normalize_concept_type(uc.get("concept_type", ""))
            allowed = type_mask.get(uc_type)
            if allowed is not None:
                allowed_indices = set(allowed)

        # --- BM25 ---
        t_bm25 = time.monotonic()
        bm25_results = bm25.search(uc_text, top_k=top_k)
        bm25_total_ms += (time.monotonic() - t_bm25) * 1000

        bm25_top = [
            {"id": reg_ids[i], "score": round(float(score), 6)}
            for i, score in bm25_results[:top_k]
        ]

        # Filter BM25 results
        if allowed_indices is not None:
            bm25_ranking = [reg_ids[i] for i, _ in bm25_results if i in allowed_indices]
        else:
            bm25_ranking = [reg_ids[i] for i, _ in bm25_results]

        # --- Embedding similarity ---
        embedding_ranking: list[str] = []
        embedding_top: list[dict[str, Any]] = []
        if reg_embeddings is not None and unit_embeddings is not None:
            try:
                import numpy as np

                t_cos = time.monotonic()
                uc_emb = unit_embeddings[idx]
                uc_norm = float(unit_norms[idx]) if unit_norms is not None else float(np.linalg.norm(uc_emb))

                if allowed_indices is not None and allowed_indices:
                    # Restrict to type-compatible subset
                    subset_indices = sorted(allowed_indices)
                    subset_embs = reg_embeddings[subset_indices]
                    subset_norms = reg_norms[subset_indices]
                    sims = np.dot(subset_embs, uc_emb) / (subset_norms * uc_norm + 1e-8)
                    top_k_eff = min(top_k, len(subset_indices))
                    top_local = np.argsort(sims)[::-1][:top_k_eff]
                    embedding_ranking = [
                        reg_ids[subset_indices[int(i)]]
                        for i in top_local if float(sims[int(i)]) > 0.3
                    ]
                    embedding_top = [
                        {"id": reg_ids[subset_indices[int(i)]], "score": round(float(sims[int(i)]), 6)}
                        for i in top_local if float(sims[int(i)]) > 0.3
                    ]
                else:
                    # Full comparison
                    sims = np.dot(reg_embeddings, uc_emb) / (reg_norms * uc_norm + 1e-8)
                    top_indices = np.argsort(sims)[::-1][:top_k]
                    embedding_ranking = [
                        reg_ids[int(i)] for i in top_indices if float(sims[int(i)]) > 0.3
                    ]
                    embedding_top = [
                        {"id": reg_ids[int(i)], "score": round(float(sims[int(i)]), 6)}
                        for i in top_indices if float(sims[int(i)]) > 0.3
                    ]
                cosine_total_ms += (time.monotonic() - t_cos) * 1000
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
        query = _trace_preview(uc_text, limit=72)
        if trace is not None:
            trace.setdefault("queries", []).append({
                "unit_concept_id": uc.get("concept_id", ""),
                "surface": uc.get("surface", ""),
                "concept_type": uc.get("concept_type", ""),
                "query": query,
                "allowed_registry_count": len(allowed_indices) if allowed_indices is not None else len(registry_index),
                "bm25_top": bm25_top[:10],
                "bm25_filtered_top": bm25_ranking[:10],
                "embedding_top": embedding_top[:10],
                "selected": selected[:top_k],
            })
        if traced < 20:
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
    timing_parts = [f"bm25={int(bm25_total_ms)}ms"]
    if reg_embeddings is not None:
        timing_parts.append(f"cosine={int(cosine_total_ms)}ms")
    elapsed_ms = int((time.monotonic() - total_start) * 1000)
    if trace is not None:
        trace["selected_candidate_ids"] = sorted(candidate_ids)
        trace["timings_ms"] = {"total": elapsed_ms, "bm25": int(bm25_total_ms)}
        if reg_embeddings is not None:
            trace["timings_ms"]["cosine"] = int(cosine_total_ms)
    print(
        f"  [registry-index] concept selection picked {len(candidate_ids)} candidates "
        f"in {elapsed_ms}ms ({' '.join(timing_parts)})",
        file=sys.stderr,
    )
    return candidate_ids


def _dual_signal_select_groups(
    unit_groups: list[dict[str, Any]],
    compact_groups: list[CompactGroup],
    *,
    top_k: int = 20,
    trace: dict[str, Any] | None = None,
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
    if trace is not None:
        trace.update({
            "kind": "group_dual_signal",
            "query_count": len(unit_groups),
            "registry_count": len(compact_groups),
            "top_k": top_k,
            "queries": [],
        })

    bm25 = BM25(reg_texts)
    model = _get_embedding_model()

    reg_embeddings = None
    unit_embeddings = None
    reg_norms = None
    unit_norms = None
    cache_hit_reg = 0
    cache_hit_unit = 0
    if model is not None:
        try:
            import numpy as np

            cache = _get_embedding_cache()

            # ── Registry group embeddings with cache ─────────────────────
            t0 = time.monotonic()
            reg_hits, reg_misses = cache.batch_get(reg_texts)
            cache_hit_reg = len(reg_hits)
            if reg_misses:
                miss_texts = [reg_texts[i] for i in reg_misses]
                miss_embs = model.encode(miss_texts, convert_to_numpy=True)
                for i, emb in zip(reg_misses, miss_embs):
                    cache.put(cache.key_for(reg_texts[i]), emb)
                    reg_hits[i] = emb
            reg_embeddings = np.stack([reg_hits[i] for i in range(len(reg_texts))])
            reg_norms = np.linalg.norm(reg_embeddings, axis=1)
            reg_ms = int((time.monotonic() - t0) * 1000)

            # ── Unit group embeddings with cache ─────────────────────────
            t0 = time.monotonic()
            unit_hits, unit_misses = cache.batch_get(unit_texts)
            cache_hit_unit = len(unit_hits)
            if unit_misses:
                miss_texts = [unit_texts[i] for i in unit_misses]
                miss_embs = model.encode(miss_texts, convert_to_numpy=True)
                for i, emb in zip(unit_misses, miss_embs):
                    cache.put(cache.key_for(unit_texts[i]), emb)
                    unit_hits[i] = emb
            unit_embeddings = np.stack([unit_hits[i] for i in range(len(unit_texts))])
            unit_norms = np.linalg.norm(unit_embeddings, axis=1)
            unit_ms = int((time.monotonic() - t0) * 1000)

            print(
                f"  [registry-index] group embeddings: registry {reg_ms}ms "
                f"(cache {cache_hit_reg}/{len(reg_texts)} hits), "
                f"unit {unit_ms}ms (cache {cache_hit_unit}/{len(unit_texts)} hits)",
                file=sys.stderr,
            )
            if trace is not None:
                trace["embedding"] = {
                    "available": True,
                    "registry_ms": reg_ms,
                    "unit_batch_ms": unit_ms,
                    "registry_cache_hits": cache_hit_reg,
                    "registry_total": len(reg_texts),
                    "unit_cache_hits": cache_hit_unit,
                    "unit_total": len(unit_texts),
                }
        except Exception as exc:
            reg_embeddings = None
            unit_embeddings = None
            print(
                f"  [registry-index] group embeddings unavailable: {exc}; using BM25 only",
                file=sys.stderr,
            )
            if trace is not None:
                trace["embedding"] = {"available": False, "error": str(exc)}

    candidate_ids: set[str] = set()
    for idx, (ug, ug_text) in enumerate(zip(unit_groups, unit_texts)):
        bm25_results = bm25.search(ug_text, top_k=top_k)
        bm25_ranking = [reg_ids[i] for i, _ in bm25_results]
        bm25_top = [
            {"id": reg_ids[i], "score": round(float(score), 6)}
            for i, score in bm25_results[:top_k]
        ]

        embedding_ranking: list[str] = []
        embedding_top: list[dict[str, Any]] = []
        if reg_embeddings is not None and unit_embeddings is not None:
            try:
                import numpy as np

                ug_emb = unit_embeddings[idx]
                ug_norm = float(unit_norms[idx]) if unit_norms is not None else float(np.linalg.norm(ug_emb))
                sims = np.dot(reg_embeddings, ug_emb) / (reg_norms * ug_norm + 1e-8)
                top_indices = np.argsort(sims)[::-1][:top_k]
                embedding_ranking = [reg_ids[int(i)] for i in top_indices if float(sims[int(i)]) > 0.3]
                embedding_top = [
                    {"id": reg_ids[int(i)], "score": round(float(sims[int(i)]), 6)}
                    for i in top_indices if float(sims[int(i)]) > 0.3
                ]
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
        if trace is not None:
            trace.setdefault("queries", []).append({
                "unit_group_id": ug.get("group_id", ""),
                "group_type": ug.get("group_type", ""),
                "query": query,
                "bm25_top": bm25_top[:10],
                "embedding_top": embedding_top[:10],
                "selected": selected[:top_k],
            })
        print(
            f"    [registry-index] group query {ug.get('group_id', idx)} {query!r}: "
            f"bm25={bm25_ranking[:3]} embed={embedding_ranking[:3]} selected={selected[:5]}",
            file=sys.stderr,
        )

    elapsed_ms = int((time.monotonic() - total_start) * 1000)
    if trace is not None:
        trace["selected_candidate_ids"] = sorted(candidate_ids)
        trace["timings_ms"] = {"total": elapsed_ms}
    print(
        f"  [registry-index] group selection picked {len(candidate_ids)} candidates in {elapsed_ms}ms",
        file=sys.stderr,
    )
    return candidate_ids


# ── Type families for relaxed type matching ──────────────────────────────────

TYPE_FAMILIES: dict[str, set[str]] = {
    "person": {"person", "group", "organization", "social_role"},
    "group": {"person", "group", "organization"},
    "organization": {"person", "group", "organization", "institution"},
    "institution": {"organization", "institution"},
    "place": {"place", "scene_element"},
    "scene_element": {"place", "scene_element"},
}


def _relaxed_types(concept_type: str) -> set[str]:
    t = normalize_concept_type(concept_type)
    if t == "other":
        # "other" is an uncertain type — allow matching anything
        return {"*"}
    return TYPE_FAMILIES.get(t, {t})


# ── Deterministic pre-filter ───────────────────────────────────────────────


def _deterministic_filter(
    unit_concepts: list[dict[str, Any]],
    registry_index: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """Deterministic pre-filter by surface collision + type family + canonical_name.

    Returns ``(candidate_registry_ids, matched_unit_concept_ids)`` where the
    second set tracks which unit concepts found at least one deterministic
    candidate so the caller can skip expensive semantic search for them.
    """
    candidate_ids: set[str] = set()
    matched_unit_ids: set[str] = set()

    for uc in unit_concepts:
        uc_type = uc.get("concept_type", "")
        relaxed = _relaxed_types(uc_type)
        uc_surface = (uc.get("surface") or "").lower()
        uc_cname = (uc.get("canonical_name") or "").lower()
        uc_aliases = {a.lower() for a in uc.get("aliases", [])}
        uc_id = uc.get("concept_id", "")
        got_match = False

        for reg in registry_index:
            rid = reg["concept_id"]
            if rid in candidate_ids:
                continue
            # Type family match
            if reg["concept_type"] not in relaxed:
                continue

            reg_surface = (reg.get("surface") or "").lower()
            reg_surfaces = {s.lower() for s in reg.get("observed_surfaces", [])}
            reg_surfaces.add(reg_surface)
            reg_surfaces.discard("")
            reg_cname = (reg.get("canonical_name") or "").lower()
            reg_aliases = {a.lower() for a in reg.get("aliases", [])}

            # Surface collision
            if uc_surface and uc_surface in reg_surfaces:
                candidate_ids.add(rid)
                got_match = True
            # Canonical_name match
            elif uc_cname and uc_cname == reg_cname:
                candidate_ids.add(rid)
                got_match = True
            # Unit surface matches registry canonical_name
            elif uc_surface and reg_cname and uc_surface == reg_cname:
                candidate_ids.add(rid)
                got_match = True
            # Unit canonical_name matches registry alias
            elif uc_cname and uc_cname in reg_aliases:
                candidate_ids.add(rid)
                got_match = True
            # Registry canonical_name matches unit alias
            elif reg_cname and reg_cname in uc_aliases:
                candidate_ids.add(rid)
                got_match = True
            # Any unit alias matches any registry alias
            elif uc_aliases and reg_aliases and (uc_aliases & reg_aliases):
                candidate_ids.add(rid)
                got_match = True
            # Any unit alias matches a registry surface
            elif uc_aliases and uc_aliases & reg_surfaces:
                candidate_ids.add(rid)
                got_match = True
            # Unit surface matches any registry alias
            elif uc_surface and uc_surface in reg_aliases:
                candidate_ids.add(rid)
                got_match = True

        # Also match by canonical_name across any type
        if uc_cname:
            for reg in registry_index:
                rid = reg["concept_id"]
                if rid in candidate_ids:
                    continue
                if (reg.get("canonical_name") or "").lower() == uc_cname:
                    candidate_ids.add(rid)
                    got_match = True
        # Also match by canonical_name in registry aliases across any type
        if uc_cname:
            for reg in registry_index:
                rid = reg["concept_id"]
                if rid in candidate_ids:
                    continue
                reg_aliases = {a.lower() for a in reg.get("aliases", [])}
                if uc_cname in reg_aliases:
                    candidate_ids.add(rid)
                    got_match = True

        if got_match and uc_id:
            matched_unit_ids.add(uc_id)

    return candidate_ids, matched_unit_ids


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
            "surface": concept.surface or "",
            "canonical_name": concept.canonical_name or "",
            "concept_type": concept.concept_type or "other",
            "summary": summary,
            "observed_surfaces": concept.observed_surfaces[:10],
            "aliases": concept.aliases[:10],
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
    trace: dict[str, Any] | None = None,
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
    if trace is not None:
        trace.update({
            "kind": "concept_candidate_selection",
            "unit_concept_count": len(unit_concepts),
            "registry_concept_count": len(registry_index),
            "small_registry_passthrough": False,
        })
    if not registry_index or not unit_concepts:
        if trace is not None:
            trace["selected_candidate_ids"] = [r.get("concept_id", "") for r in registry_index]
        return registry_index

    if len(registry_index) <= 50:
        if trace is not None:
            trace["small_registry_passthrough"] = True
            trace["selected_candidate_ids"] = [r.get("concept_id", "") for r in registry_index]
        return registry_index

    # Deterministic pre-filter: surface collision + type family.
    # Also returns which unit concepts already found candidates so we can
    # skip expensive dual-signal retrieval for them.
    det_ids, matched_unit_ids = _deterministic_filter(unit_concepts, registry_index)
    if trace is not None:
        trace["deterministic"] = {
            "candidate_ids": sorted(det_ids),
            "matched_unit_concept_ids": sorted(matched_unit_ids),
            "matched_unit_count": len(matched_unit_ids),
        }

    # Only run dual-signal (BM25 + embedding) for concepts that the
    # deterministic filter found NOTHING for.
    unmatched = [uc for uc in unit_concepts if uc.get("concept_id") not in matched_unit_ids]
    dual_ids: set[str] = set()
    if unmatched:
        print(
            f"  [registry-index] deterministic filter: {len(matched_unit_ids)}/{len(unit_concepts)} concepts matched; "
            f"running dual-signal on {len(unmatched)} unmatched",
            file=sys.stderr,
        )
        dual_trace: dict[str, Any] = {}
        dual_ids = _dual_signal_select(unmatched, registry_index, type_filter=True, trace=dual_trace)
        if trace is not None:
            trace["dual_signal"] = dual_trace
    else:
        print(
            f"  [registry-index] deterministic filter: all {len(matched_unit_ids)}/{len(unit_concepts)} concepts matched; "
            f"skipping dual-signal",
            file=sys.stderr,
        )

    all_ids = det_ids | dual_ids
    if trace is not None:
        trace["selected_candidate_ids"] = sorted(all_ids)
        trace["selected_candidate_count"] = len(all_ids)

    if not all_ids:
        return []

    return [r for r in registry_index if r["concept_id"] in all_ids]


def select_group_candidates(
    unit_groups: list[dict[str, Any]],
    registry_groups: list[dict[str, Any]],
    resolved_concepts: list[dict[str, Any]],
    trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pre-filter registry groups by concept overlap + dual-signal retrieval.

    Always includes groups with concept overlap (deterministic). When the
    registry has >50 groups, also applies BM25 + embedding + RRF dual-signal
    retrieval for semantic matches without concept overlap.
    """
    if trace is not None:
        trace.update({
            "kind": "group_candidate_selection",
            "unit_group_count": len(unit_groups),
            "registry_group_count": len(registry_groups),
            "small_registry_passthrough": False,
        })
    if not registry_groups or not unit_groups:
        if trace is not None:
            trace["selected_candidate_ids"] = [g.get("group_id", "") for g in registry_groups]
        return registry_groups

    if len(registry_groups) <= 50:
        if trace is not None:
            trace["small_registry_passthrough"] = True
            trace["selected_candidate_ids"] = [g.get("group_id", "") for g in registry_groups]
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
    dual_trace: dict[str, Any] = {}
    dual_ids = _dual_signal_select_groups(unit_groups, compact_groups, trace=dual_trace)
    if trace is not None:
        trace["concept_overlap_candidate_ids"] = sorted(overlap_ids)
        trace["dual_signal"] = dual_trace

    all_ids = overlap_ids | dual_ids

    if trace is not None:
        trace["selected_candidate_ids"] = sorted(all_ids)
        trace["selected_candidate_count"] = len(all_ids)

    if not all_ids:
        # Fallback: return groups matching by type
        unit_group_types = {g.get("group_type", "") for g in unit_groups}
        fallback = [
            rg for rg in registry_groups
            if rg.get("group_type", "") in unit_group_types
        ]
        if trace is not None:
            trace["fallback"] = "group_type"
            trace["selected_candidate_ids"] = [g.get("group_id", "") for g in fallback]
            trace["selected_candidate_count"] = len(fallback)
        return fallback

    return [rg for rg in registry_groups if rg["group_id"] in all_ids]
