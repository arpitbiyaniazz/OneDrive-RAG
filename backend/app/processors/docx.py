"""Word (.docx) parser that walks the document XML in reading order.

python-docx's helpers only see top-level paragraphs and tables and only `w:r`/`w:hyperlink` text, so
content controls, tracked insertions, fields, smart tags and text boxes would be lost. This walker
reads them all (deleted text and field instructions excluded), numbers list items the way Word shows
them, resolves custom heading styles, inlines footnotes and endnotes, and reads every header/footer.
"""
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, table_elements

logger = logging.getLogger(__name__)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_HEADING_RE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)
# Inline elements whose text is not part of the current document text.
_SKIP_INLINE = {"del", "moveFrom", "delText", "instrText", "rPr", "pPr", "fldChar", "commentReference",
                "annotationRef", "proofErr", "bookmarkStart", "bookmarkEnd", "lastRenderedPageBreak",
                "sectPr", "tblPr", "trPr", "tcPr", "softHyphen", "permStart", "permEnd", "sdtPr", "sdtEndPr"}
# Block-level wrappers whose children are paragraphs and tables.
_BLOCK_WRAPPERS = {"sdt", "sdtContent", "customXml", "smartTag", "ins", "moveTo"}
_SKIP_NOTE_TYPES = {"separator", "continuationSeparator", "continuationNotice"}


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _local(element) -> Tuple[str, str]:
    tag = element.tag if isinstance(element.tag, str) else ""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def _val(element, path: str) -> Optional[str]:
    found = element.find(path, {"w": W})
    return found.get(_w("val")) if found is not None else None


# --------------------------------------------------------------------------- styles and numbering

@dataclass
class _Style:
    name: str
    based_on: Optional[str]
    outline: Optional[int]
    num: Optional[Tuple[str, int]]


class _Styles:
    def __init__(self, document) -> None:
        self.styles: Dict[str, _Style] = {}
        self.default: Optional[str] = None
        try:
            root = document.styles.element
        except Exception:
            return
        for style in root.findall(_w("style")):
            if style.get(_w("type")) != "paragraph":
                continue
            style_id = style.get(_w("styleId"))
            outline = _val(style, "w:pPr/w:outlineLvl")
            num_id = _val(style, "w:pPr/w:numPr/w:numId")
            level = _val(style, "w:pPr/w:numPr/w:ilvl")
            self.styles[style_id] = _Style(
                name=(_val(style, "w:name") or "").strip(),
                based_on=_val(style, "w:basedOn"),
                outline=int(outline) if outline and outline.isdigit() else None,
                num=(num_id, int(level or 0)) if num_id else None,
            )
            if style.get(_w("default")) in ("1", "true"):
                self.default = style_id
        self._levels: Dict[Optional[str], Optional[int]] = {}

    def heading_level(self, style_id: Optional[str]) -> Optional[int]:
        """0 for Title, 1-9 for headings (through the base-style chain and outline levels), else None."""
        if style_id in self._levels:
            return self._levels[style_id]
        level, current, seen = None, style_id or self.default, set()
        while current and current not in seen and len(seen) < 10:
            seen.add(current)
            style = self.styles.get(current)
            if style is None:
                break
            name = style.name.lower()
            if name == "title":
                level = 0
                break
            match = _HEADING_RE.match(name)
            if match:
                level = int(match.group(1))
                break
            if style.outline is not None and style.outline < 9:
                level = style.outline + 1
                break
            current = style.based_on
        self._levels[style_id] = level
        return level

    def is_list_style(self, style_id: Optional[str]) -> bool:
        style = self.styles.get(style_id or "")
        return bool(style and style.name.lower().startswith("list"))

    def numbering(self, style_id: Optional[str]) -> Optional[Tuple[str, int]]:
        current, seen = style_id, set()
        while current and current not in seen and len(seen) < 10:
            seen.add(current)
            style = self.styles.get(current)
            if style is None:
                return None
            if style.num:
                return style.num
            current = style.based_on
        return None


@dataclass
class _Level:
    fmt: str = "decimal"
    text: str = "%1."
    start: int = 1


class _Numbering:
    """Renders list labels ("1.", "7.2", "a)") from numbering.xml, keeping counters per list."""

    def __init__(self, document) -> None:
        self.abstract: Dict[str, Dict[int, _Level]] = {}
        self.nums: Dict[str, Tuple[str, Dict[int, int]]] = {}
        self.counters: Dict[str, List[Optional[int]]] = {}
        try:
            root = document.part.numbering_part.element
        except Exception:
            return
        for abstract in root.findall(_w("abstractNum")):
            levels = {}
            for lvl in abstract.findall(_w("lvl")):
                ilvl = int(lvl.get(_w("ilvl"), "0"))
                start = _val(lvl, "w:start")
                levels[ilvl] = _Level(fmt=_val(lvl, "w:numFmt") or "decimal", text=_val(lvl, "w:lvlText") or "",
                                      start=int(start) if start and start.lstrip("-").isdigit() else 1)
            self.abstract[abstract.get(_w("abstractNumId"))] = levels
        for num in root.findall(_w("num")):
            overrides = {}
            for override in num.findall(_w("lvlOverride")):
                start = _val(override, "w:startOverride")
                if start and start.isdigit():
                    overrides[int(override.get(_w("ilvl"), "0"))] = int(start)
            self.nums[num.get(_w("numId"))] = (_val(num, "w:abstractNumId"), overrides)

    def label(self, num_id: str, ilvl: int) -> Optional[str]:
        if num_id in (None, "0") or num_id not in self.nums:
            return None
        abstract_id, overrides = self.nums[num_id]
        levels = self.abstract.get(abstract_id, {})
        level = levels.get(ilvl, _Level())
        if level.fmt == "bullet":
            return "-"
        if level.fmt == "none":
            return ""
        counters = self.counters.setdefault(num_id, [None] * 9)
        current = counters[ilvl]
        counters[ilvl] = (overrides.get(ilvl, level.start)) if current is None else current + 1
        for deeper in range(ilvl + 1, 9):
            counters[deeper] = None

        def render(match: re.Match) -> str:
            index = int(match.group(1)) - 1
            value = counters[index] if 0 <= index < 9 and counters[index] is not None else levels.get(index, _Level()).start
            return _format_number(value, levels.get(index, _Level()).fmt)

        return re.sub(r"%(\d)", render, level.text or f"%{ilvl + 1}.").strip()


def _format_number(value: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        letters, n = "", max(1, value)
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(97 + rem) + letters
        return letters.upper() if fmt == "upperLetter" else letters
    if fmt in ("lowerRoman", "upperRoman"):
        roman = _roman(max(1, value))
        return roman if fmt == "upperRoman" else roman.lower()
    if fmt == "decimalZero":
        return f"{value:02d}"
    return str(value)


def _roman(value: int) -> str:
    out = ""
    for number, letters in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                            (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while value >= number:
            out += letters
            value -= number
    return out


# --------------------------------------------------------------------------- text extraction

@dataclass
class _Context:
    styles: _Styles
    numbering: _Numbering
    links: Dict[str, str]
    notes: Dict[str, str] = field(default_factory=dict)


def _paragraph_text(p, ctx: _Context) -> Tuple[str, List]:
    """Visible text of a paragraph (insertions, fields, controls included) and its text boxes."""
    parts: List[str] = []
    boxes: List = []

    def walk(element) -> None:
        for child in element:
            ns, tag = _local(child)
            if ns == W:
                if tag in _SKIP_INLINE:
                    continue
                if tag == "t":
                    parts.append(child.text or "")
                elif tag == "tab":
                    parts.append("\t")
                elif tag in ("br", "cr"):
                    parts.append("\n")
                elif tag == "noBreakHyphen":
                    parts.append("-")
                elif tag in ("footnoteReference", "endnoteReference"):
                    key = ("f" if tag == "footnoteReference" else "e") + (child.get(_w("id")) or "")
                    note = ctx.notes.get(key)
                    if note:
                        parts.append(f" ({'Footnote' if key[0] == 'f' else 'Endnote'}: {note})")
                elif tag == "hyperlink":
                    start = len(parts)
                    walk(child)
                    url = ctx.links.get(child.get(f"{{{R}}}id") or "")
                    if url and url.startswith(("http://", "https://")) and url not in "".join(parts[start:]):
                        parts.append(f" ({url})")
                elif tag == "txbxContent":
                    boxes.append(child)
                else:
                    walk(child)  # r, sdt, sdtContent, ins, moveTo, fldSimple, smartTag, customXml, dir, bdo, ...
            elif ns == MC and tag == "AlternateContent":
                choice = child.find(f"{{{MC}}}Choice")
                walk(choice if choice is not None else (child.find(f"{{{MC}}}Fallback") or []))
            elif ns == MATH:
                parts.append(" ".join(t.text or "" for t in child.iter(f"{{{MATH}}}t")))
            else:  # drawings and VML shapes: only their text boxes carry text
                boxes.extend(child.iter(_w("txbxContent")))

    walk(p)
    text = "".join(parts)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text, boxes


def _paragraph_props(p, ctx: _Context) -> Tuple[Optional[int], Optional[str], bool]:
    """(heading level, list label, is list item) for a paragraph."""
    style_id = _val(p, "w:pPr/w:pStyle")
    outline = _val(p, "w:pPr/w:outlineLvl")
    level = int(outline) + 1 if outline and outline.isdigit() and int(outline) < 9 else ctx.styles.heading_level(style_id)
    num_id, ilvl = _val(p, "w:pPr/w:numPr/w:numId"), _val(p, "w:pPr/w:numPr/w:ilvl")
    if num_id is None:
        style_num = ctx.styles.numbering(style_id)
        if style_num:
            num_id, ilvl = style_num[0], str(style_num[1])
    label = ctx.numbering.label(num_id, int(ilvl or 0)) if num_id else None
    is_list = label is not None or ctx.styles.is_list_style(style_id)
    return level, label, is_list


def _cell_text(tc, ctx: _Context) -> str:
    parts = []
    for block in _blocks(tc):
        if _local(block)[1] == "p":
            text, boxes = _paragraph_text(block, ctx)
            if text:
                parts.append(text)
            parts.extend(t for t, _ in (_paragraph_text(bp, ctx) for box in boxes for bp in box.iter(_w("p"))) if t)
        else:
            for row in _table_rows(block, ctx):
                parts.append(" / ".join(c for c in row if c))
    return " ".join(parts)


def _blocks(container):
    """Paragraphs and tables of a container in order, looking through content controls and wrappers."""
    for child in container:
        ns, tag = _local(child)
        if ns != W:
            continue
        if tag in ("p", "tbl"):
            yield child
        elif tag in _BLOCK_WRAPPERS:
            yield from _blocks(child)


def _rows(tbl):
    for child in tbl:
        ns, tag = _local(child)
        if ns == W and tag == "tr":
            yield child
        elif ns == W and tag in _BLOCK_WRAPPERS:
            yield from _rows(child)


def _cells(tr):
    for child in tr:
        ns, tag = _local(child)
        if ns == W and tag == "tc":
            yield child
        elif ns == W and tag in _BLOCK_WRAPPERS:
            yield from _cells(child)


def _table_rows(tbl, ctx: _Context) -> List[List[str]]:
    """Rows on the table grid: merged cells appear once (horizontal) or carry down (vertical)."""
    rows: List[List[str]] = []
    above: Dict[int, str] = {}
    for tr in _rows(tbl):
        cells: List[str] = []
        before = _val(tr, "w:trPr/w:gridBefore")
        cells.extend([""] * (int(before) if before and before.isdigit() else 0))
        for tc in _cells(tr):
            span = _val(tc, "w:tcPr/w:gridSpan")
            span = int(span) if span and span.isdigit() else 1
            merge = tc.find("w:tcPr/w:vMerge", {"w": W})
            column = len(cells)
            if merge is not None and merge.get(_w("val")) in (None, "continue"):
                text = above.get(column, "")
            else:
                text = _cell_text(tc, ctx)
            above[column] = text
            cells.append(text)
            cells.extend([""] * (span - 1))
        if any(cells):
            rows.append(cells)
    return rows


# --------------------------------------------------------------------------- document walk

def parse_docx(data: bytes) -> ParsedDocument:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        logger.info("Unreadable Word document: %s", exc)
        raise UnsupportedDocumentError("Unreadable Word document") from exc

    ctx = _Context(styles=_Styles(document), numbering=_Numbering(document), links=_external_links(document.part))
    ctx.notes = _notes(document.part)
    elements: List[DocumentElement] = []
    headings: List[str] = []  # heading text per level, index 0 = level 1
    buffer: List[str] = []
    pending_heading: List[Optional[str]] = [None]  # heading with no content under it yet

    def section() -> Optional[str]:
        return " > ".join(h for h in headings if h) or None

    def flush() -> None:
        if buffer:
            elements.append(DocumentElement(text="\n".join(buffer), section=section()))
            buffer.clear()
            pending_heading[0] = None

    def emit_paragraph(p) -> None:
        text, boxes = _paragraph_text(p, ctx)
        level, label, is_list = _paragraph_props(p, ctx)
        if text:
            if level == 0:  # the document title: its own element, never replaced by Heading 1
                flush()
                elements.append(DocumentElement(text=text, section=None))
            elif level:
                flush()
                if pending_heading[0]:
                    # A heading followed directly by another one would otherwise vanish.
                    elements.append(DocumentElement(text=pending_heading[0], section=section()))
                title = f"{label} {text}".strip() if label else text
                del headings[level - 1:]
                headings.extend([""] * (level - 1 - len(headings)))
                headings.append(title)
                pending_heading[0] = title
            elif is_list:
                ilvl = _val(p, "w:pPr/w:numPr/w:ilvl")
                indent = "  " * int(ilvl) if ilvl and ilvl.isdigit() else ""
                marker = label if label else "-"
                buffer.append(f"{indent}{marker} {text}" if marker else f"{indent}{text}")
            else:
                buffer.append(text)
        for box in boxes:  # text boxes become their own paragraphs, in place
            for box_p in box.iter(_w("p")):
                box_text, _ = _paragraph_text(box_p, ctx)
                if box_text:
                    buffer.append(box_text)

    def emit_table(tbl) -> None:
        flush()
        rows = _table_rows(tbl, ctx)
        if not rows:
            return
        width = max(len(r) for r in rows)
        # A full-width first row ("Table 4: Professional fees") is a caption, not the header.
        if len(rows) >= 3 and sum(1 for c in rows[0] if c) == 1 and width > 1:
            elements.append(DocumentElement(text=next(c for c in rows[0] if c), section=section()))
            rows = rows[1:]
        header, body = rows[0], rows[1:] or [[""] * len(rows[0])]
        elements.extend(table_elements(header, body, section()))
        pending_heading[0] = None

    for block in _blocks(document.element.body):
        if _local(block)[1] == "p":
            emit_paragraph(block)
        else:
            emit_table(block)
    flush()
    if pending_heading[0]:
        elements.append(DocumentElement(text=pending_heading[0], section=section()))

    header_footer = _header_footer_text(document.part, ctx)
    if header_footer:
        elements.append(DocumentElement(text=header_footer, section="Page header and footer"))
    return ParsedDocument(elements=elements, file_type="docx")


def _external_links(part) -> Dict[str, str]:
    links = {}
    try:
        for rel_id, rel in part.rels.items():
            if rel.is_external and rel.reltype == RT.HYPERLINK:
                links[rel_id] = rel.target_ref
    except Exception:
        pass
    return links


def _notes(part) -> Dict[str, str]:
    """Footnote/endnote text by id ("f3", "e1"), separators skipped."""
    notes: Dict[str, str] = {}
    empty = _Context(styles=_Styles.__new__(_Styles), numbering=_Numbering.__new__(_Numbering), links={})
    empty.styles.styles, empty.styles.default, empty.styles._levels = {}, None, {}
    empty.numbering.abstract, empty.numbering.nums, empty.numbering.counters = {}, {}, {}
    for rel in list(part.rels.values()):
        kind = "f" if rel.reltype == RT.FOOTNOTES else "e" if rel.reltype == RT.ENDNOTES else None
        if kind is None or rel.is_external:
            continue
        try:
            root = etree.fromstring(rel.target_part.blob)
        except Exception:
            continue
        for note in root:
            if _local(note)[1] not in ("footnote", "endnote") or note.get(_w("type")) in _SKIP_NOTE_TYPES:
                continue
            text = " ".join(t for t, _ in (_paragraph_text(p, empty) for p in note.iter(_w("p"))) if t)
            if text:
                notes[kind + (note.get(_w("id")) or "")] = text
    return notes


def _header_footer_text(part, ctx: _Context) -> str:
    """Every header and footer (default, first page, even pages), tables and controls included."""
    seen: List[str] = []
    for rel in list(part.rels.values()):
        if rel.is_external or rel.reltype not in (RT.HEADER, RT.FOOTER):
            continue
        try:
            root = rel.target_part.element
        except Exception:
            continue
        for block in _blocks(root):
            if _local(block)[1] == "p":
                lines = [_paragraph_text(block, ctx)[0]]
            else:
                lines = [" | ".join(c for c in row if c) for row in _table_rows(block, ctx)]
            for text in lines:
                if text and text not in seen:
                    seen.append(text)
    return "\n".join(seen)
