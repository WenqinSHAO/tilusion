from __future__ import annotations

import json
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .book_context import book_cache_dir, stable_book_id
from .reading_schema import (
    AtomicItem,
    Concept,
    LogicalGroup,
    normalize_concept_type,
)


@dataclass
class CollisionInfo:
    existing_concept_id: str
    match_reason: str  # "exact_match" | "surface_match" | "alias_match"
    match_details: dict[str, Any] = field(default_factory=dict)


class MergeRejectedError(ValueError):
    """Raised when merge_concepts determines the merge is unsafe."""


class DeterministicConceptMerger:
    """Deterministic N→1 concept merge extracted from _merge_concept_group.

    Rules are identical to reading_payloads._merge_concept_group:
    longest canonical_name, first nonempty summary, set-union of list
    fields, provenance tracking.
    """

    @staticmethod
    def merge(members: list[Concept]) -> Concept:
        if not members:
            raise ValueError("merge requires at least one concept")
        if len(members) == 1:
            return members[0]

        canonical = DeterministicConceptMerger._pick_canonical_name(members)

        groundings: set[str] = set()
        for m in members:
            groundings.add((m.provenance or {}).get("grounding", ""))
        grounding = (
            groundings.pop()
            if len(groundings) == 1 and "" not in groundings
            else "synthesis"
        )

        merged_from = [m.concept_id for m in members]

        return Concept(
            concept_id="",  # caller assigns the book-scope ID
            surface=canonical or members[0].surface,
            concept_type=members[0].concept_type,
            canonical_name=canonical or None,
            summary=DeterministicConceptMerger._merge_summaries(members),
            aliases=DeterministicConceptMerger._union(members, "aliases"),
            observed_surfaces=DeterministicConceptMerger._union(
                members, "observed_surfaces"
            ),
            source_block_refs=DeterministicConceptMerger._union(
                members, "source_block_refs"
            ),
            facets=DeterministicConceptMerger._union(members, "facets"),
            uncertainty=DeterministicConceptMerger._union(members, "uncertainty"),
            provenance={
                "grounding": grounding,
                "created_by": "deterministic",
                "merged_from": merged_from,
            },
        )

    @staticmethod
    def _pick_canonical_name(members: list[Concept]) -> str:
        candidates: set[str] = set()
        for m in members:
            if m.canonical_name:
                candidates.add(m.canonical_name)
        if not candidates:
            return ""
        return sorted(candidates, key=lambda n: (-len(n), n))[0]

    @staticmethod
    def _union(members: list[Concept], field: str) -> list[Any]:
        seen: set[str] = set()
        result: list[Any] = []
        for m in members:
            for v in getattr(m, field, []) or []:
                if v not in seen:
                    seen.add(v)
                    result.append(v)
        return result

    @staticmethod
    def _first_nonempty(members: list[Concept], field: str) -> str:
        for m in members:
            v = getattr(m, field, "")
            if v:
                return v
        return ""

    @staticmethod
    def _merge_summaries(members: list[Concept]) -> str:
        """Concatenate summaries with source-unit prefix when all are nonempty.

        When all members carry a non-empty summary, each is prefixed with its
        source unit (from provenance.source_unit) so the merged concept records
        how understanding evolved across units. Falls back to first-nonempty
        when any member lacks a summary.
        """
        if all(m.summary for m in members):
            parts: list[str] = []
            for m in members:
                unit = (m.provenance or {}).get("source_unit", "?")
                parts.append(f"[{unit}]: {m.summary}")
            return "\n".join(parts)
        return DeterministicConceptMerger._first_nonempty(members, "summary")


class KeepExistingConceptMerger:
    """First-write-wins: return the first member unchanged."""

    @staticmethod
    def merge(members: list[Concept]) -> Concept:
        if not members:
            raise ValueError("merge requires at least one concept")
        return members[0]


class BookRegistry:
    """Book-level concept/item/group store with git-backed persistence."""

    def __init__(
        self,
        book_path: str | Path,
        cache_root: str | Path = ".tilusion_cache",
    ):
        self._book_path = Path(book_path)
        self._book_hash = stable_book_id(book_path)
        self._cache_dir = book_cache_dir(cache_root, self._book_hash)

        self._concepts: dict[str, Concept] = {}
        self._items: dict[str, dict[str, Any]] = {}
        self._groups: dict[str, dict[str, Any]] = {}
        self._metadata: dict[str, Any] = {}

        # (surface, normalized_type) → [concept_id, ...]
        self._surface_type_index: dict[tuple[str, str], list[str]] = {}
        # canonical_name → {concept_id, ...}
        self._canonical_name_index: dict[str, set[str]] = {}
        # surface (any form) → {concept_id, ...}
        self._surface_lookup: dict[str, set[str]] = {}

        self._next_concept_id: int = 1
        self._next_item_id: int = 1
        self._next_group_id: int = 1

    # ── Concept CRUD ──────────────────────────────────────────────────────

    def add_concept(
        self, concept: Concept, *, force: bool = False
    ) -> tuple[str, CollisionInfo | None]:
        if not force:
            collisions = self.find_collisions(concept)
            if collisions:
                return (collisions[0].existing_concept_id, collisions[0])

        new_id = self._alloc_concept_id()
        book_concept = Concept(
            concept_id=new_id,
            surface=concept.surface,
            concept_type=concept.concept_type,
            source_block_refs=list(concept.source_block_refs),
            canonical_name=concept.canonical_name,
            summary=concept.summary,
            aliases=list(concept.aliases),
            observed_surfaces=list(concept.observed_surfaces),
            facets=list(concept.facets),
            uncertainty=list(concept.uncertainty),
            provenance=dict(concept.provenance),
        )
        self._concepts[new_id] = book_concept
        self._add_to_indices(book_concept)
        return (new_id, None)

    def add_concepts(
        self, concepts: list[Concept]
    ) -> list[tuple[str, CollisionInfo | None]]:
        return [self.add_concept(c) for c in concepts]

    def get_concept(self, concept_id: str) -> Concept | None:
        return self._concepts.get(concept_id)

    def get_by_surface(self, surface: str) -> list[Concept]:
        ids = self._surface_lookup.get(surface, set())
        return [self._concepts[cid] for cid in ids if cid in self._concepts]

    def get_by_canonical_name(self, name: str) -> list[Concept]:
        ids = self._canonical_name_index.get(name, set())
        return [self._concepts[cid] for cid in ids if cid in self._concepts]

    # ── Collision detection ───────────────────────────────────────────────

    def find_collisions(self, concept: Concept) -> list[CollisionInfo]:
        normalized_type = normalize_concept_type(concept.concept_type)
        key = (concept.surface, normalized_type)
        surface_matches = set(self._surface_type_index.get(key, []))

        cname_matches: set[str] = set()
        if concept.canonical_name:
            cname_matches = self._canonical_name_index.get(
                concept.canonical_name, set()
            )

        all_ids = surface_matches | cname_matches
        if not all_ids:
            return []

        results: list[CollisionInfo] = []
        for cid in all_ids:
            in_surface = cid in surface_matches
            in_cname = cid in cname_matches
            if in_surface and in_cname:
                reason = "exact_match"
            elif in_surface:
                reason = "surface_match"
            else:
                reason = "alias_match"

            existing = self._concepts.get(cid)
            if existing is None:
                continue
            results.append(
                CollisionInfo(
                    existing_concept_id=cid,
                    match_reason=reason,
                    match_details={
                        "shared_surface": concept.surface,
                        "shared_type": normalized_type,
                        "shared_canonical_name": concept.canonical_name,
                    },
                )
            )
        return results

    # ── Concept merge ─────────────────────────────────────────────────────

    def merge_concepts(self, ids: list[str]) -> str:
        ids = list(dict.fromkeys(ids))  # dedup preserving order
        if len(ids) < 2:
            raise ValueError("merge_concepts requires at least two distinct concept IDs")

        members = []
        for cid in ids:
            c = self._concepts.get(cid)
            if c is None:
                raise KeyError(f"concept {cid} not found")
            members.append(c)

        rejection = _check_merge_boundary(members)
        if rejection is not None:
            raise MergeRejectedError(rejection)

        merged = DeterministicConceptMerger.merge(members)
        new_id = self._alloc_concept_id()
        merged = Concept(
            concept_id=new_id,
            surface=merged.surface,
            concept_type=merged.concept_type,
            source_block_refs=merged.source_block_refs,
            canonical_name=merged.canonical_name,
            summary=merged.summary,
            aliases=merged.aliases,
            observed_surfaces=merged.observed_surfaces,
            facets=merged.facets,
            uncertainty=merged.uncertainty,
            provenance=merged.provenance,
        )

        for cid in ids:
            old = self._concepts.pop(cid)
            self._remove_from_indices(old)
        self._concepts[new_id] = merged
        self._add_to_indices(merged)

        return new_id

    # ── Item CRUD ─────────────────────────────────────────────────────────

    def add_item(self, item: AtomicItem) -> str:
        item_id = f"item-{self._next_item_id:04d}"
        self._next_item_id += 1
        self._items[item_id] = item.to_dict()
        self._items[item_id]["item_id"] = item_id
        return item_id

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        return self._items.get(item_id)

    # ── Group CRUD ────────────────────────────────────────────────────────

    def add_group(self, group: LogicalGroup) -> str:
        group_id = f"group-{self._next_group_id:04d}"
        self._next_group_id += 1
        self._groups[group_id] = group.to_dict()
        self._groups[group_id]["group_id"] = group_id
        return group_id

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        return self._groups.get(group_id)

    # ── Introspection ──────────────────────────────────────────────────────

    @property
    def cache_dir(self) -> Path:
        """Directory containing this book registry's persisted files."""
        return self._cache_dir

    def has_concepts(self) -> bool:
        """Return True if the registry contains at least one concept."""
        return len(self._concepts) > 0

    def has_groups(self) -> bool:
        """Return True if the registry contains at least one group."""
        return len(self._groups) > 0

    def list_concepts(self) -> list[Concept]:
        """Return all concepts in the registry."""
        return list(self._concepts.values())

    def source_index_id(self) -> str:
        return str(self._metadata.get("source_index_id") or "")

    def ensure_source_index_id(self, source_index_id: str) -> None:
        if not source_index_id:
            raise ValueError("source_index_id is required for book registry updates")
        existing = self.source_index_id()
        if existing and existing != source_index_id:
            raise ValueError(
                "registry source_index_id mismatch: "
                f"registry={existing!r}, current={source_index_id!r}"
            )
        self._metadata["source_index_id"] = source_index_id

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, run_hash: str | None = None) -> str:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo()

        registry_path = self._registry_path()
        data = self._to_dict()
        with open(registry_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        subprocess.run(
            ["git", "add", "registry.json"],
            cwd=self._cache_dir,
            check=True,
            capture_output=True,
        )

        # Stage book-level state alongside registry so it is versioned together.
        for path in ("book_digest.json", "source_index.json", ".gitignore"):
            if (self._cache_dir / path).exists():
                subprocess.run(
                    ["git", "add", path],
                    cwd=self._cache_dir,
                    check=True,
                    capture_output=True,
                )

        # Only commit if there are staged changes
        diff_result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=self._cache_dir,
            capture_output=True,
        )
        if diff_result.returncode == 0:
            # No changes to commit
            return self._head_commit_hash()

        msg = (
            f"{len(self._concepts)} concepts, "
            f"{len(self._items)} items, "
            f"{len(self._groups)} groups"
        )
        if run_hash:
            msg = f"{msg} [{run_hash}]"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=self._cache_dir,
            check=True,
            capture_output=True,
        )
        return self._head_commit_hash()

    @classmethod
    def load(
        cls,
        book_path: str | Path,
        cache_root: str | Path = ".tilusion_cache",
    ) -> BookRegistry:
        book_hash = stable_book_id(book_path)
        cache_dir = book_cache_dir(cache_root, book_hash)
        registry_path = cache_dir / "registry.json"

        if not registry_path.exists():
            raise FileNotFoundError(
                f"No registry found at {registry_path}"
            )

        with open(registry_path) as f:
            data = json.load(f)

        registry = cls(book_path, cache_root)
        registry._from_dict(data)
        return registry

    @classmethod
    def load_or_init(
        cls,
        book_path: str | Path,
        cache_root: str | Path = ".tilusion_cache",
    ) -> BookRegistry:
        """Load existing registry or return a new empty one.

        Convenience for pipelines that don't know whether this is the first
        unit of a book extraction.
        """
        try:
            return cls.load(book_path, cache_root)
        except FileNotFoundError:
            return cls(book_path, cache_root)

    def rollback(self, commit_hash: str) -> None:
        self._ensure_git_repo()
        subprocess.run(
            ["git", "checkout", commit_hash, "--", "registry.json"],
            cwd=self._cache_dir,
            check=True,
            capture_output=True,
        )
        with open(self._registry_path()) as f:
            data = json.load(f)
        self._from_dict(data)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _alloc_concept_id(self) -> str:
        cid = f"concept-{self._next_concept_id:04d}"
        self._next_concept_id += 1
        return cid

    def _add_to_indices(self, concept: Concept) -> None:
        normalized_type = normalize_concept_type(concept.concept_type)
        key = (concept.surface, normalized_type)
        self._surface_type_index.setdefault(key, []).append(concept.concept_id)

        if concept.canonical_name:
            self._canonical_name_index.setdefault(
                concept.canonical_name, set()
            ).add(concept.concept_id)

        surfaces: set[str] = {concept.surface}
        surfaces.update(concept.aliases)
        surfaces.update(concept.observed_surfaces)
        for s in surfaces:
            self._surface_lookup.setdefault(s, set()).add(concept.concept_id)

    def _remove_from_indices(self, concept: Concept) -> None:
        normalized_type = normalize_concept_type(concept.concept_type)
        key = (concept.surface, normalized_type)
        lst = self._surface_type_index.get(key, [])
        if concept.concept_id in lst:
            lst.remove(concept.concept_id)
            if not lst:
                del self._surface_type_index[key]

        if concept.canonical_name:
            cset = self._canonical_name_index.get(concept.canonical_name, set())
            cset.discard(concept.concept_id)
            if not cset:
                del self._canonical_name_index[concept.canonical_name]

        surfaces: set[str] = {concept.surface}
        surfaces.update(concept.aliases)
        surfaces.update(concept.observed_surfaces)
        for s in surfaces:
            sset = self._surface_lookup.get(s, set())
            sset.discard(concept.concept_id)
            if not sset:
                del self._surface_lookup[s]

    def _registry_path(self) -> Path:
        return self._cache_dir / "registry.json"

    def head_commit_hash(self) -> str:
        git_dir = self._cache_dir / ".git"
        if not git_dir.exists():
            return ""
        try:
            return self._head_commit_hash()
        except subprocess.CalledProcessError:
            return ""

    def _ensure_git_repo(self) -> None:
        git_dir = self._cache_dir / ".git"
        if not git_dir.exists():
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init"],
                cwd=self._cache_dir,
                check=True,
                capture_output=True,
            )
        gitignore_path = self._cache_dir / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text("unit-*\ncross-unit/\nruns.json\n", encoding="utf-8")

    def _head_commit_hash(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=self._cache_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _to_dict(self) -> dict[str, Any]:
        return {
            "metadata": dict(self._metadata),
            "next_ids": {
                "concept": self._next_concept_id,
                "item": self._next_item_id,
                "group": self._next_group_id,
            },
            "concepts": {
                cid: c.to_dict() for cid, c in self._concepts.items()
            },
            "items": dict(self._items),
            "groups": dict(self._groups),
        }

    def _from_dict(self, data: dict[str, Any]) -> None:
        self._next_concept_id = data["next_ids"]["concept"]
        self._next_item_id = data["next_ids"]["item"]
        self._next_group_id = data["next_ids"]["group"]
        self._metadata = dict(data.get("metadata", {}))

        self._concepts.clear()
        self._items.clear()
        self._groups.clear()
        self._surface_type_index.clear()
        self._canonical_name_index.clear()
        self._surface_lookup.clear()

        for cid, cd in data.get("concepts", {}).items():
            self._concepts[cid] = Concept(**cd)
            self._add_to_indices(self._concepts[cid])

        self._items.update(data.get("items", {}))
        self._groups.update(data.get("groups", {}))


# ── Merge boundary validation ────────────────────────────────────────────────


def _check_merge_boundary(members: list[Concept]) -> str | None:
    """Return rejection reason if the merge is unsafe, None if ok.

    Adapted from reading_pipeline._classify_merge_risk.
    """
    surfaces: set[str] = {m.surface for m in members}
    all_cnames: set[str] = {
        m.canonical_name for m in members if m.canonical_name
    }
    types: set[str] = {normalize_concept_type(m.concept_type) for m in members}

    # Same type with a canonical_name on any side → identity established.
    # One member carrying a cname suffices: the LLM or auto-population may
    # have set it on only one side when the other came from a registry
    # entry added before cross-unit identity was resolved.
    if len(types) == 1 and len(all_cnames) >= 1:
        return None

    # Shared canonical_name across multiple members → identity established,
    # even across different types (e.g. person ↔ social_role).
    _cname_counts = Counter(
        m.canonical_name for m in members if m.canonical_name
    )
    shared_cnames: set[str] = {
        cn for cn, n in _cname_counts.items() if n >= 2
    }
    if len(shared_cnames) == 1 and all(m.canonical_name for m in members):
        return None

    # Same surface across all → probable duplicate extraction
    if len(surfaces) == 1:
        return None

    # Same type with surface or aliases overlap → safe
    if len(types) == 1:
        if _surfaces_overlap(members):
            return None

    # ── Rejection path: log detailed member info to stderr ──────────────
    reason = _make_rejection_reason(surfaces, types, shared_cnames)
    _log_rejected_merge(members, reason)
    return reason


def _make_rejection_reason(
    surfaces: set[str],
    types: set[str],
    shared_cnames: set[str],
) -> str:
    """Build a rejection reason string for the merge boundary check."""
    if "time_anchor" in types:
        return (
            "merge_rejected: merging distinct time_anchor concepts "
            "with different surfaces"
        )
    if "place" in types:
        return (
            "merge_rejected: merging distinct place concepts "
            "with different surfaces"
        )
    if "source" in types:
        return (
            "merge_rejected: merging distinct source concepts "
            "with different surfaces"
        )

    if len(types) > 1 and not shared_cnames:
        return (
            "merge_rejected: concepts have different types and no "
            "shared canonical name"
        )

    return (
        "merge_rejected: distinct surfaces and no shared "
        "identity signal"
    )


def _log_rejected_merge(members: list[Concept], reason: str) -> None:
    """Log detailed member info for a rejected merge to stderr."""
    import sys

    print(f"  [merge-reject] {reason}", file=sys.stderr)
    for i, m in enumerate(members):
        alias_preview = ", ".join(m.aliases[:5])
        if len(m.aliases) > 5:
            alias_preview += f", ... (+{len(m.aliases) - 5})"
        summary_preview = (m.summary or "")[:120]
        obs_preview = ", ".join(m.observed_surfaces[:5])
        if len(m.observed_surfaces) > 5:
            obs_preview += f", ... (+{len(m.observed_surfaces) - 5})"
        print(
            f"    member[{i}]: id={m.concept_id} type={m.concept_type} "
            f"surface=\"{m.surface}\" cname=\"{m.canonical_name}\"",
            file=sys.stderr,
        )
        if m.aliases:
            print(f"      aliases=[{alias_preview}]", file=sys.stderr)
        if summary_preview:
            print(f"      summary=\"{summary_preview}\"", file=sys.stderr)
        if m.observed_surfaces:
            print(f"      observed_surfaces=[{obs_preview}]", file=sys.stderr)

    return None


def _surfaces_overlap(members: list[Concept]) -> bool:
    """True if any two concepts share at least one surface form."""
    per_concept: list[set[str]] = []
    for m in members:
        s: set[str] = {m.surface}
        s.update(m.aliases)
        s.update(m.observed_surfaces)
        per_concept.append(s)
    for i in range(len(per_concept)):
        for j in range(i + 1, len(per_concept)):
            if per_concept[i] & per_concept[j]:
                return True
    return False
