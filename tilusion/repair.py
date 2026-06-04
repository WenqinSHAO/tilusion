from __future__ import annotations

import json
import re
import sys
import time
from typing import Any, Callable, TYPE_CHECKING

from .reading_schema import READING_UNIT_SCHEMA_VERSION

if TYPE_CHECKING:
    from .backend import LLMBackend
    from .conversation import ConversationContext
    from .pass_utils import PromptComposition
    from .reading_validation import ReadingValidationReport

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


def _fix_wrong_string_list_item(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Coerce structured values in string-list fields to compact strings."""
    parent_path, index = _parse_index_path(path)
    if parent_path is None or index is None:
        return False
    field = parent_path.rsplit(".", 1)[-1]
    if field not in {
        "aliases",
        "observed_surfaces",
        "source_block_refs",
        "facets",
        "uncertainty",
        "concept_refs",
        "item_refs",
        "merged_from",
    }:
        return False
    parent = _resolve_path(data, parent_path)
    if not isinstance(parent, list) or not (0 <= index < len(parent)):
        return False
    value = parent[index]
    if isinstance(value, str):
        return False
    if isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = text.strip()
    if not text:
        del parent[index]
    else:
        parent[index] = text
    return True


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


def _fix_missing_source_block_refs(data: dict[str, Any], path: str, issue: dict[str, Any]) -> bool:
    """Inherit source_block_refs from merged source concepts.

    When a concept has merged_from refs, collect source_block_refs from
    those source concepts. This handles both deterministic merge concepts
    and LLM merge-delta concepts that lost their refs.

    Paths look like ``concepts[65].source_block_refs`` — the field is at
    the end, so locate the parent object then look up its merged_from refs
    in the same collection.
    """
    # path ends with ".source_block_refs" — find the parent object path
    dot_idx = path.rfind(".")
    if dot_idx == -1:
        return False
    field = path[dot_idx + 1:]
    if field not in ("source_block_refs",):
        return False

    concept_path = path[:dot_idx]  # e.g. "concepts[65]"
    concept = _resolve_path(data, concept_path)
    if not isinstance(concept, dict):
        return False

    merged_from = _as_list(concept.get("merged_from", []))
    if not merged_from:
        return False

    # Walk back to the collection (e.g. "concepts") to find source concepts
    list_path = concept_path.rsplit("[", 1)[0] if "[" in concept_path else ""
    concept_list = _resolve_path(data, list_path) if list_path else None
    if not isinstance(concept_list, list):
        return False

    inherited: list[str] = []
    seen: set[str] = set()
    for other in concept_list:
        if not isinstance(other, dict):
            continue
        if other.get("concept_id") in merged_from:
            for ref in _as_list(other.get("source_block_refs")):
                if ref not in seen:
                    seen.add(ref)
                    inherited.append(ref)

    if inherited:
        concept["source_block_refs"] = list(inherited)
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
    "wrong_item_type": _fix_wrong_string_list_item,
    "missing_source_block_refs": _fix_missing_source_block_refs,
}

# Error codes that should never be auto-fixed (require LLM repair)
NOT_AUTO_FIXABLE: set[str] = {
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


# ── Agentic repair loop ──

DEFAULT_MAX_REPAIR_TURNS = 3


def run_agentic_pass(
    backend: LLMBackend,
    prompt: PromptComposition,
    payload: dict[str, Any],
    validation_subject_builder: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_repair_turns: int = DEFAULT_MAX_REPAIR_TURNS,
    pass_name: str = "",
    return_subject: bool = False,
) -> tuple[dict[str, Any], ConversationContext, ReadingValidationReport]:
    """Run a pass with an agentic validation-repair loop.

    Turn 1: LLM call → parse → validate.
    If validation passes, return immediately.
    Otherwise: auto-fix → re-validate → compact LLM repair with KV-cache
    reuse → re-validate → loop. On exhaustion, full retry (new conversation).

    If *return_subject* is True, the first return value is the final
    validation subject (after post-processing and any auto-fixes/repairs).
    Otherwise it is the raw parsed LLM response.

    Returns ``(data, conversation, final_validation_report)``.
    """
    from .backend import parse_json_response
    from .reading_validation import validate_extraction_unit_package

    # Turn 1: initial LLM call
    conversation = backend.start_conversation(
        system_prompt=prompt.content,
        user_payload=payload,
        pass_name=pass_name,
    )
    assistant_response = _last_assistant_content(conversation)
    data = parse_json_response(assistant_response)

    validation_subject = validation_subject_builder(data)
    report = validate_extraction_unit_package(validation_subject)

    if report.passed:
        _update_conversation_validation(conversation, report)
        result = validation_subject if return_subject else data
        return result, conversation, report

    # Log initial failure before entering repair loop
    _log_validation_failure(report, 0, pass_name)

    # Enter repair loop
    data, conversation, report = _repair_loop(
        data=data,
        conversation=conversation,
        validation_subject_builder=validation_subject_builder,
        max_repair_turns=max_repair_turns,
        backend=backend,
        prompt=prompt,
        payload=payload,
        pass_name=pass_name,
        return_subject=return_subject,
    )
    return data, conversation, report


def _repair_loop(
    data: dict[str, Any],
    conversation: ConversationContext,
    validation_subject_builder: Callable[[dict[str, Any]], dict[str, Any]],
    max_repair_turns: int,
    backend: LLMBackend,
    prompt: PromptComposition,
    payload: dict[str, Any],
    pass_name: str,
    return_subject: bool = False,
) -> tuple[dict[str, Any], ConversationContext, ReadingValidationReport]:
    """Inner repair loop: auto-fix → LLM repair → re-validate → repeat."""
    from .backend import parse_json_response
    from .reading_validation import validate_extraction_unit_package

    fixer = DeterministicAutoFixer()
    repair_turns = 0

    while repair_turns <= max_repair_turns:
        # Build validation subject from current data and validate
        validation_subject = validation_subject_builder(data)
        report = validate_extraction_unit_package(validation_subject)

        if report.passed:
            _update_conversation_validation(conversation, report)
            result = validation_subject if return_subject else data
            return result, conversation, report

        # Log validation failure summary
        _log_validation_failure(report, repair_turns, pass_name)

        # Layer 1: deterministic auto-fix
        issues_dicts = [issue.to_dict() for issue in report.issues]
        fixed_codes, remaining_issues = fixer.fix(validation_subject, issues_dicts)

        if fixed_codes:
            _log_auto_fixes(fixed_codes, repair_turns, pass_name)
            # Propagate auto-fixes from validation_subject back to data
            _propagate_fixes(data, validation_subject)
            if not remaining_issues:
                # Re-validate to confirm all issues resolved
                final_subject = validation_subject_builder(data)
                final_report = validate_extraction_unit_package(final_subject)
                _update_conversation_validation(conversation, final_report)
                result = final_subject if return_subject else data
                print(
                    f"  {pass_name}: all errors auto-fixed, no LLM repair needed",
                    file=sys.stderr,
                )
                return result, conversation, final_report

        # Layer 2: compact LLM repair (if turns remain)
        if repair_turns >= max_repair_turns:
            break

        repair_turns += 1
        _log_llm_repair_start(remaining_issues, repair_turns, max_repair_turns, pass_name)

        repair_msg = build_repair_message(remaining_issues)
        conversation = backend.continue_conversation(conversation, repair_msg)
        repair_response = _last_assistant_content(conversation)

        try:
            repair_data = parse_json_response(repair_response)
            repairs = repair_data.get("repairs", [])
            if repairs:
                apply_repair_patch(validation_subject, repairs)
                _propagate_fixes(data, validation_subject)
                _log_repair_applied(repairs, pass_name)
            else:
                print(
                    f"  {pass_name}: LLM repair turn {repair_turns} returned no repairs",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"  {pass_name}: LLM repair turn {repair_turns} response unparseable: {exc}",
                file=sys.stderr,
            )

    # Layer 3: full retry (new conversation)
    print(
        f"  {pass_name}: max repair turns ({max_repair_turns}) exhausted, "
        f"starting full retry with new conversation",
        file=sys.stderr,
    )
    from .backend import parse_json_response
    from .reading_validation import validate_extraction_unit_package

    conversation = backend.start_conversation(
        system_prompt=prompt.content,
        user_payload=payload,
        pass_name=pass_name,
    )
    assistant_response = _last_assistant_content(conversation)
    data = parse_json_response(assistant_response)
    validation_subject = validation_subject_builder(data)
    report = validate_extraction_unit_package(validation_subject)
    _update_conversation_validation(conversation, report)

    if report.passed:
        print(
            f"  {pass_name}: full retry passed validation",
            file=sys.stderr,
        )
    else:
        _log_validation_failure(report, 0, f"{pass_name} (full retry)")

    result = validation_subject if return_subject else data
    return result, conversation, report


def build_repair_message(errors: list[dict[str, Any]]) -> str:
    """Build a compact repair message from remaining validation errors.

    The message is intentionally terse (~200 tokens) so that repair turns
    are cheap and KV-cache reuse pays off.
    """
    compact_errors = []
    for err in errors:
        compact_errors.append({
            "code": err.get("code", ""),
            "path": err.get("path", ""),
            "message": err.get("message", ""),
            "repair_hint": err.get("repair_hint", ""),
        })

    return json.dumps(
        {
            "task": "repair_extraction",
            "errors": compact_errors,
            "instruction": (
                "Return a JSON object with a 'repairs' array. Each repair has "
                "'path' (dot/bracket path to the field), 'operation' (replace/append/remove), "
                "and 'value'. Fix only the reported errors — keep all other data unchanged."
            ),
        },
        ensure_ascii=False,
    )


def apply_repair_patch(data: dict[str, Any], repairs: list[dict[str, Any]]) -> None:
    """Apply repair patches to *data* in place.

    Each repair has:
      - path: dot/bracket path to the field (e.g., "concepts[64].source_block_refs")
      - operation: "replace" | "append" | "remove"
      - value: the new value (for replace/append)
    """
    for repair in repairs:
        path = repair.get("path", "")
        op = repair.get("operation", "replace")
        value = repair.get("value")

        if not path:
            continue

        try:
            if op == "remove":
                parent_path, index = _parse_index_path(path)
                if parent_path is not None and index is not None:
                    parent = _resolve_path(data, parent_path)
                    if isinstance(parent, list) and 0 <= index < len(parent):
                        del parent[index]
                elif path in data:
                    del data[path]
            elif op == "append":
                target = _resolve_path(data, path)
                if isinstance(target, list):
                    target.append(value)
            else:  # replace (default)
                _set_path(data, path, value)
        except (IndexError, KeyError, TypeError, ValueError):
            pass


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _last_assistant_content(conversation: ConversationContext) -> str:
    """Return the content of the last assistant message in the conversation."""
    for msg in reversed(conversation.messages):
        if msg.get("role") == "assistant":
            return str(msg.get("content", ""))
    return ""


def _update_conversation_validation(
    conversation: ConversationContext,
    report: ReadingValidationReport,
) -> None:
    """Record validation results on the last turn's metadata."""
    if conversation.turn_metadata:
        conversation.turn_metadata[-1].validation_report = report.to_dict()


def _propagate_fixes(source: dict[str, Any], target: dict[str, Any]) -> None:
    """Copy key lists from *source* (auto-fixed validation subject) back into *target* (LLM data).

    Only copies list fields that exist in both dicts, handling the common
    pattern where auto-fixes modify the validation subject's lists (concepts,
    atomic_items, logical_groups) and we need to sync them back.
    """
    for key in ("concepts", "atomic_items", "logical_groups", "source_blocks", "unresolved_items"):
        if key in source and key in target:
            target[key] = source[key]


# ── Repair loop logging ──


def _log_validation_failure(
    report: ReadingValidationReport,
    turn: int,
    pass_name: str,
) -> None:
    """Log a compact summary of validation errors to stderr."""
    label = f"{pass_name} turn {turn}" if turn > 0 else pass_name
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    print(
        f"  {label}: {len(errors)} errors, {len(warnings)} warnings",
        file=sys.stderr,
    )
    # Group errors by code for a compact summary
    by_code: dict[str, list] = {}
    for issue in errors:
        by_code.setdefault(issue.code, []).append(issue)
    for code, issues in sorted(by_code.items()):
        sample_paths = [i.path for i in issues[:3]]
        detail = ", ".join(sample_paths)
        if len(issues) > 3:
            detail += f" (+{len(issues) - 3} more)"
        print(f"    {code}: {detail}", file=sys.stderr)


def _log_auto_fixes(
    fixed_codes: list[str],
    turn: int,
    pass_name: str,
) -> None:
    """Log auto-fixes applied to stderr."""
    from collections import Counter

    counts = Counter(fixed_codes)
    parts = [f"{code} x{count}" if count > 1 else code for code, count in counts.items()]
    print(
        f"  {pass_name} turn {turn} auto-fix: {', '.join(parts)}",
        file=sys.stderr,
    )


def _log_llm_repair_start(
    remaining: list[dict[str, Any]],
    turn: int,
    max_turns: int,
    pass_name: str,
) -> None:
    """Log the errors being sent to the LLM for repair."""
    codes = [r.get("code", "?") for r in remaining]
    paths = [r.get("path", "?") for r in remaining[:5]]
    detail = ", ".join(paths)
    if len(remaining) > 5:
        detail += f" (+{len(remaining) - 5} more)"
    print(
        f"  {pass_name}: LLM repair turn {turn}/{max_turns} — "
        f"{len(remaining)} issues: {detail}",
        file=sys.stderr,
    )


def _log_repair_applied(
    repairs: list[dict[str, Any]],
    pass_name: str,
) -> None:
    """Log a summary of repairs applied by the LLM."""
    ops: dict[str, int] = {}
    for r in repairs:
        op = r.get("operation", "replace")
        ops[op] = ops.get(op, 0) + 1
    op_summary = ", ".join(f"{count}x {op}" for op, count in ops.items())
    print(
        f"  {pass_name}: LLM applied {len(repairs)} repair(s) ({op_summary})",
        file=sys.stderr,
    )
