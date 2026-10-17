import re
from collections import Counter
from typing import Dict, List, Optional

import pymupdf

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, markdown_table

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_DIGITS = re.compile(r"\d+")


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnsupportedDocumentError(f"Unreadable PDF: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise UnsupportedDocumentError("Password-protected PDFs are not supported")

    try:
        sections = _section_titles(doc)
        pages: List[Dict] = []
        image_only_pages: List[int] = []
        for index, page in enumerate(doc):
            tables = _find_tables(page)
            boxes = [t.bbox for t in tables]
            lines: List[str] = []
            for block in page.get_text("blocks", sort=True):
                x0, y0, x1, y1, text = block[:5]
                if block[6] != 0 or not text.strip():  # skip image blocks
                    continue
                if any(_overlaps((x0, y0, x1, y1), box) for box in boxes):
                    continue
                lines.extend(line.rstrip() for line in text.splitlines())
            table_texts = []
            for table in tables:
                rows = [[c or "" for c in row] for row in table.extract() if any(row)]
                if len(rows) >= 2:
                    table_texts.append(markdown_table(rows))
            if not lines and not table_texts and page.get_images():
                image_only_pages.append(index + 1)
            pages.append({"number": index + 1, "lines": lines, "tables": table_texts})
    finally:
        doc.close()

    repeated = _repeated_lines(pages)
    elements: List[DocumentElement] = []
    for page in pages:
        body = [line for line in page["lines"] if _normalize(line) not in repeated]
        text = _HYPHEN_BREAK.sub(r"\1\2", "\n".join(body)).strip()
        section = sections.get(page["number"])
        if text:
            elements.append(DocumentElement(text=text, section=section, page_number=page["number"]))
        for table_text in page["tables"]:
            elements.append(DocumentElement(text=table_text, section=section, page_number=page["number"], kind="table"))

    if repeated:
        # Running headers/footers are removed from every page but kept once (they may hold facts).
        originals = []
        for page in pages:
            for line in page["lines"]:
                key = _normalize(line)
                if key in repeated and line.strip() and line.strip() not in originals:
                    originals.append(line.strip())
                    repeated.discard(key)
        if originals:
            elements.append(DocumentElement(text="\n".join(originals), section="Page header and footer"))

    if not elements:
        raise UnsupportedDocumentError("Scanned PDF without a text layer (OCR is not supported)")

    warnings = []
    if image_only_pages:
        warnings.append(f"Pages without a text layer were skipped (no OCR): {image_only_pages[:20]}")
    return ParsedDocument(elements=elements, file_type="pdf", warnings=warnings)


def _find_tables(page) -> list:
    try:
        return [t for t in page.find_tables().tables if t.row_count >= 2 and t.col_count >= 2]
    except Exception:
        return []


def _section_titles(doc) -> Dict[int, str]:
    """Maps each page to the most recent bookmark (table of contents) title."""
    toc = doc.get_toc(simple=True)
    if not toc:
        return {}
    titles: Dict[int, str] = {}
    entries = sorted((page, title.strip()) for _, title, page in toc if page > 0 and title.strip())
    current: Optional[str] = None
    pointer = 0
    for number in range(1, doc.page_count + 1):
        while pointer < len(entries) and entries[pointer][0] <= number:
            current = entries[pointer][1]
            pointer += 1
        if current:
            titles[number] = current
    return titles


def _normalize(line: str) -> str:
    return _DIGITS.sub("#", line.strip().lower())


def _repeated_lines(pages: List[Dict]) -> set:
    """Lines (digits ignored) present on at least 60% of pages of a 3+ page document."""
    if len(pages) < 3:
        return set()
    counts: Counter = Counter()
    for page in pages:
        counts.update({_normalize(line) for line in page["lines"] if line.strip()})
    threshold = max(3, int(len(pages) * 0.6))
    return {line for line, count in counts.items() if count >= threshold and len(line) < 200}


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])
