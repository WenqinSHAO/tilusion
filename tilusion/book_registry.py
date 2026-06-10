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


GENERIC_IDENTITY_FORMS = frozenset({
    "余", "吾", "我", "予", "作者", "叙述者",
    "先生", "夫人", "妻", "妻子", "夫", "丈夫", "友人", "主人",
    "person", "author", "narrator", "wife", "husband", "friend",
})


def _usable_identity_forms(values: list[str]) -> set[str]:
    """Return non-generic surface forms suitable as identity evidence."""
    forms: set[str] = set()
    for value in values:
        form = str(value or "").strip()
        if form and form not in GENERIC_IDENTITY_FORMS:
            forms.add(form)
    return forms


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
    def merge(
        members: list[Concept],
        *,
        merge_stats: MergeStats | None = None,
    ) -> Concept:
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
            surface=members[0].surface,
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
        # Prefer the first member's canonical_name (first-write-wins for
        # registry stability in cross-unit merges).  If the first member
        # has no cname, fall back to the first non-empty cname from any
        # member (typical for within-unit merges where one duplicate
        # carries a cname and the other doesn't).
        if members[0].canonical_name:
            return members[0].canonical_name
        for m in members:
            if m.canonical_name:
                return m.canonical_name
        return ""

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


def find_registry_duplicates(
    concepts: dict[str, Concept],
) -> list[tuple[str, str, str]]:
    """Find deterministic duplicate pairs in a registry concept dict.

    Returns a list of ``(id_a, id_b, reason)`` tuples where *id_a* is the
    older (lower-numbered) concept that should absorb *id_b*.

    Rules (both must be satisfied):
    1. Same normalized type
    2. Same surface, OR shared alias between the two concepts
    """
    # Group by normalized type
    by_type: dict[str, list[tuple[str, Concept]]] = {}
    for cid, c in concepts.items():
        ntype = normalize_concept_type(c.concept_type)
        by_type.setdefault(ntype, []).append((cid, c))

    pairs: list[tuple[str, str, str]] = []
    seen: set[frozenset[str]] = set()

    for _ntype, group in by_type.items():
        if len(group) < 2:
            continue
        # Build surface index: surface → list of (id, concept)
        by_surface: dict[str, list[tuple[str, Concept]]] = {}
        for cid, c in group:
            key = c.surface.strip()
            if key:
                by_surface.setdefault(key, []).append((cid, c))

        for _surf, matches in by_surface.items():
            if len(matches) < 2:
                continue
            for i in range(len(matches)):
                for j in range(i + 1, len(matches)):
                    id_a, id_b = matches[i][0], matches[j][0]
                    pair = frozenset({id_a, id_b})
                    if pair in seen:
                        continue
                    seen.add(pair)
                    pairs.append((id_a, id_b, f"same surface '{_surf}'"))

        # Alias overlap check: for each pair in the type group
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                id_a, c_a = group[i]
                id_b, c_b = group[j]
                pair = frozenset({id_a, id_b})
                if pair in seen:
                    continue
                aliases_a = _usable_identity_forms(list(c_a.aliases or []))
                aliases_b = _usable_identity_forms(list(c_b.aliases or []))
                shared = aliases_a & aliases_b
                if shared:
                    seen.add(pair)
                    pairs.append((id_a, id_b, f"shared alias {sorted(shared)}"))

    # Sort: older (lower-numbered) ID absorbs newer
    def _id_num(cid: str) -> int:
        try:
            return int(cid.split("-")[-1])
        except (ValueError, IndexError):
            return 0

    result: list[tuple[str, str, str]] = []
    for id_a, id_b, reason in pairs:
        na, nb = _id_num(id_a), _id_num(id_b)
        if na <= nb:
            result.append((id_a, id_b, reason))
        else:
            result.append((id_b, id_a, reason))
    return result


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

    def merge_concepts(
        self,
        ids: list[str],
        *,
        merge_stats: MergeStats | None = None,
        merge_reason: str | None = None,
    ) -> str:
        ids = list(dict.fromkeys(ids))  # dedup preserving order
        if len(ids) < 2:
            raise ValueError("merge_concepts requires at least two distinct concept IDs")

        members = []
        for cid in ids:
            c = self._concepts.get(cid)
            if c is None:
                raise KeyError(f"concept {cid} not found")
            members.append(c)

        rejection = _check_merge_boundary(
            members, stats=merge_stats, merge_reason=merge_reason
        )
        if rejection is not None:
            raise MergeRejectedError(rejection)

        merged = DeterministicConceptMerger.merge(members, merge_stats=merge_stats)
        # Keep the first ID as the stable target (typically a book concept
        # that other operations may reference). Source IDs are absorbed.
        target_id = ids[0]
        source_ids = ids[1:]
        merged = Concept(
            concept_id=target_id,
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

        # Remove all members from indices, then re-add the merged target
        for m in members:
            self._remove_from_indices(m)
        for cid in source_ids:
            self._concepts.pop(cid)
        self._concepts[target_id] = merged
        self._add_to_indices(merged)

        return target_id

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

    @property
    def embedding_cache_dir(self) -> Path:
        """Directory for persisted concept/group embeddings."""
        return self._cache_dir / "embeddings"

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


# ── Merge observability ──────────────────────────────────────────────────────


@dataclass
class MergeStats:
    """Counters collected during a single cross-unit delta application.

    Incremented by _check_merge_boundary and the dedup pass.  Logged at the
    end of each book-scope pipeline run.
    """

    # Accepted
    accepted_same_surface: int = 0
    accepted_shared_cname: int = 0
    accepted_usable_alias: int = 0
    accepted_soft_type_bridge: int = 0
    accepted_llm_link: int = 0

    # Rejected
    rejected_no_identity: int = 0
    rejected_hard_boundary: int = 0
    rejected_generic_alias_only: int = 0
    rejected_type_mismatch: int = 0
    rejected_type_mismatch_no_facets: int = 0

    # Dedup
    dedup_found: int = 0
    dedup_accepted: int = 0
    dedup_skipped: int = 0

    # Soft-type bridge details: (type_a, type_b, shared_facets)
    soft_type_bridges: list[tuple[str, str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": {
                "same_surface": self.accepted_same_surface,
                "shared_cname": self.accepted_shared_cname,
                "usable_alias": self.accepted_usable_alias,
                "soft_type_bridge": self.accepted_soft_type_bridge,
                "llm_link": self.accepted_llm_link,
            },
            "rejected": {
                "no_identity": self.rejected_no_identity,
                "hard_boundary": self.rejected_hard_boundary,
                "generic_alias_only": self.rejected_generic_alias_only,
                "type_mismatch": self.rejected_type_mismatch,
                "type_mismatch_no_facets": self.rejected_type_mismatch_no_facets,
            },
            "dedup": {
                "found": self.dedup_found,
                "accepted": self.dedup_accepted,
                "skipped": self.dedup_skipped,
            },
            "soft_type_bridges": [
                {"type_a": a, "type_b": b, "shared_facets": f}
                for a, b, f in self.soft_type_bridges
            ],
        }


def _log_merge_stats(stats: MergeStats) -> None:
    """Print a one-line merge summary to stderr."""
    import sys

    a = stats.accepted_same_surface + stats.accepted_shared_cname + \
        stats.accepted_usable_alias + stats.accepted_soft_type_bridge + \
        stats.accepted_llm_link
    r = stats.rejected_no_identity + stats.rejected_hard_boundary + \
        stats.rejected_generic_alias_only + stats.rejected_type_mismatch + \
        stats.rejected_type_mismatch_no_facets
    print(
        f"  [merge-stats] accepted={a} "
        f"(surface={stats.accepted_same_surface} "
        f"cname={stats.accepted_shared_cname} "
        f"alias={stats.accepted_usable_alias} "
        f"facet_bridge={stats.accepted_soft_type_bridge} "
        f"llm={stats.accepted_llm_link}) "
        f"rejected={r} "
        f"(no_id={stats.rejected_no_identity} "
        f"boundary={stats.rejected_hard_boundary} "
        f"generic_alias={stats.rejected_generic_alias_only} "
        f"type_mismatch={stats.rejected_type_mismatch} "
        f"no_facets={stats.rejected_type_mismatch_no_facets}) "
        f"dedup={stats.dedup_accepted}/{stats.dedup_found} "
        f"(skipped={stats.dedup_skipped})",
        file=sys.stderr,
    )


# ── Facet overlap weighting ───────────────────────────────────────────────────

# Facets that are merely type-class labels — too generic to bridge types
# in soft-type merges.  Role, domain, and continuity facets carry actual
# identity signal.
_GENERIC_FACETS: frozenset[str] = frozenset({
    "person", "place", "object", "term", "theme", "motif",
    "method", "time_anchor", "source", "event", "other",
})


# ── Merge boundary validation ────────────────────────────────────────────────


def _check_merge_boundary(
    members: list[Concept],
    *,
    stats: MergeStats | None = None,
    merge_reason: str | None = None,
) -> str | None:
    """Return rejection reason if the merge is unsafe, None if ok.

    Adapted from reading_pipeline._classify_merge_risk.
    """
    surfaces: set[str] = {m.surface for m in members}
    all_cnames: set[str] = {
        m.canonical_name for m in members if m.canonical_name
    }
    types: set[str] = {normalize_concept_type(m.concept_type) for m in members}

    # ── Identity signals ─────────────────────────────────────────────────
    # Same surface across all → probable duplicate extraction
    same_surface = len(surfaces) == 1

    # Shared canonical_name across multiple members
    _cname_counts = Counter(
        m.canonical_name for m in members if m.canonical_name
    )
    shared_cnames: set[str] = {
        cn for cn, n in _cname_counts.items() if n >= 2
    }
    shared_cname = len(shared_cnames) == 1 and all(
        m.canonical_name for m in members
    )

    # Surface or alias overlap
    has_surface_overlap = _surfaces_overlap(members)

    generic_form_overlap = _generic_forms_overlap(members)
    identity_signal = same_surface or shared_cname or has_surface_overlap
    if not identity_signal:
        reason = _make_rejection_reason(surfaces, types, shared_cnames)
        _log_rejected_merge(members, reason)
        if stats is not None:
            if generic_form_overlap:
                stats.rejected_generic_alias_only += 1
            else:
                stats.rejected_no_identity += 1
        return reason

    # ── Type compatibility ───────────────────────────────────────────────
    # Hard match: all members share the same normalized type
    if len(types) == 1:
        if stats is not None:
            if merge_reason == "llm_link_proposal":
                stats.accepted_llm_link += 1
            elif same_surface:
                stats.accepted_same_surface += 1
            elif shared_cname:
                stats.accepted_shared_cname += 1
            else:
                stats.accepted_usable_alias += 1
        return None

    # Hard boundary types never soft-bridge across concept types.
    if types & {"time_anchor", "place", "source"}:
        reason = _make_rejection_reason(surfaces, types, shared_cnames)
        _log_rejected_merge(members, reason)
        if stats is not None:
            stats.rejected_hard_boundary += 1
        return reason

    # Soft typing: different types but overlapping facets → compatible.
    # Identity-gated: we only reach here if identity_signal is already
    # true (same surface, shared cname, or alias overlap).
    # Require non-generic facet overlap for cross-type bridging.
    if _facets_overlap(members, require_specific=True):
        if stats is not None:
            if merge_reason == "llm_link_proposal":
                stats.accepted_llm_link += 1
            else:
                stats.accepted_soft_type_bridge += 1
            shared = _shared_facets(members, require_specific=True)
            type_a = sorted(types)[0] if len(types) >= 1 else "?"
            type_b = sorted(types)[1] if len(types) >= 2 else "?"
            stats.soft_type_bridges.append((type_a, type_b, ", ".join(sorted(shared))))
        return None

    # ── Rejection path ───────────────────────────────────────────────────
    reason = _make_rejection_reason(surfaces, types, shared_cnames)
    _log_rejected_merge(members, reason)
    if stats is not None:
        if len(types) > 1:
            if _facets_overlap(members, require_specific=False):
                # Had class-only overlap but not specific
                stats.rejected_type_mismatch += 1
            else:
                stats.rejected_type_mismatch_no_facets += 1
        elif generic_form_overlap:
            stats.rejected_generic_alias_only += 1
    return reason


def _collect_facet_sets(
    members: list[Concept], *, require_specific: bool = False
) -> list[set[str]]:
    """Return facet sets for each member, optionally filtered to non-generic."""
    result: list[set[str]] = []
    for m in members:
        fs = {f.strip().lower() for f in (m.facets or []) if f.strip()}
        if require_specific:
            fs = fs - _GENERIC_FACETS
        if fs:
            result.append(fs)
    return result


def _facets_overlap(
    members: list[Concept], *, require_specific: bool = False
) -> bool:
    """True if any two members share at least one facet (case-insensitive).

    When *require_specific* is True, class-only facets (person, place,
    object, etc.) are ignored — only role, domain, or continuity facets
    count.  Used for soft-type bridging across different concept types.
    """
    facet_sets = _collect_facet_sets(members, require_specific=require_specific)
    if len(facet_sets) < 2:
        return False
    for i in range(len(facet_sets)):
        for j in range(i + 1, len(facet_sets)):
            if facet_sets[i] & facet_sets[j]:
                return True
    return False


def _shared_facets(
    members: list[Concept], *, require_specific: bool = False
) -> set[str]:
    """Return facets shared by at least two members (case-insensitive)."""
    facet_sets = _collect_facet_sets(members, require_specific=require_specific)
    shared: set[str] = set()
    for i in range(len(facet_sets)):
        for j in range(i + 1, len(facet_sets)):
            shared |= facet_sets[i] & facet_sets[j]
    return shared


def _generic_forms_overlap(members: list[Concept]) -> bool:
    """True if members overlap only through generic identity forms."""
    per_concept: list[set[str]] = []
    for m in members:
        forms = {str(v or "").strip() for v in [m.surface, *m.aliases, *m.observed_surfaces]}
        per_concept.append({f for f in forms if f in GENERIC_IDENTITY_FORMS})
    for i in range(len(per_concept)):
        for j in range(i + 1, len(per_concept)):
            if per_concept[i] & per_concept[j]:
                return True
    return False


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
        per_concept.append(_usable_identity_forms(
            [m.surface, *m.aliases, *m.observed_surfaces]
        ))
    for i in range(len(per_concept)):
        for j in range(i + 1, len(per_concept)):
            if per_concept[i] & per_concept[j]:
                return True
    return False
