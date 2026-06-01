from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .book_registry import BookRegistry

ToolExecutionContext = dict[str, Any]
"""Mutable context passed to tool handlers: registry, source_blocks, book_summary."""


@dataclass(slots=True)
class ToolDefinition:
    """Defines a registry tool the LLM can call."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the args object


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "get_concept": ToolDefinition(
        name="get_concept",
        description=(
            "Get the full Concept record for a registry concept_id. "
            "Use when the compact index suggests a potential match but "
            "you need the full record (all fields: canonical_name, "
            "summary, aliases, observed_surfaces, facets, provenance, "
            "source_block_refs, merged_from) to confirm identity."
        ),
        parameters={
            "type": "object",
            "properties": {
                "concept_id": {
                    "type": "string",
                    "description": "Registry concept ID from the compact index "
                    "(e.g., 'book-concept-0042').",
                },
            },
            "required": ["concept_id"],
        },
    ),
    "get_group": ToolDefinition(
        name="get_group",
        description=(
            "Get the full LogicalGroup record for a registry group_id. "
            "Use when the compact group index suggests potential "
            "continuation but you need the full structure (items, "
            "concepts, graph edges) to decide continue vs. mutate vs. "
            "new_thread."
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "Registry group ID (e.g., 'book-group-0017').",
                },
            },
            "required": ["group_id"],
        },
    ),
    "search_concepts": ToolDefinition(
        name="search_concepts",
        description=(
            "Semantic search over the FULL registry (not just shortlisted "
            "candidates). Use when you suspect a match exists but it "
            "wasn't in the shortlist. Craft queries using discriminative "
            "fields: canonical_name, observed_surfaces, or key phrases "
            "from the summary."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query using the most discriminative "
                    "fields you have (canonical name, key phrases).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    "search_groups": ToolDefinition(
        name="search_groups",
        description=(
            "Semantic search over the FULL registry groups. Use when "
            "a unit group sounds familiar but wasn't in the shortlisted "
            "candidates."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    "get_source_block": ToolDefinition(
        name="get_source_block",
        description=(
            "Get the full text and metadata for a source block "
            "referenced by a concept. Use to read the original passage "
            "to confirm identity in ambiguous cases."
        ),
        parameters={
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "string",
                    "description": "Source block ID (from concept.source_block_refs).",
                },
            },
            "required": ["block_id"],
        },
    ),
    "get_book_summary": ToolDefinition(
        name="get_book_summary",
        description=(
            "Get the book-level overview summary to provide domain "
            "context for interpreting concept surfaces."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    ),
}


def execute_tool_call(
    tool_call: dict[str, Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    """Execute a single tool call and return the result payload.

    Args:
        tool_call: ``{"action": "get_concept", "args": {"concept_id": "..."}}``
        context: Execution context with registry and optional source_blocks/book_summary.

    Returns:
        ``{"tool": "get_concept", "result": {...}}`` on success,
        ``{"tool": "get_concept", "error": "..."}`` on failure.
    """
    action = tool_call.get("action", "")
    args = tool_call.get("args", {})
    if not isinstance(args, dict):
        args = {}

    handler = _TOOL_HANDLERS.get(action)
    if handler is None:
        return {"tool": action, "error": f"Unknown tool action: {action!r}"}

    try:
        result = handler(args, context)
        return {"tool": action, "result": result}
    except Exception as exc:
        return {"tool": action, "error": str(exc)}


def render_tool_definitions_markdown(
    tool_names: list[str] | None = None,
) -> str:
    """Render tool definitions as a markdown section for the system prompt.

    Args:
        tool_names: Specific tools to include (``None`` = all).
    """
    names = tool_names or list(TOOL_DEFINITIONS.keys())
    parts: list[str] = [
        "## Available Tools\n",
        "You can call tools by including a `tool_calls` key in your "
        "JSON response. Each tool call has an `action` and `args.`\n",
        "When you call tools, the system will execute them and return "
        "results in the next turn. You will NOT include "
        "`resolution_proposals` keys in tool-calling turns.\n",
    ]
    for name in names:
        td = TOOL_DEFINITIONS.get(name)
        if td is None:
            continue
        parts.append(f"### {name}\n\n{td.description}\n\n")
        parts.append(
            f"**Parameters:**\n```json\n{json.dumps(td.parameters, indent=2)}\n```\n"
        )
    return "\n".join(parts)


# ── Tool handlers ───────────────────────────────────────────────────────────


def _handle_get_concept(args: dict, ctx: ToolExecutionContext) -> dict[str, Any]:
    registry: BookRegistry = ctx["registry"]
    concept_id = args.get("concept_id", "")
    if not concept_id:
        raise ValueError("concept_id is required")
    concept = registry.get_concept(concept_id)
    if concept is None:
        raise ValueError(f"Concept {concept_id!r} not found in registry")
    return concept.to_dict()


def _handle_get_group(args: dict, ctx: ToolExecutionContext) -> dict[str, Any]:
    registry: BookRegistry = ctx["registry"]
    group_id = args.get("group_id", "")
    if not group_id:
        raise ValueError("group_id is required")
    group = registry.get_group(group_id)
    if group is None:
        raise ValueError(f"Group {group_id!r} not found in registry")
    return group  # groups stored as dicts


def _handle_search_concepts(
    args: dict, ctx: ToolExecutionContext
) -> list[dict[str, Any]]:
    """Semantic search over all registry concepts in embedding space."""
    registry: BookRegistry = ctx["registry"]
    query = args.get("query", "")
    top_k = min(args.get("top_k", 10), 50)

    if not query or not registry.has_concepts():
        return []

    from .registry_index import BM25, _build_concept_text, _get_embedding_model

    concepts = registry.list_concepts()
    reg_index = _concepts_to_compact(concepts)
    reg_texts = [_build_concept_text(c) for c in reg_index]
    reg_ids = [c["concept_id"] for c in reg_index]

    model = _get_embedding_model()

    if model is not None:
        try:
            import numpy as np

            query_emb = model.encode(query, convert_to_numpy=True)
            reg_embeddings = model.encode(reg_texts, convert_to_numpy=True)
            reg_norms = np.linalg.norm(reg_embeddings, axis=1)
            query_norm = float(np.linalg.norm(query_emb))
            sims = (
                np.dot(reg_embeddings, query_emb) / (reg_norms * query_norm + 1e-8)
            )
            top_indices = np.argsort(sims)[::-1][:top_k]
            results = [
                reg_index[int(i)] for i in top_indices if float(sims[int(i)]) > 0.3
            ]
            return results
        except Exception:
            pass

    # Fallback to BM25
    bm25 = BM25(reg_texts)
    bm25_results = bm25.search(query, top_k=top_k)
    return [reg_index[idx] for idx, _ in bm25_results]


def _handle_search_groups(
    args: dict, ctx: ToolExecutionContext
) -> list[dict[str, Any]]:
    """Semantic search over all registry groups."""
    registry: BookRegistry = ctx["registry"]
    query = args.get("query", "")
    top_k = min(args.get("top_k", 10), 50)

    if not query or not registry._groups:
        return []

    from .registry_index import (  # noqa: F811
        BM25,
        _build_group_text,
        _get_embedding_model,
        build_group_index,
    )

    compact_groups = build_group_index(registry)
    reg_texts = [_build_group_text(cg) for cg in compact_groups]
    reg_ids = [cg.group_id for cg in compact_groups]

    model = _get_embedding_model()
    results: list[dict[str, Any]] = []

    if model is not None:
        try:
            import numpy as np

            query_emb = model.encode(query, convert_to_numpy=True)
            reg_embeddings = model.encode(reg_texts, convert_to_numpy=True)
            reg_norms = np.linalg.norm(reg_embeddings, axis=1)
            query_norm = float(np.linalg.norm(query_emb))
            sims = (
                np.dot(reg_embeddings, query_emb) / (reg_norms * query_norm + 1e-8)
            )
            top_indices = np.argsort(sims)[::-1][:top_k]
            for i in top_indices:
                if float(sims[int(i)]) > 0.3:
                    match_id = reg_ids[int(i)]
                    reg_group = registry.get_group(match_id)
                    if reg_group:
                        results.append(reg_group)
            if results:
                return results
        except Exception:
            pass

    bm25 = BM25(reg_texts)
    bm25_results = bm25.search(query, top_k=top_k)
    for idx, _ in bm25_results:
        match_id = reg_ids[idx]
        reg_group = registry.get_group(match_id)
        if reg_group:
            results.append(reg_group)
    return results


def _handle_get_source_block(
    args: dict, ctx: ToolExecutionContext
) -> dict[str, Any]:
    block_id = args.get("block_id", "")
    if not block_id:
        raise ValueError("block_id is required")
    source_blocks = ctx.get("source_blocks", [])
    for block in source_blocks:
        if block.get("block_id") == block_id:
            return block
    raise ValueError(f"Source block {block_id!r} not found")


def _handle_get_book_summary(
    args: dict, ctx: ToolExecutionContext
) -> str:
    return ctx.get("book_summary", "No book summary available.")


def _concepts_to_compact(
    concepts: list[Any],
) -> list[dict[str, Any]]:
    """Convert Concept objects to compact index dicts for search results."""
    index: list[dict[str, Any]] = []
    for concept in concepts:
        summary = getattr(concept, "summary", "") or ""
        if len(summary) > 200:
            summary = summary[:197] + "..."
        index.append({
            "concept_id": getattr(concept, "concept_id", ""),
            "canonical_name": getattr(concept, "canonical_name", "") or "",
            "concept_type": getattr(concept, "concept_type", "other") or "other",
            "summary": summary,
            "observed_surfaces": (getattr(concept, "observed_surfaces", None) or [])[:10],
        })
    return index


_TOOL_HANDLERS: dict[str, Callable] = {
    "get_concept": _handle_get_concept,
    "get_group": _handle_get_group,
    "search_concepts": _handle_search_concepts,
    "search_groups": _handle_search_groups,
    "get_source_block": _handle_get_source_block,
    "get_book_summary": _handle_get_book_summary,
}
