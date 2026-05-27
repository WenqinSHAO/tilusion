from __future__ import annotations

import pytest

from tilusion.source_blocks import (
    MAX_BLOCK_CHARS,
    SOURCE_BLOCK_SPLITTER_VERSION,
    SourceBlock,
    SourceBlockMetrics,
    split_source_blocks,
)


def _block_texts(blocks: list[SourceBlock]) -> list[str]:
    return [b.text for b in blocks]


# ── Round-trip and coverage ───────────────────────────────────────────────────


def test_round_trip_verification_fails_on_mismatch():
    with pytest.raises(ValueError, match="Round-trip verification failed"):
        split_source_blocks(
            "actual segment text",
            segment_id="seg-0001",
            unit_id="unit-0001",
            unit_text="different unit text entirely",
            unit_offset=0,
        )


def test_empty_text():
    blocks, metrics = split_source_blocks(
        "", segment_id="seg-0001", unit_id="unit-0001", unit_text="", unit_offset=0
    )
    assert blocks == []
    assert metrics.block_count == 0
    assert metrics.coverage_pct == 100.0


def test_whitespace_only():
    blocks, metrics = split_source_blocks(
        "  \n\n ",
        segment_id="seg-0001",
        unit_id="unit-0001",
        unit_text="  \n\n ",
        unit_offset=0,
    )
    assert metrics.coverage_pct == 100.0
    # Verify contiguous coverage
    covered = set()
    for b in blocks:
        for i in range(b.start, b.end):
            covered.add(i)
    assert covered == set(range(len("  \n\n ")))


def test_full_coverage():
    text = "Para one.\n\nPara two with more text.\n\nPara three."
    blocks, metrics = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert metrics.coverage_pct == 100.0
    assert metrics.total_chars == len(text)
    assert metrics.covered_chars == len(text)


def test_round_trip_every_block():
    text = "First paragraph.\n\nSecond paragraph with more text.\n\nThird."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    for b in blocks:
        assert text[b.start : b.end] == b.text


def test_round_trip_with_offset():
    unit_text = "prefix--First paragraph.\n\nSecond.---suffix"
    seg_text = "First paragraph.\n\nSecond."
    offset = len("prefix--")  # 8
    blocks, _ = split_source_blocks(
        seg_text,
        segment_id="seg-0001",
        unit_id="unit-0001",
        unit_text=unit_text,
        unit_offset=offset,
    )
    for b in blocks:
        assert unit_text[b.start : b.end] == b.text


# ── Block IDs ─────────────────────────────────────────────────────────────────


def test_block_ids_are_deterministic():
    text = "A.\n\nB."
    blocks1, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    blocks2, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert [b.block_id for b in blocks1] == [b.block_id for b in blocks2]


def test_block_ids_embed_segment_id():
    text = "Some text here."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0003", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert blocks[0].block_id.startswith("seg-0003-block-")


def test_two_segments_no_id_collision():
    text = "Content A"
    blocks_a, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    blocks_b, _ = split_source_blocks(
        text, segment_id="seg-0002", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    ids_a = {b.block_id for b in blocks_a}
    ids_b = {b.block_id for b in blocks_b}
    assert ids_a.isdisjoint(ids_b)


# ── Paragraph splitting ───────────────────────────────────────────────────────


def test_paragraph_splitting():
    # Multi-line content is classified as paragraph regardless of length
    text = "A line.\nB line.\n\nC line.\nD line.\n\nE line.\nF line."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert len(blocks) == 3
    for b in blocks:
        assert b.block_type == "paragraph"


def test_oversized_paragraph_split_on_sentences():
    # Create a paragraph > 800 chars with CJK sentence boundaries
    sentence = "这是第{}句话，包含一些额外内容来描述情况。"
    text = "".join(sentence.format(i) for i in range(50))
    assert len(text) > MAX_BLOCK_CHARS
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert len(blocks) > 1
    for b in blocks:
        assert len(b.text.strip()) <= MAX_BLOCK_CHARS + 100  # allow some slack


def test_oversized_no_sentence_boundaries():
    text = "x" * 1500
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert len(blocks) > 1
    for b in blocks:
        assert len(b.text.strip()) <= MAX_BLOCK_CHARS


# ── Block type classification ─────────────────────────────────────────────────


def test_note_classification():
    text = "[1] This is a footnote annotation."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert blocks[0].block_type == "note"


def test_line_classification():
    text = "A short standalone line."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert blocks[0].block_type == "line"


def test_multiple_consecutive_blank_lines():
    text = "Para one.\n\n\n\n\nPara two."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    # Should still produce exactly 2 content blocks (or 2 blocks total)
    content_blocks = [b for b in blocks if b.text.strip()]
    assert len(content_blocks) == 2


# ── Metrics ───────────────────────────────────────────────────────────────────


def test_metrics_no_oversized():
    text = "Short.\n\nAlso short."
    _, metrics = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert metrics.oversized_count == 0
    assert metrics.oversized_block_ids == []


def test_metrics_oversized_detected():
    text = "x" * 1500
    _, metrics = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    # After sentence splitting, there should be no oversized if fixed split worked
    assert metrics.oversized_count == 0


def test_metrics_coverage_pct():
    text = "Hello world.\n\nGoodbye."
    _, metrics = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert metrics.coverage_pct == 100.0


def test_metrics_avg_block_size():
    text = "A" * 100 + "\n\n" + "B" * 50
    _, metrics = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert metrics.avg_block_size > 0


# ── Provenance ────────────────────────────────────────────────────────────────


def test_provenance_on_every_block():
    text = "Para one.\n\nPara two."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    for b in blocks:
        assert b.provenance["created_by"] == "deterministic"
        assert b.provenance["splitter"] == SOURCE_BLOCK_SPLITTER_VERSION


# ── Text hash ─────────────────────────────────────────────────────────────────


def test_text_hash_is_sha256_hex():
    text = "Some content."
    blocks, _ = split_source_blocks(
        text, segment_id="seg-0001", unit_id="unit-0001", unit_text=text, unit_offset=0
    )
    assert len(blocks[0].text_hash) == 64
    assert all(c in "0123456789abcdef" for c in blocks[0].text_hash)
