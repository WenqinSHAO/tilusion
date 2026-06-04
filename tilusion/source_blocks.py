from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .reading_schema import SourceBlock

SOURCE_BLOCK_SPLITTER_VERSION = "source-block-splitter-v0.1"
MAX_BLOCK_CHARS = 800
MIN_SENTENCE_FRAGMENT_CHARS = 20

# Sentence-ending punctuation: CJK (always safe) + ASCII .!?
# The period '.' is post-filtered by _is_abbreviation to avoid splitting on
# "Mr.", "Dr.", etc.
_SENTENCE_END_RE = re.compile(r"[。！？；.!?]")

# Blank-line paragraph separator
_PARA_SEP = re.compile(r"(\n\s*\n)")

# Horizontal rule patterns — ornamental dividers between sections
_HRULE_RE = re.compile(
    r"^\s*(?:[-*_]{3,}|[—]{2,}|\*[ ]*\*[ ]*\*)\s*$",
    re.MULTILINE,
)

# Common English abbreviations whose trailing period is not a sentence end.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "dr", "mr", "mrs", "ms", "prof", "rev", "hon", "jr", "sr",
        "vs", "etc", "approx", "dept", "est", "govt",
        "ie", "eg", "am", "pm", "st", "ave", "blvd", "rd",
        "vol", "ch", "p", "pp", "no", "nos",
        "al", "cf", "ed", "eds", "et", "fig", "figs",
        "ibid", "id", "loc", "op", "seq", "sq", "ss", "v",
        "capt", "col", "gen", "lt", "maj", "sgt",
    }
)


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

    Works with both TXT and EPUB source text. The caller is responsible for
    providing correctly offset segment text (the book reader already
    normalises whitespace and handles format differences).
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
            blocks.append(
                _make_block(
                    segment_id=segment_id,
                    unit_id=unit_id,
                    block_index=len(blocks),
                    block_type="paragraph",
                    start=unit_offset + pos,
                    text=chunk,
                )
            )
            pos += len(chunk)
            continue

        # Horizontal rule stands alone
        if _is_horizontal_rule(stripped):
            blocks.append(
                _make_block(
                    segment_id=segment_id,
                    unit_id=unit_id,
                    block_index=len(blocks),
                    block_type="paragraph",
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
    _validate_contiguity(segment_id, blocks, segment_text)
    return blocks, metrics


def _split_contiguous(text: str) -> list[str]:
    """Split text into contiguous chunks at blank-line boundaries.

    Uses ``re.split`` with a capturing group so separators are retained.
    Adjacent content and separator are paired so every character lands in
    exactly one chunk.
    """
    parts = _PARA_SEP.split(text)
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
    sub_offset = 0
    for sub in sub_texts:
        idx = chunk.find(sub, sub_offset)
        if idx < 0:
            continue
        actual_start = sub_offset if sub_offset < idx else idx
        actual_text = chunk[actual_start : idx + len(sub)]
        blocks.append(
            _make_block(
                segment_id=segment_id,
                unit_id=unit_id,
                block_index=start_block_index + len(blocks),
                block_type="paragraph",
                start=unit_offset + local_offset + actual_start,
                text=actual_text,
            )
        )
        sub_offset = idx + len(sub)

    # Catch any trailing whitespace after the last sentence
    if sub_offset < len(chunk):
        if blocks:
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
                    block_type="paragraph",
                    start=unit_offset + local_offset + sub_offset,
                    text=chunk[sub_offset:],
                )
            )

    return blocks


def _split_on_sentences(text: str) -> list[str]:
    """Split on CJK and English sentence boundaries.

    CJK punctuation (``。！？；``) unconditionally ends a sentence.
    ASCII ``.`` ends a sentence only when followed by whitespace and a
    capital letter (or end of text), and is not part of a known abbreviation.
    ASCII ``!`` and ``?`` unconditionally end a sentence.
    """
    result: list[str] = []
    last = 0
    n = len(text)

    for m in _SENTENCE_END_RE.finditer(text):
        i = m.start()
        ch = m.group()

        if ch == ".":
            # Skip abbreviation periods
            if _is_abbreviation(text, i):
                continue
            # Period must be followed by whitespace + uppercase or end-of-text
            after = _peek_after(text, i + 1)
            if after and not _starts_sentence(after):
                continue

        result.append(text[last : i + 1])
        last = i + 1

    if last == 0:
        # No sentence boundaries found — fixed-size split
        return _split_fixed(text, MAX_BLOCK_CHARS)
    if last < n:
        result.append(text[last:])

    return _merge_short_fragments(result)


def _peek_after(text: str, pos: int) -> str:
    """Return the text after skipping whitespace, or '' if at end."""
    rest = text[pos:]
    m = re.match(r"\s*", rest)
    if m is None:
        return ""
    return rest[m.end() :]


def _starts_sentence(text: str) -> bool:
    """Return True if *text* starts like a new sentence."""
    if not text:
        return False
    # Capital letter (ASCII or CJK fullwidth)
    if re.match(r"[A-Z　-〿一-鿿]", text):
        return True
    # Opening quote or bracket followed by capital
    if re.match(r"""["'‘“（(〔［｛][A-Z一-鿿]""", text):
        return True
    return False


def _is_abbreviation(text: str, period_pos: int) -> bool:
    """Return True if the period at *period_pos* is part of a known abbreviation."""
    # Find the word before the period
    start = period_pos - 1
    while start >= 0 and text[start].isalpha():
        start -= 1
    word = text[start + 1 : period_pos].lower()
    return word in _ABBREVIATIONS


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


def _is_horizontal_rule(text: str) -> bool:
    """Return True if *text* is an ornamental divider (***, ---, ——, etc.)."""
    return bool(_HRULE_RE.match(text))


def _classify_block_type(text: str) -> str:
    """All blocks are 'paragraph' — type differentiation is deferred to extraction."""
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


def _validate_contiguity(
    segment_id: str,
    blocks: list[SourceBlock],
    segment_text: str,
) -> None:
    """Assert blocks are contiguous, non-overlapping, and cover all segment text."""
    if not blocks:
        if len(segment_text) > 0:
            raise ValueError(
                f"Block contiguity failure for {segment_id}: "
                f"non-empty segment text ({len(segment_text)} chars) produced zero blocks"
            )
        return

    # Sort by start position for validation
    sorted_blocks = sorted(blocks, key=lambda b: b.start)

    for i in range(len(sorted_blocks) - 1):
        curr = sorted_blocks[i]
        nxt = sorted_blocks[i + 1]
        if curr.end > nxt.start:
            raise ValueError(
                f"Block overlap in {segment_id}: "
                f"{curr.block_id} [{curr.start}:{curr.end}] overlaps "
                f"{nxt.block_id} [{nxt.start}:{nxt.end}] "
                f"by {curr.end - nxt.start} chars"
            )
        if curr.end < nxt.start:
            raise ValueError(
                f"Block gap in {segment_id}: "
                f"{curr.block_id} ends at {curr.end}, "
                f"{nxt.block_id} starts at {nxt.start} "
                f"({nxt.start - curr.end} chars uncovered)"
            )

    total_block_chars = sum(b.end - b.start for b in sorted_blocks)
    if total_block_chars != len(segment_text):
        raise ValueError(
            f"Block coverage mismatch for {segment_id}: "
            f"blocks cover {total_block_chars} chars, "
            f"segment_text is {len(segment_text)} chars"
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
