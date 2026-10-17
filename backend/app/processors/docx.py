import io
import re
from typing import List, Optional

import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, table_elements

_HEADING_RE = re.compile(r"^heading\s*(\d)", re.IGNORECASE)


def parse_docx(data: bytes) -> ParsedDocument:
    """Walks the body in reading order: headings build a section path, tables stay in place."""
    try:
        document: DocxDocument = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise UnsupportedDocumentError(f"Unreadable Word document: {exc}") from exc

    elements: List[DocumentElement] = []
    headings: List[str] = []  # heading text per level, index 0 = level 1
    buffer: List[str] = []

    def section() -> Optional[str]:
        return " > ".join(h for h in headings if h) or None

    def flush() -> None:
        if buffer:
            elements.append(DocumentElement(text="\n".join(buffer), section=section()))
            buffer.clear()

    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            level = _heading_level(block)
            if level:
                flush()
                del headings[level - 1:]
                headings.extend([""] * (level - 1 - len(headings)))
                headings.append(text)
            elif _is_list_item(block):
                buffer.append(f"- {text}")
            else:
                buffer.append(text)
        else:
            flush()
            rows = _table_rows(block)
            if rows:
                header, body = rows[0], rows[1:] or [[""] * len(rows[0])]
                elements.extend(table_elements(header, body, section()))

    flush()

    header_footer = _header_footer_text(document)
    if header_footer:
        elements.append(DocumentElement(text=header_footer, section="Page header and footer"))
    return ParsedDocument(elements=elements, file_type="docx")


def _iter_blocks(document: DocxDocument):
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def _heading_level(paragraph: Paragraph) -> int:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name.lower() == "title":
        return 1
    match = _HEADING_RE.match(name)
    return min(int(match.group(1)), 6) if match else 0


def _is_list_item(paragraph: Paragraph) -> bool:
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None:
        return True
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    return name.lower().startswith("list")


def _cell_text(cell) -> str:
    parts = [p.text.strip() for p in cell.paragraphs if p.text.strip()]
    for nested in cell.tables:
        for row in _table_rows(nested):
            parts.append(" / ".join(c for c in row if c))
    return " ".join(parts)


def _table_rows(table: Table) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in table.rows:
        cells, previous = [], None
        for cell in row.cells:
            # Horizontally merged cells are returned repeatedly as the same element.
            cells.append("" if cell._tc is previous else _cell_text(cell))
            previous = cell._tc
        if any(cells):
            rows.append(cells)
    return rows


def _header_footer_text(document: DocxDocument) -> str:
    seen: List[str] = []
    for sec in document.sections:
        for part in (sec.header, sec.footer):
            if part is None or part.is_linked_to_previous:
                continue
            for p in part.paragraphs:
                text = p.text.strip()
                if text and text not in seen:
                    seen.append(text)
    return "\n".join(seen)
