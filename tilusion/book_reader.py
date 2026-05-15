from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path, PurePosixPath
import codecs
import json
import re
from typing import Any
from urllib.parse import urldefrag
from zipfile import ZipFile

from charset_normalizer import from_bytes
from lxml import etree, html


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}

TXT_HEADING_PATTERNS = [
    (
        re.compile(
            r"^(?P<label>(part|book|chapter)\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))[\s:.-]*$",
            re.IGNORECASE,
        ),
        "chapter",
        2,
    ),
    (
        re.compile(r"^(?P<label>(prologue|epilogue|preface|introduction))[\s:.-]*$", re.IGNORECASE),
        "section",
        2,
    ),
    (
        re.compile(
            r"^(?P<label>第[0-9零〇○一二三四五六七八九十百千万兩两壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]+[部卷章节節回篇集](?:[\s　]+.+)?)\s*$"
        ),
        "chapter",
        2,
    ),
    (
        re.compile(
            r"^(?P<label>卷[0-9零〇○一二三四五六七八九十百千万兩两壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]+(?:[\s　]+.+)?)\s*$"
        ),
        "chapter",
        2,
    ),
    (
        re.compile(
            r"^(?P<label>(上篇|中篇|下篇|前言|序|序章|楔子|后记|後記|跋|附录|附錄))[\s：:.-]*$"
        ),
        "section",
        2,
    ),
]


@dataclass(slots=True)
class Position:
    spine_index: int
    char_offset: int


@dataclass(slots=True)
class StructureUnit:
    id: str
    kind: str
    label: str
    order: int
    level: int
    parent_id: str | None
    children: list[str]
    locator: dict[str, Any]
    nav_hint: str
    source_kind: str
    content_kind: str
    title_path: list[str] = field(default_factory=list)
    source_range: dict[str, Any] | None = None
    source_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BookIndex:
    source_path: str
    source_format: str
    title: str
    metadata: dict[str, Any]
    root_id: str
    units: list[StructureUnit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "title": self.title,
            "metadata": self.metadata,
            "root_id": self.root_id,
            "units": [unit.to_dict() for unit in self.units],
        }

    def unit_map(self) -> dict[str, StructureUnit]:
        return {unit.id: unit for unit in self.units}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_outline(self) -> str:
        unit_map = self.unit_map()
        lines = [f"{self.title} [{self.source_format}]"]

        def walk(unit_id: str, depth: int) -> None:
            unit = unit_map[unit_id]
            if unit.parent_id is not None:
                lines.append(
                    f"{'  ' * depth}- {unit.id}: {unit.label} ({unit.kind}) [{unit.nav_hint}]"
                )
            for child_id in unit.children:
                walk(child_id, depth + 1)

        walk(self.root_id, 0)
        return "\n".join(lines)


def build_book_index(path: str | Path) -> BookIndex:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".txt":
        return TxtBookReader(source).build_index()
    if suffix == ".epub":
        return EpubBookReader(source).build_index()
    raise ValueError(f"Unsupported file format: {source.suffix}")


def extract_unit_text(path: str | Path, unit: StructureUnit) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".txt":
        return TxtBookReader(source).extract_unit(unit)
    if suffix == ".epub":
        return EpubBookReader(source).extract_unit(unit)
    raise ValueError(f"Unsupported file format: {source.suffix}")


class TxtBookReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.encoding = detect_text_encoding(path)

    def build_index(self) -> BookIndex:
        units: list[StructureUnit] = []
        root = StructureUnit(
            id="book",
            kind="book",
            label=self.path.stem,
            order=0,
            level=0,
            parent_id=None,
            children=[],
            locator={"type": "txt-book", "path": str(self.path)},
            nav_hint=self.path.name,
            source_kind="container",
            content_kind="book",
        )
        units.append(root)

        headings: list[dict[str, Any]] = []
        total_lines = 0
        byte_offset = 0
        with self.path.open("rb") as handle:
            for raw_line in handle:
                total_lines += 1
                decoded = raw_line.decode(self.encoding, errors="replace").strip()
                heading = classify_txt_heading(decoded)
                if heading is not None:
                    heading["start_byte"] = byte_offset
                    heading["start_line"] = total_lines
                    headings.append(heading)
                byte_offset += len(raw_line)
        file_size = self.path.stat().st_size
        headings = filter_txt_duplicate_toc_blocks(headings)

        if headings:
            stack: list[StructureUnit] = [root]
            for idx, heading in enumerate(headings, start=1):
                end_byte = headings[idx]["start_byte"] if idx < len(headings) else file_size
                end_line = headings[idx]["start_line"] - 1 if idx < len(headings) else total_lines
                unit = StructureUnit(
                    id=f"u{idx}",
                    kind=heading["kind"],
                    label=heading["label"],
                    order=idx,
                    level=heading["level"],
                    parent_id=None,
                    children=[],
                    locator={
                        "type": "txt-span",
                        "start_byte": heading["start_byte"],
                        "end_byte": end_byte,
                        "encoding": self.encoding,
                    },
                    nav_hint=f"line {heading['start_line']}",
                    source_kind="heading",
                    content_kind=infer_content_kind(heading["label"], heading["kind"], "heading"),
                    source_range={
                        "kind": "txt-span",
                        "start_byte": heading["start_byte"],
                        "end_byte": end_byte,
                        "start_line": heading["start_line"],
                        "end_line": end_line,
                    },
                    start_line=heading["start_line"],
                    end_line=end_line,
                )
                while stack and stack[-1].level >= unit.level:
                    stack.pop()
                parent = stack[-1] if stack else root
                unit.parent_id = parent.id
                parent.children.append(unit.id)
                stack.append(unit)
                units.append(unit)
        else:
            chunk_size = 64 * 1024
            start = 0
            order = 1
            line_cursor = 1
            while start < file_size:
                end = min(start + chunk_size, file_size)
                unit = StructureUnit(
                    id=f"u{order}",
                    kind="chunk",
                    label=f"Chunk {order}",
                    order=order,
                    level=1,
                    parent_id="book",
                    children=[],
                    locator={
                        "type": "txt-span",
                        "start_byte": start,
                        "end_byte": end,
                        "encoding": self.encoding,
                    },
                    nav_hint=f"bytes {start}-{end}",
                    source_kind="fallback_chunk",
                    content_kind="unknown",
                    source_range={
                        "kind": "txt-span",
                        "start_byte": start,
                        "end_byte": end,
                        "start_line": line_cursor,
                        "end_line": None,
                    },
                    start_line=line_cursor,
                    end_line=None,
                    warnings=["structure inferred from fallback chunking"],
                    notes=["fallback chunking: no confident headings detected"],
                )
                root.children.append(unit.id)
                units.append(unit)
                start = end
                order += 1

        assign_title_paths(units, root.id)

        return BookIndex(
            source_path=str(self.path),
            source_format="txt",
            title=self.path.stem,
            metadata={"encoding": self.encoding, "size_bytes": file_size, "line_count": total_lines},
            root_id=root.id,
            units=units,
        )

    def extract_unit(self, unit: StructureUnit) -> str:
        locator = unit.locator
        with self.path.open("rb") as handle:
            handle.seek(locator["start_byte"])
            payload = handle.read(locator["end_byte"] - locator["start_byte"])
        return payload.decode(locator["encoding"], errors="replace")


class EpubBookReader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def build_index(self) -> BookIndex:
        with ZipFile(self.path) as archive:
            opf_path = self._get_opf_path(archive)
            package = self._parse_xml_bytes(archive.read(opf_path))
            opf_dir = PurePosixPath(opf_path).parent
            metadata = self._extract_metadata(package)
            manifest = self._parse_manifest(package, opf_dir)
            spine = self._parse_spine(package, manifest)
            doc_cache = self._build_document_cache(archive, spine)
            toc_entries = self._parse_toc(archive, package, manifest, opf_dir, spine, doc_cache)
            units = self._build_units(str(self.path), metadata, spine, doc_cache, toc_entries)
            title = metadata.get("title") or self.path.stem
            return BookIndex(
                source_path=str(self.path),
                source_format="epub",
                title=title,
                metadata={
                    **metadata,
                    "opf_path": opf_path,
                    "spine_count": len(spine),
                    "archive_members": len(archive.namelist()),
                },
                root_id="book",
                units=units,
            )

    def extract_unit(self, unit: StructureUnit) -> str:
        locator = unit.locator
        start = Position(locator["start"]["spine_index"], locator["start"]["char_offset"])
        end = Position(locator["end"]["spine_index"], locator["end"]["char_offset"])
        with ZipFile(self.path) as archive:
            opf_path = self._get_opf_path(archive)
            package = self._parse_xml_bytes(archive.read(opf_path))
            opf_dir = PurePosixPath(opf_path).parent
            manifest = self._parse_manifest(package, opf_dir)
            spine = self._parse_spine(package, manifest)
            doc_cache = self._build_document_cache(archive, spine)

        chunks: list[str] = []
        for spine_index in range(start.spine_index, end.spine_index + 1):
            doc = doc_cache[spine_index]
            text = doc["text"]
            slice_start = start.char_offset if spine_index == start.spine_index else 0
            slice_end = end.char_offset if spine_index == end.spine_index else len(text)
            if slice_start < slice_end:
                chunks.append(text[slice_start:slice_end].strip())
        return "\n\n".join(chunk for chunk in chunks if chunk)

    def _get_opf_path(self, archive: ZipFile) -> str:
        container = self._parse_xml_bytes(archive.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        opf_path = container.xpath("string(//c:rootfile/@full-path)", namespaces=ns)
        if not opf_path:
            raise ValueError("EPUB container.xml does not declare a package document")
        return opf_path

    def _parse_xml_bytes(self, payload: bytes) -> etree._Element:
        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
        return etree.fromstring(payload, parser=parser)

    def _parse_manifest(
        self, package: etree._Element, opf_dir: PurePosixPath
    ) -> dict[str, dict[str, Any]]:
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        manifest: dict[str, dict[str, Any]] = {}
        for item in package.xpath("//opf:manifest/opf:item", namespaces=ns):
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            full_path = normalize_epub_path(opf_dir, href)
            manifest[item_id] = {
                "id": item_id,
                "href": full_path,
                "media_type": item.get("media-type"),
                "properties": item.get("properties", ""),
            }
        return manifest

    def _parse_spine(
        self, package: etree._Element, manifest: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        spine: list[dict[str, Any]] = []
        for idx, itemref in enumerate(package.xpath("//opf:spine/opf:itemref", namespaces=ns)):
            idref = itemref.get("idref")
            manifest_item = manifest.get(idref or "")
            if not manifest_item:
                continue
            media_type = manifest_item.get("media_type")
            if media_type not in {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}:
                continue
            spine.append({"spine_index": idx, **manifest_item})
        return spine

    def _extract_metadata(self, package: etree._Element) -> dict[str, Any]:
        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        title = package.xpath("string(//dc:title[1])", namespaces=ns).strip()
        creator = package.xpath("string(//dc:creator[1])", namespaces=ns).strip()
        language = package.xpath("string(//dc:language[1])", namespaces=ns).strip()
        return {"title": title, "creator": creator, "language": language}

    def _build_document_cache(
        self, archive: ZipFile, spine: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        cache: dict[int, dict[str, Any]] = {}
        for item in spine:
            payload = archive.read(item["href"])
            doc = parse_html_bytes(payload)
            text, anchors, headings, title = flatten_html_document(doc)
            cache[item["spine_index"]] = {
                "href": item["href"],
                "text": text,
                "anchors": anchors,
                "headings": headings,
                "title": title or Path(item["href"]).stem,
            }
        return cache

    def _parse_toc(
        self,
        archive: ZipFile,
        package: etree._Element,
        manifest: dict[str, dict[str, Any]],
        opf_dir: PurePosixPath,
        spine: list[dict[str, Any]],
        doc_cache: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        spine_lookup = {item["href"]: item["spine_index"] for item in spine}
        nav_item = next((item for item in manifest.values() if "nav" in item["properties"].split()), None)
        if nav_item:
            doc = parse_html_bytes(archive.read(nav_item["href"]))
            nav_nodes = doc.xpath(
                "//*[local-name()='nav' and contains(concat(' ', normalize-space(@epub:type), ' '), ' toc ')]"
            )
            if nav_nodes:
                entries = parse_epub3_nav(nav_nodes[0], nav_item["href"], spine_lookup, doc_cache)
                return reconcile_toc_entries(entries, spine, doc_cache)

        ns = {"opf": "http://www.idpf.org/2007/opf"}
        toc_id = package.xpath("string(//opf:spine/@toc)", namespaces=ns)
        if toc_id and toc_id in manifest:
            entries = parse_ncx_toc(
                self._parse_xml_bytes(archive.read(manifest[toc_id]["href"])),
                manifest[toc_id]["href"],
                spine_lookup,
                doc_cache,
            )
            return reconcile_toc_entries(entries, spine, doc_cache)
        return []

    def _build_units(
        self,
        source_path: str,
        metadata: dict[str, Any],
        spine: list[dict[str, Any]],
        doc_cache: dict[int, dict[str, Any]],
        toc_entries: list[dict[str, Any]],
    ) -> list[StructureUnit]:
        root = StructureUnit(
            id="book",
            kind="book",
            label=metadata.get("title") or Path(source_path).stem,
            order=0,
            level=0,
            parent_id=None,
            children=[],
            locator={"type": "epub-book", "path": source_path},
            nav_hint=Path(source_path).name,
            source_kind="container",
            content_kind="book",
        )
        units: list[StructureUnit] = [root]

        if toc_entries:
            children_by_parent: dict[str, list[str]] = {"book": []}
            flat = sort_toc_entries(toc_entries)
            next_by_id = compute_toc_end_positions(flat, spine, doc_cache)
            for order, entry in enumerate(flat, start=1):
                start = entry["position"]
                end = next_by_id[entry["id"]]
                unit = StructureUnit(
                    id=entry["id"],
                    kind=guess_label_kind(entry["label"]),
                    label=entry["label"],
                    order=order,
                    level=entry["depth"] + 1,
                    parent_id=entry["parent_id"] or "book",
                    children=[],
                    locator={
                        "type": "epub-range",
                        "start": asdict(start),
                        "end": asdict(end),
                    },
                    nav_hint=entry["nav_hint"],
                    source_kind=entry.get("source_kind", "toc"),
                    content_kind=infer_content_kind(
                        entry["label"], guess_label_kind(entry["label"]), entry.get("source_kind", "toc")
                    ),
                    source_range={
                        "kind": "epub-range",
                        "start": asdict(start),
                        "end": asdict(end),
                        "source_path": entry["href"],
                    },
                    source_path=entry["href"],
                    warnings=list(entry.get("warnings", [])),
                )
                units.append(unit)
                children_by_parent.setdefault(unit.parent_id, []).append(unit.id)
                children_by_parent.setdefault(unit.id, [])
            for unit in units:
                unit.children = children_by_parent.get(unit.id, [])
            assign_title_paths(units, root.id)
            return units

        for order, item in enumerate(spine, start=1):
            doc = doc_cache[item["spine_index"]]
            start = Position(item["spine_index"], 0)
            end = Position(item["spine_index"], len(doc["text"]))
            unit = StructureUnit(
                id=f"spine-{item['spine_index']}",
                kind=guess_label_kind(doc["title"]),
                label=doc["title"],
                order=order,
                level=1,
                parent_id="book",
                children=[],
                locator={
                    "type": "epub-range",
                    "start": asdict(start),
                    "end": asdict(end),
                },
                nav_hint=f"spine {item['spine_index']} :: {item['href']}",
                source_kind="spine_document",
                content_kind=infer_content_kind(doc["title"], guess_label_kind(doc["title"]), "spine_document"),
                source_range={
                    "kind": "epub-range",
                    "start": asdict(start),
                    "end": asdict(end),
                    "source_path": item["href"],
                },
                source_path=item["href"],
            )
            root.children.append(unit.id)
            units.append(unit)
        assign_title_paths(units, root.id)
        return units


def detect_text_encoding(path: Path) -> str:
    with path.open("rb") as handle:
        sample = handle.read(1024 * 1024)
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if sample.startswith(bom):
            return encoding
    best = from_bytes(sample).best()
    if best and best.encoding:
        return best.encoding
    return "utf-8"


def classify_txt_heading(line: str) -> dict[str, Any] | None:
    candidate = line.strip().strip("\ufeff")
    if not candidate or len(candidate) > 120:
        return None
    for pattern, kind, level in TXT_HEADING_PATTERNS:
        match = pattern.match(candidate)
        if match:
            return {"label": match.group("label"), "kind": kind, "level": level}
    return None


def normalize_epub_path(base: PurePosixPath, href: str) -> str:
    relative = PurePosixPath(href)
    return str(base.joinpath(relative))


def flatten_html_document(doc: etree._Element) -> tuple[str, dict[str, int], list[dict[str, Any]], str]:
    body = doc.find(".//body")
    root = body if body is not None else doc
    chunks: list[str] = []
    anchors: dict[str, int] = {}
    headings: list[dict[str, Any]] = []
    title = ""

    def ensure_break() -> None:
        if chunks and not chunks[-1].endswith("\n"):
            chunks.append("\n")

    def push_text(text: str | None) -> None:
        if text:
            cleaned = normalize_whitespace(text)
            if cleaned:
                chunks.append(cleaned)

    def walk(node: etree._Element) -> None:
        nonlocal title
        tag = local_name(node.tag)
        if tag in BLOCK_TAGS:
            ensure_break()
        anchor_offset = current_length(chunks)
        node_id = node.get("id") or node.get("name")
        if node_id and node_id not in anchors:
            anchors[node_id] = anchor_offset
        push_text(node.text)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading_text = normalize_whitespace("".join(node.itertext()))
            if heading_text:
                headings.append(
                    {
                        "level": int(tag[1]),
                        "label": heading_text,
                        "anchor": node_id,
                        "char_offset": anchor_offset,
                    }
                )
                if not title:
                    title = heading_text
        for child in node:
            walk(child)
            push_text(child.tail)
        if tag in BLOCK_TAGS:
            ensure_break()

    walk(root)
    if not title:
        head_title = doc.find(".//title")
        if head_title is not None:
            title = normalize_whitespace("".join(head_title.itertext()))
    return cleanup_text("".join(chunks)), anchors, headings, title


def local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def normalize_whitespace(text: str) -> str:
    text = unescape(text.replace("\xa0", " "))
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def cleanup_text(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def current_length(chunks: list[str]) -> int:
    return sum(len(chunk) for chunk in chunks)


def parse_epub3_nav(
    nav_node: etree._Element,
    nav_href: str,
    spine_lookup: dict[str, int],
    doc_cache: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    nav_dir = PurePosixPath(nav_href).parent
    entries: list[dict[str, Any]] = []
    counter = 1

    def walk_list(list_node: etree._Element, depth: int, parent_id: str | None) -> None:
        nonlocal counter
        for li in list_node.xpath("./*[local-name()='li']"):
            link = li.xpath("./*[local-name()='a'][1]")
            if not link:
                continue
            anchor = link[0]
            href = anchor.get("href")
            if not href:
                continue
            full_href, frag = urldefrag(str(normalize_epub_path(nav_dir, href)))
            if full_href not in spine_lookup:
                continue
            position = resolve_epub_position(spine_lookup[full_href], frag, doc_cache)
            entry_id = f"toc-{counter}"
            counter += 1
            entries.append(
                {
                    "id": entry_id,
                    "parent_id": parent_id,
                    "depth": depth,
                    "label": normalize_whitespace("".join(anchor.itertext())) or Path(full_href).stem,
                    "href": full_href,
                    "fragment": frag or None,
                    "position": position,
                    "nav_hint": href,
                    "source_kind": "toc",
                    "warnings": [],
                }
            )
            child_lists = li.xpath("./*[local-name()='ol' or local-name()='ul']")
            for child_list in child_lists:
                walk_list(child_list, depth + 1, entry_id)

    for root_list in nav_node.xpath("./*[local-name()='ol' or local-name()='ul']"):
        walk_list(root_list, 0, None)
    return entries


def parse_ncx_toc(
    ncx: etree._Element,
    ncx_href: str,
    spine_lookup: dict[str, int],
    doc_cache: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
    ncx_dir = PurePosixPath(ncx_href).parent
    entries: list[dict[str, Any]] = []
    counter = 1

    def walk(points: list[etree._Element], depth: int, parent_id: str | None) -> None:
        nonlocal counter
        for point in points:
            src = point.xpath("string(./ncx:content/@src)", namespaces=ns)
            label = point.xpath("string(./ncx:navLabel/ncx:text)", namespaces=ns).strip()
            full_href, frag = urldefrag(str(normalize_epub_path(ncx_dir, src)))
            if full_href not in spine_lookup:
                continue
            position = resolve_epub_position(spine_lookup[full_href], frag, doc_cache)
            entry_id = f"toc-{counter}"
            counter += 1
            entries.append(
                {
                    "id": entry_id,
                    "parent_id": parent_id,
                    "depth": depth,
                    "label": label or Path(full_href).stem,
                    "href": full_href,
                    "fragment": frag or None,
                    "position": position,
                    "nav_hint": src,
                    "source_kind": "toc",
                    "warnings": [],
                }
            )
            walk(point.xpath("./ncx:navPoint", namespaces=ns), depth + 1, entry_id)

    walk(ncx.xpath("//ncx:navMap/ncx:navPoint", namespaces=ns), 0, None)
    return entries


def resolve_epub_position(
    spine_index: int, fragment: str, doc_cache: dict[int, dict[str, Any]]
) -> Position:
    doc = doc_cache[spine_index]
    if not fragment:
        return Position(spine_index, 0)
    offset = doc["anchors"].get(fragment)
    if offset is None:
        return Position(spine_index, 0)
    if fragment.endswith("-back"):
        heading_offset = nearest_preceding_heading_offset(doc["headings"], offset)
        if heading_offset is not None:
            return Position(spine_index, heading_offset)
    return Position(spine_index, offset)


def sort_toc_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(entries)


def compute_toc_end_positions(
    entries: list[dict[str, Any]],
    spine: list[dict[str, Any]],
    doc_cache: dict[int, dict[str, Any]],
) -> dict[str, Position]:
    end_positions: dict[str, Position] = {}
    last_spine = spine[-1]["spine_index"]
    last_end = Position(last_spine, len(doc_cache[last_spine]["text"]))
    for idx, entry in enumerate(entries):
        end = last_end
        for follower in entries[idx + 1 :]:
            if follower["depth"] <= entry["depth"] and position_after(follower["position"], entry["position"]):
                end = follower["position"]
                break
        end_positions[entry["id"]] = end
    return end_positions


def position_after(candidate: Position, current: Position) -> bool:
    return (candidate.spine_index, candidate.char_offset) > (current.spine_index, current.char_offset)


def guess_label_kind(label: str) -> str:
    lowered = label.lower()
    if re.search(r"\b(part|book)\b", lowered):
        return "part"
    if re.search(r"\bchapter\b", lowered) or re.match(
        r"^第[0-9零〇○一二三四五六七八九十百千万兩两壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]+[章节節回卷部篇集]",
        label,
    ):
        return "chapter"
    return "section"


def infer_content_kind(label: str, kind: str, source_kind: str) -> str:
    normalized = normalize_nav_label(label).lower()
    if kind == "book" or source_kind == "container":
        return "book"
    if source_kind == "fallback_chunk":
        return "unknown"
    if normalized in {"目录"} or "contents" in normalized:
        return "toc"
    if any(token in normalized for token in ("书名页", "版权页", "出版说明", "前言", "preface", "prologue", "introduction")):
        return "front_matter"
    if re.match(r"^(卷|册|篇|部|chapter|part|book|第)", normalized, flags=re.IGNORECASE):
        return "main_text"
    if kind in {"chapter", "part"}:
        return "main_text"
    return "section"


def assign_title_paths(units: list[StructureUnit], root_id: str) -> None:
    unit_map = {unit.id: unit for unit in units}
    memo: dict[str, list[str]] = {}

    def walk(unit_id: str) -> list[str]:
        if unit_id in memo:
            return memo[unit_id]
        unit = unit_map[unit_id]
        if unit.id == root_id:
            path: list[str] = []
        elif unit.parent_id is None or unit.parent_id == root_id:
            path = [unit.label]
        else:
            path = [*walk(unit.parent_id), unit.label]
        memo[unit_id] = path
        return path

    for unit in units:
        unit.title_path = walk(unit.id)


def filter_txt_duplicate_toc_blocks(headings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not headings:
        return headings
    keep = [True] * len(headings)
    idx = 0
    while idx < len(headings):
        cluster_end = idx
        while (
            cluster_end + 1 < len(headings)
            and headings[cluster_end + 1]["start_line"] - headings[cluster_end]["start_line"] <= 3
        ):
            cluster_end += 1
        cluster = headings[idx : cluster_end + 1]
        if len(cluster) >= 3 and cluster[-1]["start_line"] - cluster[0]["start_line"] <= 40:
            repeated = 0
            for item in cluster:
                if any(
                    later["label"] == item["label"] and later["kind"] == item["kind"]
                    for later in headings[cluster_end + 1 :]
                ):
                    repeated += 1
            if repeated >= max(2, len(cluster) // 2):
                for pos in range(idx, cluster_end + 1):
                    keep[pos] = False
        idx = cluster_end + 1
    return [heading for heading, is_kept in zip(headings, keep) if is_kept]


def nearest_preceding_heading_offset(headings: list[dict[str, Any]], offset: int) -> int | None:
    candidates = [heading["char_offset"] for heading in headings if heading["char_offset"] <= offset]
    if not candidates:
        return None
    return max(candidates)


def normalize_nav_label(label: str) -> str:
    return re.sub(r"[\s\u3000]+", "", label)


def parse_html_bytes(payload: bytes) -> etree._Element:
    encoding = detect_html_encoding(payload)
    parser = html.HTMLParser(encoding=encoding)
    text = payload.decode(encoding, errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    return html.fromstring(text, parser=parser)


def detect_html_encoding(payload: bytes) -> str:
    head = payload[:4096]
    xml_match = re.search(br'encoding=["\']([A-Za-z0-9._-]+)["\']', head)
    if xml_match:
        return xml_match.group(1).decode("ascii", errors="ignore")
    meta_match = re.search(br'charset=["\']?([A-Za-z0-9._-]+)', head, flags=re.IGNORECASE)
    if meta_match:
        return meta_match.group(1).decode("ascii", errors="ignore")
    return "utf-8"


def reconcile_toc_entries(
    entries: list[dict[str, Any]],
    spine: list[dict[str, Any]],
    doc_cache: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not entries:
        return entries
    spine_by_index = {item["spine_index"]: item for item in spine}
    normalized_doc_titles: dict[str, list[int]] = {}
    for item in spine:
        normalized_doc_titles.setdefault(normalize_nav_label(doc_cache[item["spine_index"]]["title"]), []).append(
            item["spine_index"]
        )

    min_spine = 0
    for entry in entries:
        current = entry["position"]
        current_title = normalize_nav_label(doc_cache[current.spine_index]["title"])
        wanted = normalize_nav_label(entry["label"])
        candidate_indexes = normalized_doc_titles.get(wanted, [])
        candidate = next((idx for idx in candidate_indexes if idx >= min_spine), None)
        suspicious = (
            current_title != wanted
            or (entry.get("fragment") or "").endswith("-back")
        )
        if suspicious and candidate is not None:
            entry["position"] = Position(candidate, 0)
            entry["href"] = spine_by_index[candidate]["href"]
            entry["fragment"] = None
            entry["nav_hint"] = spine_by_index[candidate]["href"]
            entry["source_kind"] = "reconciled_toc"
            entry.setdefault("warnings", []).append("toc target reconciled to matching spine document title")
        elif (entry.get("fragment") or "").endswith("-back"):
            entry["source_kind"] = "reconciled_toc"
            entry.setdefault("warnings", []).append("toc back-anchor normalized to enclosing heading start")
        min_spine = max(min_spine, entry["position"].spine_index)
    return entries
