# Cache Layout Redesign

## Motivation

The current cache layout splits book-level and run-level artifacts across two
roots (`books/` and `reading_passes/`) with two different book hash derivations
(`[:16]` vs `[:12]`). The pipeline works around this with fragile path-peeling:

```python
book_cache_root = _cache_path.parent if _cache_path.name == "reading_passes" else _cache_path
```

Before Phase 4 (registry migration) and Phase 5 (legacy ID removal) of the
source index refactor, we unify run artifacts and book artifacts under the
existing book-scoped root with a consistent hash.

## Guiding principles

- **One book, one root.** All artifacts — source index, registry, digest, unit
  runs, cross-unit passes — live under `.tilusion_cache/books/book-{hash}/`.
- **Run-level grouping.** Overview, per-segment extraction, and logical grouping
  pass caches are bundled into a run directory. A run is a single end-to-end
  extraction of one unit.
- **Book-level artifacts are separate from runs.** Registry, source index, and
  digest are book properties, not run properties.
- **Git tracks book state, not runs.** Unit runs and cross-unit caches are
  reproducible artifacts — they are gitignored. The registry git history IS the
  audit log, with commit messages linking back to triggering runs.
- **Single book-level run catalog.** One `runs.json` at book level records
  every computation — unit extractions and cross-unit resolutions — in causal
  order.
- **Same layout for unit and book scope.** Unit scope simply never creates
  `registry.json`, `runs.json`, `.git/`, or `cross-unit/`.
- **Path identity is local; source identity is semantic.** The book root uses
  the existing path-based `stable_book_id()`. Cache validity is governed by
  `source_index_id`, registry commit/context hash, model config, and prompt
  versions.

## Target layout

```
.tilusion_cache/
  books/
    book-{hash}/
      .git/                        # git repo tracking book-level state
      .gitignore                   # excludes unit-*/, cross-unit/
      runs.json                    # book-level catalog of all runs
      source_index.json            # deterministic, content-addressed (tracked)
      book_digest.json             # book-level digest (book scope only, tracked)
      registry.json                # current registry state (book scope only, tracked)

      unit-{unit_id}/
        {run-hash}/
          run.json                 # manifest: what went into this run
          overview/{cache_key}/
          per_segment/{cache_key}/
          logical_grouping/{cache_key}/
          metrics.json
          unit_package.json

      cross-unit/
        {run-hash}/                # cross-unit's own run hash
          run.json
          concept_resolution/{cache_key}/
          group_resolution/{cache_key}/
```

### Book hash

Consolidate on the existing `stable_book_id()` from `book_context.py`:

```python
def stable_book_id(book_path: str | Path) -> str:
    normalized = str(Path(book_path).expanduser().resolve())
    return f"book-{sha256_text(normalized)[:16]}"
```

The pipeline's ad-hoc `book_hash = sha256_text(str(book_path))[:12]` is removed.
The `books/` directory layer stays because existing registry and context-pack
helpers already use it. It is not a cache-consultation problem; the problem is
the split between `books/` and `reading_passes/`.

### Unit extraction run hash

Content-addressed identity of everything that affects extraction output:

```python
run_hash_input = {
    "schema_version": "run-hash-v0.1",
    "run_type": "unit_extraction",
    "source_index_id": source_index_id,
    "unit_id": unit_id,
    "scope": scope,
    "model_identity": backend.model_identity,
    "model_config": backend.model_config,
    "context_identity": {
        "registry_commit": registry_head_commit_or_empty,
        "book_digest_hash": book_digest_hash_or_empty,
        "context_pack_hash": context_pack_hash_or_empty,
    },
    "prompt_versions": {
        "overview": build_overview_composition().composition_id,
        "per_segment": build_per_segment_extraction_composition().composition_id,
        "logical_grouping": build_unit_logical_grouping_v0_2_composition().composition_id,
    },
}
run_hash = f"run-{sha256_json(run_hash_input)[:16]}"
```

Same source index, scope, registry/digest context, model config, and prompt
versions → same run hash → cache hits across sessions. Unit scope uses empty
context identity. In book scope, the registry commit is the primary restore
handle for the exact digest/context state used by the run. Per-segment hints do
not need a separate identity field because they are derived from the overview
segment, source index, and prompt composition already represented by the run
and pass hashes.

### Cross-unit resolution run hash

Cross-unit runs get their **own** hash, separate from the triggering unit run.
This is necessary because cross-unit results depend on the current registry
state (all prior units' contributions), not just the triggering unit. If
registry state changes, cross-unit results change even if the triggering unit
run is identical.

```python
cross_unit_run_hash_input = {
    "schema_version": "run-hash-v0.1",
    "run_type": "cross_unit_resolution",
    "source_index_id": source_index_id,
    "triggering_run_hash": unit_run_hash,
    "triggering_unit_id": unit_id,
    "registry_state_hash": registry.head_commit_hash(),
    "model_identity": backend.model_identity,
    "model_config": backend.model_config,
    "prompt_versions": {
        "concept_resolution": build_concept_resolution_composition().composition_id,
        "group_resolution": build_group_resolution_composition().composition_id,
    },
}
cross_unit_run_hash = f"run-{sha256_json(cross_unit_run_hash_input)[:16]}"
```

## Book-level run catalog

`runs.json` at the book root — a single file recording every computation in
causal order, newest first. This replaces per-unit `runs.json` files and gives
a complete timeline of everything that happened to the book.

```json
{
  "runs": [
    {
      "run_hash": "run-cu-abc123",
      "run_type": "cross_unit_resolution",
      "triggered_by": {
        "run_hash": "run-abc123",
        "unit_id": "unit-0002"
      },
      "timestamp": "2026-06-02T10:01:00Z",
      "source_index_id": "source-index-abc123",
      "passes": {
        "concept_resolution": {
          "cache_key": "pass-cu-cn-abc",
          "registry_commit": "abc123def"
        },
        "group_resolution": {
          "cache_key": "pass-cu-gr-abc",
          "registry_commit": "abc123def"
        }
      }
    },
    {
      "run_hash": "run-abc123",
      "run_type": "unit_extraction",
      "unit_id": "unit-0002",
      "timestamp": "2026-06-02T10:00:00Z",
      "source_index_id": "source-index-abc123",
      "model": "deepseek-v4-flash",
      "validation_passed": true,
      "elapsed_ms": 12345,
      "triggered_cross_unit": "run-cu-abc123"
    },
    {
      "run_hash": "run-789012",
      "run_type": "unit_extraction",
      "unit_id": "unit-0001",
      "timestamp": "2026-06-01T15:00:00Z",
      "source_index_id": "source-index-abc123",
      "model": "deepseek-v4-flash",
      "validation_passed": true,
      "elapsed_ms": 11800
    }
  ]
}
```

Bidirectional linkage:
- Unit extraction entry has `triggered_cross_unit` → points to the cross-unit
  run that followed.
- Cross-unit entry has `triggered_by` → points to the unit run that triggered
  it. Includes `unit_id` so you can find the triggering run without scanning.
- Cross-unit `passes` record `registry_commit` — the git commit hash after
  applying that pass's delta.

On each unit extraction, the pipeline appends the unit run entry (and its
cross-unit follow-up, if any) to the top of `runs.json`. `runs.json` is
gitignored — it's an index of cache directories. Writes are atomic:
`runs.json.tmp` is written and fsynced, then replaced into place. A lightweight
lock file prevents two concurrent runs for the same book from corrupting the
catalog.

## Run manifest (per-run `run.json`)

Each run directory contains a `run.json` with full details. Same for both
`unit_extraction` and `cross_unit_resolution` run types.

**Unit extraction `run.json`:**

```json
{
  "run_hash": "run-abc123",
  "run_type": "unit_extraction",
  "unit_id": "unit-0002",
  "source_index_id": "source-index-abc123",
  "scope": "book",
  "model_identity": {
    "model": "deepseek-v4-flash",
    "thinking": false,
    "reasoning_effort": "high"
  },
  "model_config": {
    "max_tokens": 384000,
    "temperature": 0
  },
  "context_identity": {
    "registry_commit": "abc123def",
    "book_digest_hash": "digest-abc123",
    "context_pack_hash": ""
  },
  "prompt_versions": {
    "overview": "overview_segmentation_v0.2",
    "per_segment": "per_segment_extraction_v0.2",
    "logical_grouping": "unit_logical_grouping_v0.2"
  },
  "pass_cache_keys": {
    "overview": "pass-abc123",
    "per_segment": ["pass-def456", "pass-ghi789"],
    "logical_grouping": "pass-jkl012"
  },
  "elapsed_ms": 12345,
  "validation_passed": true
}
```

**Cross-unit resolution `run.json`:**

```json
{
  "run_hash": "run-cu-abc123",
  "run_type": "cross_unit_resolution",
  "triggered_by": {
    "run_hash": "run-abc123",
    "unit_id": "unit-0002"
  },
  "source_index_id": "source-index-abc123",
  "registry_state_hash": "git-abc123",
  "model_identity": {
    "model": "deepseek-v4-flash",
    "thinking": false,
    "reasoning_effort": "high"
  },
  "model_config": {
    "max_tokens": 384000,
    "temperature": 0
  },
  "prompt_versions": {
    "concept_resolution": "concept-resolution-v0.2",
    "group_resolution": "group-resolution-v0.2"
  },
  "pass_cache_keys": {
    "concept_resolution": "pass-cu-cn-abc",
    "group_resolution": "pass-cu-gr-abc"
  },
  "registry_commits": {
    "concept_resolution": "abc123def",
    "group_resolution": "def456abc"
  }
}
```

## Registry/source-index compatibility

Book-scope registry state records the `source_index_id` it was built against.
Before applying any registry delta, the pipeline verifies that the current
`source_index_id` matches the registry metadata. A mismatch is a hard error
unless an explicit migration command is introduced later. This keeps Phase 4
from silently mixing old `overview-segment-*` evidence refs with book-scoped
`block-*` refs.

## Cross-unit resolution

Lives at `cross-unit/{cross_unit_run_hash}/`. The directory uses the
cross-unit's own run hash, which encodes the triggering run + registry state +
prompt versions. Each pass cache also carries `triggering_run_hash` and
`triggering_unit_id` in its metadata for direct lookup without scanning
`runs.json`.

### Uncommitted proposals

Cross-unit resolution may propose registry changes that are validated but not
yet committed (e.g., held for user review). The resolution cache stores the
full proposal. The registry is only modified when
`registry.apply_delta(delta, run_hash=cross_unit_run_hash)` is called. Until
then, the proposal exists only in the cross-unit pass cache.

## Registry commit model (git-backed)

The git repo lives at `.tilusion_cache/books/book-{hash}/.git`. `.gitignore`:

```
unit-*/
cross-unit/
runs.json
```

Git tracks only book-level state:
- `registry.json`
- `source_index.json` — if it diffs, the splitter or book identity changed,
  which is a sign of error worth investigating.
- `book_digest.json`
- `.gitignore`

The git history of `registry.json` is the audit trail. Each commit message
includes the cross-unit run hash:

```
cross-unit resolution for unit-0002 [run-cu-abc123]

- 3 concepts merged
- 1 concept added
- 2 groups continued
```

### `save()` and `rollback()`

`BookRegistry.save(run_hash: str | None = None)`:
- Writes `registry.json`
- If `run_hash` provided, includes it in the commit message
- Stages `registry.json`, `book_digest.json`, `source_index.json`, and
  `.gitignore` when present
- No-op if nothing changed

`BookRegistry.rollback(commit_hash: str)`:
- `git checkout <hash> -- registry.json`
- Reloads in-memory state
- Unit runs and cross-unit caches are unaffected (gitignored)

### Initialization

First book-scope extraction:
1. `source_index.json` built (always, even in unit scope)
2. Book root directory created: `.tilusion_cache/books/book-{hash}/`
3. Git repo initialized, `.gitignore` written
4. `source_index.json` committed (initial commit)
5. `BookRegistry` created, `registry.json` committed
6. `book_digest.json` built and committed

First unit-scope extraction:
1. `source_index.json` built
2. No git repo, no registry, no digest, no `runs.json` — unit scope only

## Unit scope vs book scope

| Artifact | Unit scope | Book scope |
|----------|-----------|------------|
| `source_index.json` | yes | yes |
| `unit-{id}/` runs | yes | yes |
| `runs.json` | no | yes |
| `.git/` | no | yes |
| `registry.json` | no | yes |
| `book_digest.json` | no | yes |
| `cross-unit/` | no | yes |

The layout is the same for both scopes — book scope just has additional
book-level artifacts. A unit-scope extraction can later be "upgraded" to
book scope without moving files.

## Migration from current layout

Old layout:
```
.tilusion_cache/
  books/{book_id}/
    .git/
    registry.json
    book_digest.json
    source_index.json
  reading_passes/{book_hash[:12]}/
    overview/{cache_key}/
    per_segment/{cache_key}/
    logical_grouping/{cache_key}/
    concept_resolution/{cache_key}/
    group_resolution/{cache_key}/
    unit_package.json
```

New layout:
```
.tilusion_cache/
  books/{book_id}/
    source_index.json
    registry.json
    book_digest.json
    runs.json
    unit-{unit_id}/{run_hash}/...
    cross-unit/{run_hash}/...
```

Migration:
1. New runs write to the new layout only.
2. On load, check new layout first; fall back to old `reading_passes/` caches
   for read-only access to legacy run artifacts.
3. Old caches are never deleted automatically.
4. Existing book-level git history under `books/{book_id}/.git` is preserved
   because the book root does not move.

## Files changed

| File | Action | Est. lines |
|------|--------|------------|
| `tilusion/cache_layout.py` | NEW | ~180 |
| `tilusion/reading_pipeline.py` | MODIFY | ~120 |
| `tilusion/book_registry.py` | MODIFY | ~40 |
| `tilusion/source_index.py` | MODIFY | ~20 |
| `tilusion/book_context.py` | SMALL MODIFY | ~10 |

New `tilusion/cache_layout.py` provides:

```python
def book_root(cache_root: str | Path, book_path: str | Path) -> Path: ...
def source_index_path(cache_root, book_path) -> Path: ...
def registry_path(cache_root, book_path) -> Path: ...
def digest_path(cache_root, book_path) -> Path: ...

def unit_runs_dir(cache_root, book_path, unit_id: str) -> Path: ...
def unit_run_dir(cache_root, book_path, unit_id: str, run_hash: str) -> Path: ...

def cross_unit_dir(cache_root, book_path) -> Path: ...
def cross_unit_run_dir(cache_root, book_path, run_hash: str) -> Path: ...

def runs_catalog_path(cache_root, book_path) -> Path: ...
def read_runs_catalog(cache_root, book_path) -> list[dict]: ...
def prepend_to_runs_catalog(cache_root, book_path, entry: dict) -> None: ...

def compute_unit_run_hash(
    source_index_id: str, unit_id: str,
    scope: str,
    model_identity: dict, model_config: dict,
    context_identity: dict, prompt_versions: dict,
) -> str: ...

def compute_cross_unit_run_hash(
    source_index_id: str, triggering_run_hash: str,
    triggering_unit_id: str, registry_state_hash: str,
    model_identity: dict, model_config: dict,
    prompt_versions: dict,
) -> str: ...

def write_run_manifest(run_dir: Path, manifest: dict) -> None: ...
def read_run_manifest(run_dir: Path) -> dict: ...
```
