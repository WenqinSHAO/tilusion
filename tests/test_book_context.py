from __future__ import annotations

import json
from pathlib import Path

from tilusion.book_context import (
    BOOK_CONTEXT_SCHEMA_VERSION,
    build_book_state_snapshot,
    build_compact_context_from_scan,
    build_empty_book_state_snapshot,
    build_passive_context_pack,
    build_registry_from_packages,
    scan_unit_text_for_surfaces,
    stable_book_id,
    write_passive_context_artifacts,
)
from tilusion.extraction import sha256_json


def test_stable_book_id_is_deterministic_for_local_path(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\n", encoding="utf-8")

    assert stable_book_id(book) == stable_book_id(book)
    assert stable_book_id(book).startswith("book-")
    assert stable_book_id(book) != stable_book_id(tmp_path / "other.txt")


def test_empty_book_state_snapshot_is_hash_addressed(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    snapshot = build_empty_book_state_snapshot(book)
    snapshot_hash = snapshot["snapshot_hash"]
    base = {k: v for k, v in snapshot.items() if k not in {"snapshot_id", "snapshot_hash"}}

    assert snapshot["schema_version"] == BOOK_CONTEXT_SCHEMA_VERSION
    assert snapshot["snapshot_id"] == f"snapshot-{snapshot_hash[:16]}"
    assert snapshot_hash == sha256_json(base)
    assert snapshot["registry"]["entities"] == []
    assert snapshot["indices"]["surfaces"] == {}


def test_passive_context_pack_is_not_prompt_injected(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    pack = build_passive_context_pack(book, "unit-0002", source_length={"chars": 12})
    pack_hash = pack["context_pack_hash"]
    base = {k: v for k, v in pack.items() if k not in {"context_pack_id", "context_pack_hash"}}

    assert pack["context_pack_id"] == f"context-pack-{pack_hash[:16]}"
    assert pack_hash == sha256_json(base)
    assert pack["prompt_injection"]["enabled"] is False
    assert pack["context"]["entities"] == []
    assert pack["selection_summary"]["known_surface_hits"] == 0


def test_write_passive_context_artifacts(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\n", encoding="utf-8")
    paths = write_passive_context_artifacts(
        book_path=book,
        unit_id="unit-0002",
        cache_root=tmp_path / "cache",
        source_length={"chars": 10},
    )

    for path in paths.values():
        assert Path(path).exists()

    context_pack = json.loads(Path(paths["context_pack"]).read_text(encoding="utf-8"))
    report = json.loads(Path(paths["context_selection_report"]).read_text(encoding="utf-8"))
    latest = json.loads(Path(paths["book_state_latest"]).read_text(encoding="utf-8"))

    assert context_pack["target_unit_id"] == "unit-0002"
    assert context_pack["source_length"] == {"chars": 10}
    assert report["context_pack_hash"] == context_pack["context_pack_hash"]
    assert latest["snapshot_hash"] == context_pack["book_state_snapshot"]["snapshot_hash"]


def test_run_all_writes_passive_book_context_artifacts(tmp_path: Path) -> None:
    from tilusion.extraction import MockExtractionBackend
    from tilusion.extraction_pipeline import run_all_passes

    book = tmp_path / "book.txt"
    book.write_text("Chapter 1\nAlice left home.\n", encoding="utf-8")
    record = run_all_passes(
        book,
        "unit-0001",
        backend=MockExtractionBackend(),
        cache_dir=tmp_path / "cache",
    )

    package = json.loads(Path(record.unit_package_path).read_text(encoding="utf-8"))
    book_context = package["book_context"]

    assert book_context["enabled"] is True
    assert book_context["prompt_injection"]["enabled"] is False
    assert book_context["context_pack_id"].startswith("context-pack-")
    assert book_context["selection_policy"] == "passive-context-v0.1"
    for path in book_context["artifact_paths"].values():
        assert Path(path).exists()

    context_pack = json.loads(
        Path(book_context["artifact_paths"]["context_pack"]).read_text(encoding="utf-8")
    )
    assert context_pack["target_unit_id"] == "unit-0001"
    assert context_pack["context_pack_hash"] == book_context["context_pack_hash"]
    assert context_pack["source_length"]["chars"] == len("Chapter 1\nAlice left home.\n")


# ── registry building ──


def test_build_registry_from_unit_package(tmp_path: Path) -> None:
    package_path = tmp_path / "unit_package.json"
    package_path.write_text(
        json.dumps(
            {
                "unit_id": "unit-0002",
                "data": {
                    "entity_records": [
                        {
                            "entity_id": "unit-entity-0001",
                            "canonical_name": "沈复",
                            "surfaces": ["余", "沈复", "沈三白"],
                            "kind": "person",
                            "summary": "叙述者",
                        },
                        {
                            "entity_id": "unit-entity-0002",
                            "canonical_name": "陈芸",
                            "surfaces": ["陈芸", "芸娘"],
                            "kind": "person",
                            "summary": "芸娘",
                        },
                    ],
                    "location_records": [
                        {
                            "location_id": "unit-location-0001",
                            "canonical_name": "苏州",
                            "surfaces": ["苏州"],
                            "kind": "physical",
                            "summary": "居住地",
                        }
                    ],
                    "thread_records": [
                        {
                            "thread_id": "unit-thread-0001",
                            "summary": "婚姻缔结",
                            "status": "advanced",
                        }
                    ],
                    "event_records": [
                        {
                            "event_id": "unit-event-0001",
                            "summary": "沈复出生",
                            "participant_entity_ids": ["unit-entity-0001"],
                        }
                    ],
                    "timelines": [
                        {
                            "timeline_id": "unit-timeline-0001",
                            "summary": "主线",
                            "confidence": "high",
                            "ordered_events": [],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registry = build_registry_from_packages([str(package_path)])
    assert len(registry["entities"]) == 2
    assert len(registry["locations"]) == 1
    assert len(registry["threads"]) == 1
    assert len(registry["events"]) == 1
    assert len(registry["timelines"]) == 1
    scoped_entity_id = "unit-0002:unit-entity-0001"
    assert registry["entities"][scoped_entity_id]["canonical_name"] == "沈复"
    assert registry["entities"][scoped_entity_id]["source_record_id"] == "unit-entity-0001"
    assert registry["entities"][scoped_entity_id]["source_unit"] == "unit-0002"
    assert "沈三白" in registry["entities"][scoped_entity_id]["surfaces"]




def test_build_registry_scopes_unit_local_ids_across_packages(tmp_path: Path) -> None:
    def write_package(path: Path, unit_id: str, name: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "unit_id": unit_id,
                    "data": {
                        "entity_records": [
                            {
                                "entity_id": "unit-entity-0001",
                                "canonical_name": name,
                                "surfaces": [name],
                                "kind": "person",
                            }
                        ],
                        "location_records": [],
                        "thread_records": [],
                        "event_records": [],
                        "timelines": [],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    first = tmp_path / "u1.json"
    second = tmp_path / "u2.json"
    write_package(first, "unit-0001", "甲")
    write_package(second, "unit-0002", "乙")

    registry = build_registry_from_packages([first, second])

    assert set(registry["entities"]) == {
        "unit-0001:unit-entity-0001",
        "unit-0002:unit-entity-0001",
    }
    assert registry["entities"]["unit-0001:unit-entity-0001"]["canonical_name"] == "甲"
    assert registry["entities"]["unit-0002:unit-entity-0001"]["canonical_name"] == "乙"


def test_populated_book_state_snapshot_hashes_registry_content(tmp_path: Path) -> None:
    book = tmp_path / "book.txt"
    registry = {
        "entities": {
            "unit-0001:unit-entity-0001": {
                "entity_id": "unit-0001:unit-entity-0001",
                "canonical_name": "沈复",
            }
        },
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    snapshot = build_book_state_snapshot(book, registry)
    base = {k: v for k, v in snapshot.items() if k not in {"snapshot_id", "snapshot_hash"}}

    assert snapshot["registry"]["entities"] == list(registry["entities"].values())
    assert snapshot["snapshot_hash"] == sha256_json(base)
    assert snapshot["snapshot_id"] == f"snapshot-{snapshot['snapshot_hash'][:16]}"


# ── surface scanner ──


def test_scan_finds_exact_surface_matches() -> None:
    registry = {
        "entities": {
            "unit-entity-0001": {
                "entity_id": "unit-entity-0001",
                "canonical_name": "沈复",
                "surfaces": ["沈复", "沈三白"],
                "kind": "person",
                "summary": "叙述者",
            }
        },
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    text = "作者沈复生于苏州。沈三白是其别号。"
    scan = scan_unit_text_for_surfaces(text, registry)
    assert scan["total_matches"] == 2
    assert len(scan["matched_records"]) == 1
    assert scan["matched_records"][0]["canonical_name"] == "沈复"
    assert scan["matched_records"][0]["match_count"] == 2


def test_scan_skips_single_char_surfaces() -> None:
    registry = {
        "entities": {
            "unit-entity-0001": {
                "entity_id": "unit-entity-0001",
                "canonical_name": "沈复",
                "surfaces": ["余"],
                "kind": "person",
                "summary": "叙述者",
            }
        },
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    text = "余忆童稚时，能张目对日。"
    scan = scan_unit_text_for_surfaces(text, registry)
    assert scan["total_matches"] == 0


def test_scan_marks_short_surfaces_as_ambiguous() -> None:
    registry = {
        "entities": {
            "unit-entity-0001": {
                "entity_id": "unit-entity-0001",
                "canonical_name": "沈复",
                "surfaces": ["沈复", "三白"],
                "kind": "person",
                "summary": "叙述者",
            }
        },
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    text = "沈三白即沈复。"
    scan = scan_unit_text_for_surfaces(text, registry)
    matched = scan["matched_records"][0]
    assert matched["match_count"] >= 1


def test_scan_resolves_overlapping_matches_by_preferring_longer_surface() -> None:
    registry = {
        "entities": {
            "short": {
                "entity_id": "short",
                "canonical_name": "浪亭",
                "surfaces": ["浪亭"],
                "kind": "physical",
                "summary": "short alias",
            },
            "long": {
                "entity_id": "long",
                "canonical_name": "沧浪亭",
                "surfaces": ["沧浪亭"],
                "kind": "physical",
                "summary": "园亭",
            },
        },
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    text = "间壁之沧浪亭中"
    scan = scan_unit_text_for_surfaces(text, registry)
    assert scan["total_matches"] == 1
    assert scan["matched_records"][0]["record_id"] == "long"


def test_scan_reports_unmatched_surfaces() -> None:
    registry = {
        "entities": {
            "unit-entity-0001": {
                "entity_id": "unit-entity-0001",
                "canonical_name": "沈复",
                "surfaces": ["沈复", "沈三白"],
                "kind": "person",
                "summary": "叙述者",
            },
            "unit-entity-0099": {
                "entity_id": "unit-entity-0099",
                "canonical_name": "未知人物",
                "surfaces": ["未知人物"],
                "kind": "person",
                "summary": "unknown",
            },
        },
        "locations": {},
        "threads": {},
        "events": {},
        "timelines": {},
    }
    text = "沈复来到苏州。"
    scan = scan_unit_text_for_surfaces(text, registry)
    unmatched_entity_ids = {r["record_id"] for r in scan["surfaces_not_matched"] if r["record_type"] == "entity"}
    assert "unit-entity-0099" in unmatched_entity_ids


# ── compact context builder ──


def test_build_compact_context_from_scan() -> None:
    registry = {
        "entities": {
            "unit-entity-0001": {
                "entity_id": "unit-entity-0001",
                "canonical_name": "沈复",
                "surfaces": ["沈复", "沈三白"],
                "kind": "person",
                "summary": "叙述者",
            }
        },
        "locations": {
            "unit-location-0001": {
                "location_id": "unit-location-0001",
                "canonical_name": "苏州",
                "surfaces": ["苏州"],
                "kind": "physical",
                "summary": "城市",
            }
        },
        "threads": {
            "unit-thread-0001": {
                "thread_id": "unit-thread-0001",
                "summary": "主线",
                "status": "advanced",
            }
        },
        "events": {},
        "timelines": {},
    }
    scan = {
        "matched_records": [
            {
                "record_id": "unit-entity-0001",
                "record_type": "entity",
                "canonical_name": "沈复",
                "matched_surfaces": [{"surface": "沈复", "start_char": 0, "end_char": 2}],
                "match_count": 1,
                "ambiguous_short_count": 0,
            },
            {
                "record_id": "unit-location-0001",
                "record_type": "location",
                "canonical_name": "苏州",
                "matched_surfaces": [{"surface": "苏州", "start_char": 10, "end_char": 12}],
                "match_count": 1,
                "ambiguous_short_count": 0,
            },
        ],
        "surfaces_not_matched": [],
    }
    ctx = build_compact_context_from_scan(scan, registry)
    assert len(ctx["entities"]) == 1
    assert ctx["entities"][0]["canonical_name"] == "沈复"
    assert len(ctx["locations"]) == 1
    assert len(ctx["active_threads"]) == 1
    assert ctx["active_threads"][0]["summary"] == "主线"
