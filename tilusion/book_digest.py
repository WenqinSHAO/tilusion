from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .backend import LLMBackend, sha256_json
from .book_registry import BookRegistry
from .pass_utils import PromptComposition, PromptPart, build_pass_cache_key, load_static_prompt_part

BOOK_DIGEST_PROMPT_VERSION = "book-digest-v0.2"
BOOK_DIGEST_PROMPT_RESOURCE = "prompt_book_digest_v0.1.md"

MAX_ENTITIES_IN_DIGEST = 50


def build_book_digest(
    backend: LLMBackend,
    registry: BookRegistry,
    unit_id: str,
    *,
    previous_digest: str | None = None,
    cache_dir: str | Path = ".tilusion_cache/book_digests",
    use_cache: bool = True,
) -> str | None:
    """Generate a book context digest from the current registry state.

    Returns the digest as a markdown string, or ``None`` if the registry
    has no concepts (first unit). If the LLM call fails, logs a warning
    and returns ``None`` — the caller should proceed with no context.

    The digest starts minimal: an entity table with known concepts and
    their summaries. Prose sections (book-level understanding, active
    threads, extraction guidance) are deferred.
    """
    if not registry.has_concepts():
        return None

    prompt = _build_digest_composition()
    payload = _build_digest_payload(registry, unit_id, previous_digest)
    model_identity = backend.model_identity

    cache_key = build_pass_cache_key(
        pass_name="book-digest",
        prompt=prompt,
        user_payload=payload,
        model_identity=model_identity,
    )

    cache_path = Path(cache_dir) / cache_key / "result.json"
    if use_cache and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data.get("digest")

    try:
        conversation = backend.start_conversation(
            system_prompt=prompt.content,
            user_payload=payload,
            pass_name="book-digest",
        )
        raw = _last_assistant_content(conversation)
        data = json.loads(raw)
        digest = data.get("digest", "") if isinstance(data, dict) else ""
    except Exception as exc:
        print(
            f"  [book-digest] WARNING: digest generation failed ({exc}); "
            f"proceeding without context",
            file=sys.stderr,
        )
        return None

    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return digest if digest else None


def _build_digest_composition() -> PromptComposition:
    part = load_static_prompt_part(
        "book-digest-contract",
        role="static_task_contract",
        resource_name=BOOK_DIGEST_PROMPT_RESOURCE,
        metadata={
            "prompt_version": BOOK_DIGEST_PROMPT_VERSION,
        },
    )
    return PromptComposition(composition_id=BOOK_DIGEST_PROMPT_VERSION, parts=[part])


def _build_digest_payload(
    registry: BookRegistry,
    unit_id: str,
    previous_digest: str | None,
) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    # Sort by concept_id so the table is stable across runs
    sorted_concepts = sorted(
        registry._concepts.values(), key=lambda c: c.concept_id
    )
    for concept in sorted_concepts[:MAX_ENTITIES_IN_DIGEST]:
        entities.append({
            "name": concept.canonical_name or concept.surface,
            "type": concept.concept_type,
            "summary": concept.summary,
            "aliases": list(concept.aliases),
        })

    total = len(registry._concepts)
    omitted = total - len(entities) if total > MAX_ENTITIES_IN_DIGEST else 0

    payload: dict[str, Any] = {
        "task": "book_digest",
        "unit_id": unit_id,
        "entities": entities,
        "total_entities": total,
        "omitted_entities": omitted,
    }
    if previous_digest:
        payload["previous_digest"] = previous_digest

    return payload


def _last_assistant_content(conversation: Any) -> str:
    for msg in reversed(conversation.messages):
        if msg.get("role") == "assistant":
            return str(msg.get("content", ""))
    return ""


def make_context_dict(digest: str | None) -> dict[str, Any]:
    """Wrap a digest string into the context dict shape expected by the pipeline.

    When *digest* is ``None`` or empty, returns an empty dict so the
    pipeline's ``context`` parameter behaves as if no context was injected.
    """
    if digest:
        return {"digest": digest}
    return {}
