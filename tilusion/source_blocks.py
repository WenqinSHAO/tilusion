from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

SOURCE_BLOCK_SPLITTER_VERSION = "source-block-splitter-v0.1"
MAX_BLOCK_CHARS = 800
MIN_SENTENCE_FRAGMENT_CHARS = 20

_CJK_SENTENCE_END = re.compile(r"[。！？；](?=\s*)")
_PARA_SEP = re.compile(r"(\n\s*\n)")


@dataclass(slots=True)
class SourceBlock:
    block_id: str
    unit_id: str
    segment_id: str
    block_index: int
    block_type: str
    start: int
    end: int
    text: str
    text_hash: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "unit_id": self.unit_id,
            "segment_id": self.segment_id,
            "block_index": self.block_index,
            "block_type": self.block_type,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "text_hash": self.text_hash,
            "provenance": self.provenance,
        }


@dataclass(slots=True)
class SourceBlockMetrics:
    segment_id: str
    block_count: int
    covered_chars: int
    total_chars: int
    coverage_pct: float
    avg_block_size: float
    oversized_count: int
    oversized_block_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "block_count": self.block_count,
            "covered_chars": self.covered_chars,
            "total_chars": self.total_chars,
            "coverage_pct": self.coverage_pct,
            "avg_block_size": self.avg_block_size,
            "oversized_count": self.oversized_count,
            "oversized_block_ids": self.oversized_block_ids,
        }


def split_source_blocks(
    segment_text: str,
    *,
    segment_id: str,
    unit_id: str,
    unit_text: str,
    unit_offset: int,
) -> tuple[list[SourceBlock], SourceBlockMetrics]:
    """Build deterministic source blocks from one segment's text.

    Returns blocks and quality metrics. Each block carries a stable
    ``{segment_id}-block-{index:04d}`` ID and exact unit-level character
    offsets verified by round-trip against ``unit_text``.

    Blocks are contiguous — every character of the segment text belongs to
    exactly one block. Blank-line paragraph separators are included as
    trailing text in the preceding block.
    """

    # Round-trip verification
    expected = unit_text[unit_offset : unit_offset + len(segment_text)]
    if expected != segment_text:
        raise ValueError(
            f"Round-trip verification failed for {segment_id}: "
            f"segment_text does not match unit_text at offset {unit_offset}"
        )

    raw_chunks = _split_contiguous(segment_text)
    blocks: list[SourceBlock] = []
    pos = 0  # local offset within segment_text

    for chunk in raw_chunks:
        if not chunk:
            pos += len(chunk)
            continue

        stripped = chunk.strip()
        if not stripped:
            # Whitespace-only chunk — keep as a minimal block for coverage
            blocks.append(
                _make_block(
                    segment_id=segment_id,
                    unit_id=unit_id,
                    block_index=len(blocks),
                    block_type="other",
                    start=unit_offset + pos,
                    text=chunk,
                )
            )
            pos += len(chunk)
            continue

        sub_blocks = _chunk_to_blocks(
            chunk=chunk,
            stripped=stripped,
            segment_id=segment_id,
            unit_id=unit_id,
            unit_offset=unit_offset,
            local_offset=pos,
            start_block_index=len(blocks),
        )
        blocks.extend(sub_blocks)
        pos += len(chunk)

    metrics = _compute_metrics(segment_id, blocks, segment_text)
    return blocks, metrics


def _split_contiguous(text: str) -> list[str]:
    """Split text into contiguous chunks at blank-line boundaries.

    Uses ``re.split`` with a capturing group so separators are retained.
    Adjacent content and separator are paired so every character lands in
    exactly one chunk.
    """
    parts = _PARA_SEP.split(text)
    # parts alternates: content, separator, content, separator, ..., content
    chunks: list[str] = []
    for i in range(0, len(parts), 2):
        content = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        chunks.append(content + sep)
    return chunks


def _chunk_to_blocks(
    *,
    chunk: str,
    stripped: str,
    segment_id: str,
    unit_id: str,
    unit_offset: int,
    local_offset: int,
    start_block_index: int,
) -> list[SourceBlock]:
    """Convert a raw text chunk into one or more SourceBlock records."""
    if len(stripped) <= MAX_BLOCK_CHARS:
        block_type = _classify_block_type(stripped)
        return [
            _make_block(
                segment_id=segment_id,
                unit_id=unit_id,
                block_index=start_block_index,
                block_type=block_type,
                start=unit_offset + local_offset,
                text=chunk,
            )
        ]

    # Oversized — split on sentence boundaries
    sub_texts = _split_on_sentences(stripped)
    blocks: list[SourceBlock] = []
    # Locate each sub_text within the chunk to recover leading/trailing whitespace
    sub_offset = 0
    for sub in sub_texts:
        idx = chunk.find(sub, sub_offset)
        if idx < 0:
            continue
        # Include any whitespace between this sub and the previous one
        actual_start = sub_offset if sub_offset < idx else idx
        actual_text = chunk[actual_start : idx + len(sub)]
        blocks.append(
            _make_block(
                segment_id=segment_id,
                unit_id=unit_id,
                block_index=start_block_index + len(blocks),
                block_type="sentence_group",
                start=unit_offset + local_offset + actual_start,
                text=actual_text,
            )
        )
        sub_offset = idx + len(sub)

    # Catch any trailing whitespace after the last sentence
    if sub_offset < len(chunk):
        if blocks:
            # Append trailing whitespace to last block
            last = blocks[-1]
            extra = chunk[sub_offset:]
            blocks[-1] = _make_block(
                segment_id=segment_id,
                unit_id=unit_id,
                block_index=last.block_index,
                block_type=last.block_type,
                start=last.start,
                text=last.text + extra,
            )
        else:
            blocks.append(
                _make_block(
                    segment_id=segment_id,
                    unit_id=unit_id,
                    block_index=start_block_index,
                    block_type="other",
                    start=unit_offset + local_offset + sub_offset,
                    text=chunk[sub_offset:],
                )
            )

    return blocks


def _split_on_sentences(text: str) -> list[str]:
    """Split text on CJK sentence boundaries, keeping delimiters attached.

    Falls back to fixed-size splitting when no sentence boundaries are found.
    """
    result: list[str] = []
    last = 0
    for m in _CJK_SENTENCE_END.finditer(text):
        end = m.end()
        result.append(text[last:end])
        last = end
    if last == 0:
        # No sentence boundaries found — split on fixed character limit
        return _split_fixed(text, MAX_BLOCK_CHARS)
    if last < len(text):
        result.append(text[last:])
    return _merge_short_fragments(result)


def _split_fixed(text: str, chunk_size: int) -> list[str]:
    """Split text into fixed-size chunks at the given character limit."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i : i + chunk_size])
    return chunks


def _merge_short_fragments(fragments: list[str]) -> list[str]:
    """Merge fragments shorter than MIN_SENTENCE_FRAGMENT_CHARS into the
    preceding fragment. Whitespace-only fragments are appended to the
    preceding fragment unconditionally.
    """
    if len(fragments) <= 1:
        return fragments
    merged: list[str] = []
    for frag in fragments:
        stripped = frag.strip()
        if not stripped:
            if merged:
                merged[-1] += frag
            else:
                merged.append(frag)
        elif merged and len(merged[-1].strip()) < MIN_SENTENCE_FRAGMENT_CHARS:
            merged[-1] += frag
        else:
            merged.append(frag)
    return merged or fragments


def _classify_block_type(text: str) -> str:
    """Heuristic block type classification."""
    stripped = text.strip()
    if not stripped:
        return "other"
    # Footnote / annotation: starts with [digit]
    if re.match(r"\[\d+\]", stripped):
        return "note"
    # Single short line (title, standalone line)
    if "\n" not in stripped and len(stripped) < 120:
        return "line"
    return "paragraph"


def _make_block(
    *,
    segment_id: str,
    unit_id: str,
    block_index: int,
    block_type: str,
    start: int,
    text: str,
) -> SourceBlock:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SourceBlock(
        block_id=f"{segment_id}-block-{block_index:04d}",
        unit_id=unit_id,
        segment_id=segment_id,
        block_index=block_index,
        block_type=block_type,
        start=start,
        end=start + len(text),
        text=text,
        text_hash=text_hash,
        provenance={
            "created_by": "deterministic",
            "splitter": SOURCE_BLOCK_SPLITTER_VERSION,
        },
    )


def _compute_metrics(
    segment_id: str,
    blocks: list[SourceBlock],
    segment_text: str,
) -> SourceBlockMetrics:
    covered = sum(blk.end - blk.start for blk in blocks)
    total = len(segment_text)
    coverage = (covered / total * 100) if total > 0 else 100.0
    oversized = [
        blk.block_id for blk in blocks if len(blk.text.strip()) > MAX_BLOCK_CHARS
    ]
    return SourceBlockMetrics(
        segment_id=segment_id,
        block_count=len(blocks),
        covered_chars=covered,
        total_chars=total,
        coverage_pct=round(coverage, 2),
        avg_block_size=round(covered / len(blocks), 2) if blocks else 0.0,
        oversized_count=len(oversized),
        oversized_block_ids=oversized,
    )
