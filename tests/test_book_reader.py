from __future__ import annotations

from pathlib import Path
import zipfile

from tilusion.book_reader import build_book_index, extract_unit_text


def test_txt_reader_detects_chapters(tmp_path: Path) -> None:
    book = tmp_path / "sample.txt"
    book.write_text("Chapter 1\nAlpha\n\nChapter 2\nBeta\n", encoding="utf-8")

    index = build_book_index(book)

    assert index.source_format == "txt"
    assert [unit.label for unit in index.units[1:]] == ["Chapter 1", "Chapter 2"]
    assert index.units[1].source_kind == "heading"
    assert index.units[1].content_kind == "main_text"
    assert index.units[1].title_path == ["Chapter 1"]
    assert index.units[1].source_range == {
        "kind": "txt-span",
        "start_byte": 0,
        "end_byte": 17,
        "start_line": 1,
        "end_line": 3,
    }
    assert extract_unit_text(book, index.units[1]).startswith("Chapter 1")


def test_txt_reader_falls_back_to_chunks(tmp_path: Path) -> None:
    book = tmp_path / "plain.txt"
    book.write_text("alpha\n" * 20000, encoding="utf-8")

    index = build_book_index(book)

    assert index.units[1].kind == "chunk"
    assert index.units[1].source_kind == "fallback_chunk"
    assert index.units[1].content_kind == "unknown"
    assert "structure inferred from fallback chunking" in index.units[1].warnings[0]
    assert "fallback chunking" in index.units[1].notes[0]


def test_txt_reader_filters_dense_duplicate_toc_block(tmp_path: Path) -> None:
    book = tmp_path / "toc_like.txt"
    book.write_text(
        "\n".join(
            [
                "前言",
                "",
                "卷一 闺房记乐",
                "卷二 闲情记趣",
                "卷三 坎坷记愁",
                "",
                "前言内容",
                "",
                "卷一 闺房记乐",
                "正文甲",
                "",
                "卷二 闲情记趣",
                "正文乙",
                "",
                "卷三 坎坷记愁",
                "正文丙",
            ]
        ),
        encoding="utf-8",
    )

    index = build_book_index(book)

    assert [unit.label for unit in index.units[1:]] == ["卷一 闺房记乐", "卷二 闲情记趣", "卷三 坎坷记愁"]
    assert "正文甲" in extract_unit_text(book, index.units[1])


def test_epub_reader_uses_toc_ranges(tmp_path: Path) -> None:
    book = tmp_path / "sample.epub"
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Sample EPUB</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>
""",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
    <navPoint id="n2" playOrder="2">
      <navLabel><text>Chapter 2</text></navLabel>
      <content src="chapter2.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter 1</h1><p>Alpha text.</p></body></html>""",
        )
        archive.writestr(
            "OEBPS/chapter2.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter 2</h1><p>Beta text.</p></body></html>""",
        )

    index = build_book_index(book)

    assert index.title == "Sample EPUB"
    assert [unit.label for unit in index.units[1:3]] == ["Chapter 1", "Chapter 2"]
    assert index.units[1].source_kind == "toc"
    assert index.units[1].content_kind == "main_text"
    assert index.units[1].title_path == ["Chapter 1"]
    assert index.units[1].source_range == {
        "kind": "epub-range",
        "start": {"spine_index": 0, "char_offset": 0},
        "end": {"spine_index": 1, "char_offset": 0},
        "source_path": "OEBPS/chapter1.xhtml",
    }
    assert "Alpha text." in extract_unit_text(book, index.units[1])


def test_epub_reader_reconciles_suspicious_toc_targets(tmp_path: Path) -> None:
    book = tmp_path / "odd_toc.epub"
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Odd TOC</dc:title>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="a" href="part1.xhtml" media-type="application/xhtml+xml"/>
    <item id="b" href="part2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="a"/>
    <itemref idref="b"/>
  </spine>
</package>
""",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1" playOrder="1">
      <navLabel><text>卷一 闺房记乐</text></navLabel>
      <content src="part1.xhtml#c1-back"/>
    </navPoint>
    <navPoint id="n2" playOrder="2">
      <navLabel><text>卷二 闲情记趣</text></navLabel>
      <content src="part1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
""",
        )
        archive.writestr(
            "OEBPS/part1.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="h1">卷一 闺房记乐</h1><a id="c1-back"></a><p>正文甲。</p></body></html>""",
        )
        archive.writestr(
            "OEBPS/part2.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>卷二 闲情记趣</h1><p>正文乙。</p></body></html>""",
        )

    index = build_book_index(book)

    assert [unit.label for unit in index.units[1:3]] == ["卷一 闺房记乐", "卷二 闲情记趣"]
    assert index.units[1].source_kind == "reconciled_toc"
    assert "reconciled" in index.units[1].warnings[0]
    assert index.units[2].source_kind == "reconciled_toc"
    assert "卷一 闺房记乐" in extract_unit_text(book, index.units[1])
    assert "卷二 闲情记趣" in extract_unit_text(book, index.units[2])
