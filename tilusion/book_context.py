from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .extraction import sha256_json, sha256_text


BOOK_CONTEXT_SCHEMA_VERSION = "book-context-v0.1"
CONTEXT_SELECTION_POLICY = "passive-context-v0.1"


def stable_book_id(book_path: str | Path) -> str:
    """Return a deterministic local cache id for a book path.

    This is intentionally local-path based for the first passive scaffold. It
    avoids hashing large book files and can be replaced later by a stronger
    content/index identity without changing the artifact layout.
    """
    normalized = str(Path(book_path).expanduser().resolve())
    return f"book-{sha256_text(normalized)[:16]}"


def book_cache_dir(cache_root: str | Path, book_id: str) -> Path:
    return Path(cache_root) / "books" / book_id


def context_pack_dir(cache_root: str | Path, book_id: str, unit_id: str) -> Path:
    return book_cache_dir(cache_root, book_id) / "context_packs" / unit_id


def build_empty_book_state_snapshot(book_path: str | Path) -> dict[str, Any]:
    book_id = stable_book_id(book_path)
    base: dict[str, Any] = {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "book_id": book_id,
        "source": {
            "book_path": str(book_path),
            "identity_strategy": "local_resolved_path_v0",
        },
        "registry": {
            "entities": [],
            "locations": [],
            "threads": [],
            "events": [],
            "time_anchors": [],
            "timelines": [],
        },
        "indices": {
            "surfaces": {},
            "aliases": {},
            "active_threads": [],
        },
        "transactions": [],
    }
    snapshot_hash = sha256_json(base)
    return {
        **base,
        "snapshot_id": f"snapshot-{snapshot_hash[:16]}",
        "snapshot_hash": snapshot_hash,
    }


def build_passive_context_pack(
    book_path: str | Path,
    unit_id: str,
    *,
    source_length: dict[str, int] | None = None,
) -> dict[str, Any]:
    snapshot = build_empty_book_state_snapshot(book_path)
    base: dict[str, Any] = {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "selection_policy": CONTEXT_SELECTION_POLICY,
        "book_id": snapshot["book_id"],
        "target_unit_id": unit_id,
        "book_state_snapshot": {
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_hash": snapshot["snapshot_hash"],
        },
        "source_length": source_length or {},
        "prompt_injection": {
            "enabled": False,
            "reason": "passive scaffold only; not sent to LLM prompts yet",
        },
        "selection_summary": {
            "known_surface_hits": 0,
            "entities_included": 0,
            "locations_included": 0,
            "active_threads_included": 0,
            "recent_events_included": 0,
            "time_anchors_included": 0,
            "arc_summaries_included": 0,
            "excluded_counts": {},
        },
        "context": {
            "entities": [],
            "locations": [],
            "active_threads": [],
            "recent_events": [],
            "landmark_events": [],
            "time_anchors": [],
            "arc_summaries": [],
        },
        "selection_reasons": [],
    }
    context_pack_hash = sha256_json(base)
    return {
        **base,
        "context_pack_id": f"context-pack-{context_pack_hash[:16]}",
        "context_pack_hash": context_pack_hash,
    }


def build_context_selection_report(context_pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": BOOK_CONTEXT_SCHEMA_VERSION,
        "selection_policy": context_pack.get("selection_policy"),
        "book_id": context_pack.get("book_id"),
        "target_unit_id": context_pack.get("target_unit_id"),
        "context_pack_id": context_pack.get("context_pack_id"),
        "context_pack_hash": context_pack.get("context_pack_hash"),
        "prompt_injection": context_pack.get("prompt_injection", {}),
        "summary": context_pack.get("selection_summary", {}),
        "selection_reasons": context_pack.get("selection_reasons", []),
        "notes": [
            "Passive scaffold: no prior book context is selected yet.",
            "Future selector reports should explain every included registry record.",
        ],
    }


def write_passive_context_artifacts(
    *,
    book_path: str | Path,
    unit_id: str,
    cache_root: str | Path,
    source_length: dict[str, int] | None = None,
) -> dict[str, str]:
    snapshot = build_empty_book_state_snapshot(book_path)
    context_pack = build_passive_context_pack(
        book_path,
        unit_id,
        source_length=source_length,
    )
    selection_report = build_context_selection_report(context_pack)

    root = book_cache_dir(cache_root, snapshot["book_id"])
    snapshot_dir = root / "registry" / "snapshots"
    pack_dir = context_pack_dir(cache_root, snapshot["book_id"], unit_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshot_dir / f"{snapshot['snapshot_id']}.json"
    latest_path = root / "registry" / "latest.json"
    context_pack_path = pack_dir / "context_pack.json"
    selection_report_path = pack_dir / "context_selection_report.json"

    _write_json(snapshot_path, snapshot)
    _write_json(latest_path, snapshot)
    _write_json(context_pack_path, context_pack)
    _write_json(selection_report_path, selection_report)

    return {
        "book_state_snapshot": str(snapshot_path),
        "book_state_latest": str(latest_path),
        "context_pack": str(context_pack_path),
        "context_selection_report": str(selection_report_path),
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
