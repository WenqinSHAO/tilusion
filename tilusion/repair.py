from __future__ import annotations

import re
from typing import Any, Callable

from .reading_schema import READING_UNIT_SCHEMA_VERSION

# Error codes that the auto-fixer can correct mechanically (no LLM).
# Each entry maps error_code → fix function.
#
# Fix functions receive (data, path, issue_dict) where:
#   - data: the full validated data dict (mutated in place)
#   - path: the dot/bracket path to the problematic field
#   - issue_dict: {"code", "path", "message", "repair_hint", "severity"}
#
# Returns True if a fix was applied, False otherwise.


def _fix_invalid_ref(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Remove an invalid/unknown ref from a list field."""
    parent_path, index = _parse_index_path(path)
    if parent_path is None or index is None:
        return False
    parent = _resolve_path(data, parent_path)
    if not isinstance(parent, list):
        return False
    if 0 <= index < len(parent):
        del parent[index]
        return True
    return False


def _fix_empty_string_list_item(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Filter out empty/whitespace-only strings from a list."""
    parent_path, index = _parse_index_path(path)
    if parent_path is None or index is None:
        return False
    parent = _resolve_path(data, parent_path)
    if not isinstance(parent, list):
        return False
    if 0 <= index < len(parent) and isinstance(parent[index], str) and not parent[index].strip():
        del parent[index]
        return True
    return False


def _fix_duplicate_object_id(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Append a dedup suffix to a duplicate id field."""
    parent_path, key = _parse_dot_path(path)
    if parent_path is None or key is None:
        return False
    parent = _resolve_path(data, parent_path)
    if not isinstance(parent, dict) or key not in parent:
        return False
    original = str(parent[key])
    suffix = 2
    while True:
        candidate = f"{original}_dedup{suffix}"
        if not _id_exists(data, parent_path, key, candidate):
            parent[key] = candidate
            return True
        suffix += 1


def _fix_missing_required_field(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Insert a sensible default value for a missing required field."""
    parent_path, key = _parse_dot_path(path)
    if parent_path is None or key is None:
        return False
    parent = _resolve_path(data, parent_path)
    if not isinstance(parent, dict):
        return False
    if key in parent:
        return False
    default = _default_value(key, issue)
    if default is not None:
        parent[key] = default
        return True
    return False


def _fix_schema_version_mismatch(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Set schema_version to the current version."""
    if path == "schema_version" or path.endswith(".schema_version"):
        data["schema_version"] = READING_UNIT_SCHEMA_VERSION
        return True
    parent_path, key = _parse_dot_path(path)
    if parent_path is None or key is None:
        return False
    parent = _resolve_path(data, parent_path)
    if isinstance(parent, dict):
        parent[key] = READING_UNIT_SCHEMA_VERSION
        return True
    return False


def _fix_stale_core_field(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Remove a stale core field from the data."""
    if path in data:
        del data[path]
        return True
    return False


def _fix_wrong_field_type(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Coerce a field to the expected type when safe (list→list, str→str, etc.)."""
    parent_path, key = _parse_dot_path(path)
    if parent_path is None or key is None:
        return False
    parent = _resolve_path(data, parent_path)
    if not isinstance(parent, dict) or key not in parent:
        return False
    value = parent[key]
    message = issue.get("message", "").lower()

    # Coerce non-list to empty list for list fields (check before None→str)
    if ("must be list" in message or "must be a list" in message) and not isinstance(value, list):
        parent[key] = []
        return True
    # Coerce non-dict to empty dict for dict fields
    if ("must be dict" in message or "must be object" in message or "must be an object" in message) and not isinstance(value, dict):
        parent[key] = {}
        return True
    # Coerce non-str to empty string for string fields
    if ("must be str" in message or "must be string" in message or "must be a string" in message) and not isinstance(value, str):
        parent[key] = ""
        return True
    # Generic None→"" fallback
    if value is None:
        parent[key] = ""
        return True
    return False


# ── Registry ──

AUTO_FIXERS: dict[str, Callable[[dict[str, Any], str, dict[str, Any]], bool]] = {
    "invalid_ref": _fix_invalid_ref,
    "unknown_ref": _fix_invalid_ref,
    "empty_string_list_item": _fix_empty_string_list_item,
    "duplicate_object_id": _fix_duplicate_object_id,
    "missing_required_field": _fix_missing_required_field,
    "schema_version_mismatch": _fix_schema_version_mismatch,
    "stale_core_field": _fix_stale_core_field,
    "wrong_field_type": _fix_wrong_field_type,
}

# Error codes that should never be auto-fixed (require LLM repair)
NOT_AUTO_FIXABLE: set[str] = {
    "missing_source_block_refs",
    "invalid_grounding",
    "invalid_type_string",
    "prior_context_used_as_evidence",
    "empty_ref_list",
    "missing_source_grounded_evidence",
    "invalid_source_block_range",
    "source_block_range_length_mismatch",
    "source_block_round_trip_mismatch",
    "unit_id_mismatch",
    "unsupported_delta_operation",
    "destructive_auto_merge",
    "base_snapshot_mismatch",
}


class DeterministicAutoFixer:
    """Applies deterministic fixes to validation errors.

    Fixes are free (no LLM cost) and safe (only mechanical corrections).
    Errors that can't be auto-fixed are left for LLM repair.
    """

    def fix(
        self, data: dict[str, Any], issues: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Apply auto-fixes to *data* in place.

        Returns (fixed_codes, remaining_issues).
        """
        fixed_codes: list[str] = []
        remaining: list[dict[str, Any]] = []

        for issue in issues:
            code = issue.get("code", "")
            if code in NOT_AUTO_FIXABLE:
                remaining.append(issue)
                continue

            fixer = AUTO_FIXERS.get(code)
            if fixer is None:
                remaining.append(issue)
                continue

            try:
                applied = fixer(data, issue.get("path", ""), issue)
            except (IndexError, KeyError, TypeError, ValueError):
                applied = False

            if applied:
                fixed_codes.append(code)
            else:
                remaining.append(issue)

        return fixed_codes, remaining


# ── Path helpers ──


def _parse_index_path(path: str) -> tuple[str | None, int | None]:
    """Parse a path like 'concepts[3].source_block_refs[0]' into (parent_path, index).

    Returns ('concepts[3].source_block_refs', 0) for the example above.
    Returns (None, None) if no bracket index is found.
    """
    match = re.search(r"\[(\d+)\]$", path)
    if not match:
        return None, None
    index = int(match.group(1))
    parent_path = path[: match.start()]
    return parent_path, index


def _parse_dot_path(path: str) -> tuple[str | None, str | None]:
    """Parse a path like 'concepts[3].surface' into ('concepts[3]', 'surface').

    Returns (None, None) if no dot-separated key is found.
    """
    # Split on last dot not inside brackets
    last_dot = path.rfind(".")
    bracket_depth = 0
    for i in range(len(path) - 1, -1, -1):
        ch = path[i]
        if ch == "]":
            bracket_depth += 1
        elif ch == "[":
            bracket_depth -= 1
        elif ch == "." and bracket_depth == 0:
            return path[:i], path[i + 1 :]
    return None, None


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    """Navigate into *data* following *path* (e.g., 'concepts[0].surface').

    Returns the value at the path, or raises KeyError/IndexError/TypeError.
    """
    if not path:
        return data

    current: Any = data
    # Split on dots, then parse brackets within each segment
    segments = path.split(".")
    for segment in segments:
        # Split segment like "concepts[3]" into "concepts" and [3]
        bracket_match = re.match(r"^(\w+)((?:\[\d+\])*)$", segment)
        if bracket_match:
            key = bracket_match.group(1)
            current = current[key]
            indices_str = bracket_match.group(2)
            for idx_match in re.finditer(r"\[(\d+)\]", indices_str):
                idx = int(idx_match.group(1))
                current = current[idx]
        else:
            current = current[segment]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a value at *path* in *data*, creating intermediate containers as needed."""
    if not path:
        return

    parent_path, key = _parse_dot_path(path)
    if parent_path is not None and key is not None:
        parent = _resolve_path(data, parent_path)
        if isinstance(parent, dict):
            parent[key] = value
            return

    # Fallback: set directly on data
    data[path] = value


def _id_exists(data: dict[str, Any], parent_path: str, key: str, candidate: str) -> bool:
    """Check if *candidate* already exists as a value of *key* in sibling objects.

    Used to avoid generating duplicate ids via the dedup suffix approach.
    """
    # Walk all objects in the same collection to check for conflicts
    # parent_path is like 'concepts' (the list)
    collection = _resolve_path(data, parent_path) if parent_path else None
    if isinstance(collection, list):
        for item in collection:
            if isinstance(item, dict) and item.get(key) == candidate:
                return True
    return False


def _default_value(key: str, issue: dict[str, Any]) -> Any:
    """Return a sensible default for a missing required field."""
    message = issue.get("message", "").lower()
    # Type-based defaults from the validation message
    if "must be str" in message or "must be string" in message or "must be a string" in message:
        return ""
    if "must be list" in message or "must be a list" in message:
        return []
    if "must be dict" in message or "must be object" in message or "must be an object" in message:
        return {}
    if "must be int" in message:
        return 0
    # Field-name-based defaults
    if key == "schema_version":
        return READING_UNIT_SCHEMA_VERSION
    # Common required string fields
    if key in ("unit_id", "segment_id", "block_id", "concept_id", "item_id",
                "group_id", "delta_id", "base_snapshot_id", "summary", "surface",
                "text", "text_hash"):
        return ""
    if key in ("start", "end", "block_index"):
        return 0
    return None
