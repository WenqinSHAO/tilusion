from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from typing import Any, Iterator

from .backend import sha256_json
from .book_context import stable_book_id


RUN_HASH_SCHEMA_VERSION = "run-hash-v0.1"
RUN_CATALOG_SCHEMA_VERSION = "runs-catalog-v0.1"


def book_root(cache_root: str | Path, book_path: str | Path) -> Path:
    return Path(cache_root) / stable_book_id(book_path)


def source_index_path(cache_root: str | Path, book_path: str | Path) -> Path:
    return book_root(cache_root, book_path) / "source_index.json"


def registry_path(cache_root: str | Path, book_path: str | Path) -> Path:
    return book_root(cache_root, book_path) / "registry.json"


def digest_path(cache_root: str | Path, book_path: str | Path) -> Path:
    return book_root(cache_root, book_path) / "book_digest.json"


def unit_runs_dir(cache_root: str | Path, book_path: str | Path, unit_id: str) -> Path:
    return book_root(cache_root, book_path) / unit_id


def unit_run_dir(
    cache_root: str | Path,
    book_path: str | Path,
    unit_id: str,
    run_hash: str,
) -> Path:
    return unit_runs_dir(cache_root, book_path, unit_id) / run_hash


def cross_unit_dir(cache_root: str | Path, book_path: str | Path) -> Path:
    return book_root(cache_root, book_path) / "cross-unit"


def cross_unit_run_dir(cache_root: str | Path, book_path: str | Path, run_hash: str) -> Path:
    return cross_unit_dir(cache_root, book_path) / run_hash


def runs_catalog_path(cache_root: str | Path, book_path: str | Path) -> Path:
    return book_root(cache_root, book_path) / "runs.json"


def compute_unit_run_hash(
    *,
    source_index_id: str,
    unit_id: str,
    scope: str,
    model_identity: str,
    model_config: dict[str, Any],
    context_identity: dict[str, Any],
    prompt_versions: dict[str, str],
) -> str:
    return _run_hash(
        {
            "schema_version": RUN_HASH_SCHEMA_VERSION,
            "run_type": "unit_extraction",
            "source_index_id": source_index_id,
            "unit_id": unit_id,
            "scope": scope,
            "model_identity": model_identity,
            "model_config": model_config,
            "context_identity": context_identity,
            "prompt_versions": prompt_versions,
        }
    )


def compute_cross_unit_run_hash(
    *,
    source_index_id: str,
    triggering_run_hash: str,
    triggering_unit_id: str,
    registry_state_hash: str,
    model_identity: str,
    model_config: dict[str, Any],
    prompt_versions: dict[str, str],
) -> str:
    return _run_hash(
        {
            "schema_version": RUN_HASH_SCHEMA_VERSION,
            "run_type": "cross_unit_resolution",
            "source_index_id": source_index_id,
            "triggering_run_hash": triggering_run_hash,
            "triggering_unit_id": triggering_unit_id,
            "registry_state_hash": registry_state_hash,
            "model_identity": model_identity,
            "model_config": model_config,
            "prompt_versions": prompt_versions,
        }
    )


def model_config_for_cache(backend: Any) -> dict[str, Any]:
    keys = ("model", "thinking", "reasoning_effort", "max_tokens", "timeout", "max_retries")
    return {
        key: getattr(backend, key)
        for key in keys
        if hasattr(backend, key) and _json_scalar(getattr(backend, key))
    }


def read_runs_catalog(cache_root: str | Path, book_path: str | Path) -> dict[str, Any]:
    path = runs_catalog_path(cache_root, book_path)
    if not path.exists():
        return {"schema_version": RUN_CATALOG_SCHEMA_VERSION, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != RUN_CATALOG_SCHEMA_VERSION:
        raise ValueError(f"unsupported runs catalog schema: {data.get('schema_version')!r}")
    if not isinstance(data.get("runs"), list):
        raise ValueError("runs catalog must contain a list at 'runs'")
    return data


def prepend_to_runs_catalog(
    cache_root: str | Path,
    book_path: str | Path,
    entry: dict[str, Any],
) -> Path:
    path = runs_catalog_path(cache_root, book_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _catalog_lock(path):
        catalog = read_runs_catalog(cache_root, book_path)
        catalog["runs"] = [entry] + list(catalog.get("runs", []))
        _write_json_atomic(path, catalog)
    return path


def write_run_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    _write_json_atomic(path, manifest)
    return path


def read_run_manifest(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def _run_hash(payload: dict[str, Any]) -> str:
    return f"run-{sha256_json(payload)[:16]}"


def _json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


@contextmanager
def _catalog_lock(path: Path, *, timeout_s: float = 30.0) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    started = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() - started > timeout_s:
                raise TimeoutError(f"timed out waiting for runs catalog lock: {lock_path}")
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
