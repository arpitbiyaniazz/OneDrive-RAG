import logging
import re
from collections import Counter
from typing import Dict, List, Optional

import pymupdf

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, table_elements

logger = logging.getLogger(__name__)

# Ligatures (ﬁ, ﬂ) are expanded so "financial" matches; whitespace is preserved for layout.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_BLOCKS & ~pymupdf.TEXT_PRESERVE_LIGATURES
# "infor-\nmation" -> "information"; codes, numbers and compounds keep their hyphen.
_HYPHEN_BREAK = re.compile(r"([\w-]*?)([^\W\d_])-\n([^\W\d_])")
_DIGITS = re.compile(r"\d+")
_PAGE_WORDS = {"page", "pages", "p", "pg", "of", "seite", "von", "pagina", "página", "de", "sur", "van", "blad",
               "sida", "av", "strona", "z", "side", "af"}
# A page is treated as scanned when images cover most of it and it has almost no real text.
_SCAN_COVERAGE = 0.5
_SCAN_MAX_TEXT_CHARS = 200
_MARGIN_BAND = 0.08  # top and bottom 8% of the page hold running headers and footers
_PROSE_CELL_CHARS = 120  # "tables" whose cells are paragraphs are ruled page layouts, not tables
MAX_PAGES = 2000


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.info("Unreadable PDF: %s", exc)
        raise UnsupportedDocumentError("Unreadable PDF") from exc
    if doc.needs_pass:
        doc.close()
        raise UnsupportedDocumentError("Password-protected PDFs are not supported")

    page_count = doc.page_count
    try:
        sections = _section_titles(doc)
        pages: List[Dict] = []
        scanned_pages: List[int] = []
        previous_header: Optional[List[str]] = None
        for index, page in enumerate(doc):
            if index >= MAX_PAGES:
                break
            tables = _find_tables(page)
            boxes = [t.bbox for t in tables]
            lines: List[str] = []
            margin_lines: List[str] = []  # lines in the top/bottom band: running header/footer candidates
            top, bottom = page.rect.y0 + page.rect.height * _MARGIN_BAND, page.rect.y1 - page.rect.height * _MARGIN_BAND
            blocks = [b for b in page.get_text("blocks", flags=_TEXT_FLAGS) if b[6] == 0 and b[4].strip()]
            for block in _reading_order(blocks):
                x0, y0, x1, y1, text = block[:5]
                if any(_mostly_inside((x0, y0, x1, y1), box) for box in boxes):
                    continue  # the table extraction has this text
                block_lines = [line.rstrip() for line in text.splitlines()]
                lines.extend(block_lines)
                if y1 <= top or y0 >= bottom:
                    margin_lines.extend(block_lines)
            lines.extend(_annotation_notes(page))

            table_parts = []
            for table in tables:
                rows = [[_clean_cell(c) for c in row] for row in table.extract() if any(row)]
                if len(rows) < 2:
                    continue
                header, body = rows[0], rows[1:]
                # A table continued from the previous page usually starts without its header row.
                if previous_header and len(header) == len(previous_header) and header != previous_header \
                        and _looks_like_data(header):
                    header, body = previous_header, rows
                previous_header = header
                table_parts.append((header, body))

            text_chars = sum(len(line.strip()) for line in lines) + sum(len(" ".join(r)) for _, b in table_parts for r in b)
            if text_chars < _SCAN_MAX_TEXT_CHARS and page.get_images() and (
                text_chars == 0 or _image_coverage(page) >= _SCAN_COVERAGE
            ):
                scanned_pages.append(index + 1)
            pages.append({"number": index + 1, "lines": lines, "margin": margin_lines, "tables": table_parts})
    finally:
        doc.close()

    repeated = _repeated_lines(pages)
    elements: List[DocumentElement] = []
    for page in pages:
        margin = {_normalize(line) for line in page["margin"]}
        body = [line for line in page["lines"] if not (_normalize(line) in repeated and _normalize(line) in margin)]
        text = _dehyphenate("\n".join(body)).strip()
        section = sections.get(page["number"])
        if text:
            elements.append(DocumentElement(text=text, section=section, page_number=page["number"]))
        for header, rows in page["tables"]:
            elements.extend(table_elements(header, rows, section, page_number=page["number"]))

    if repeated:
        # Running headers/footers are removed from every page but kept once (they may hold facts).
        originals = []
        for page in pages:
            for line in page["margin"]:
                key = _normalize(line)
                # Page numbers and other letter-free lines carry no facts.
                if key in repeated and any(ch.isalpha() for ch in line) and line.strip() not in originals:
                    originals.append(line.strip())
                    repeated.discard(key)
        if originals:
            elements.append(DocumentElement(text="\n".join(originals), section="Page header and footer"))

    if scanned_pages and (not elements or len(scanned_pages) == len(pages)):
        raise UnsupportedDocumentError("Scanned PDF without a text layer (OCR is turned off)",
                                       needs_ocr=True, file_type="pdf")
    if not elements:
        raise UnsupportedDocumentError("The PDF contains no text")
    warnings = [f"Only the first {MAX_PAGES} of {page_count} pages were indexed"] if page_count > MAX_PAGES else []
    # The indexer runs OCR on scanned pages when it is enabled, and reports them otherwise.
    return ParsedDocument(elements=elements, file_type="pdf", warnings=warnings, ocr_pages=scanned_pages)


def _reading_order(blocks: list) -> list:
    """Orders text blocks for reading, column by column.

    Blocks that span most of the text width (titles, single-column paragraphs) separate bands; inside
    a band, a left and a right column are read one after the other instead of line by line across.
    """
    if len(blocks) < 2:
        return blocks
    left_edge, right_edge = min(b[0] for b in blocks), max(b[2] for b in blocks)
    width = right_edge - left_edge or 1.0
    middle = left_edge + width / 2
    ordered, band = [], []

    def flush() -> None:
        left = [b for b in band if b[2] <= middle + width * 0.05]
        right = [b for b in band if b[0] >= middle - width * 0.05 and b not in left]
        rest = [b for b in band if b not in left and b not in right]
        if left and right and not rest:
            ordered.extend(sorted(left, key=lambda b: (b[1], b[0])) + sorted(right, key=lambda b: (b[1], b[0])))
        else:
            ordered.extend(sorted(band, key=lambda b: (round(b[1], 1), b[0])))
        band.clear()

    for block in sorted(blocks, key=lambda b: (b[1], b[0])):
        if block[2] - block[0] > width * 0.6:  # full-width block
            flush()
            ordered.append(block)
        else:
            band.append(block)
    flush()
    return ordered


def _dehyphenate(text: str) -> str:
    def join(match: re.Match) -> str:
        before, last, first = match.group(1), match.group(2), match.group(3)
        if last.islower() and first.islower() and "-" not in before:
            return f"{before}{last}{first}"
        return f"{before}{last}-{first}"  # "state-of-the-\nart", "Jean-\nPierre": keep the hyphen

    return _HYPHEN_BREAK.sub(join, text)


def _annotation_notes(page) -> List[str]:
    """Comments on sticky notes (FreeText annotations are already part of the page text)."""
    notes = []
    try:
        for annot in page.annots() or []:
            if annot.type[0] == pymupdf.PDF_ANNOT_TEXT:
                content = (annot.info.get("content") or "").strip()
                if content:
                    notes.append(f"Note: {content}")
    except Exception:
        return []
    return notes


def _clean_cell(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def _looks_like_data(row: List[str]) -> bool:
    filled = [c for c in row if c]
    return bool(filled) and sum(any(ch.isdigit() for ch in c) for c in filled) >= max(1, len(filled) // 2)


def _find_tables(page) -> list:
    # Table detection follows ruling lines; pages without vector drawings cannot have such tables,
    # and skipping them avoids the most expensive step for text-only pages.
    try:
        if not page.get_cdrawings():
            return []
        tables = [t for t in page.find_tables().tables if t.row_count >= 2 and t.col_count >= 2]
    except Exception:
        return []
    return [t for t in tables if not _is_ruled_prose(t)]


def _is_ruled_prose(table) -> bool:
    try:
        cells = [c for row in table.extract() for c in row if c and c.strip()]
    except Exception:
        return True
    return bool(cells) and table.col_count <= 3 and sum(len(c) for c in cells) / len(cells) > _PROSE_CELL_CHARS


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
    """Comparison key for running headers: page-number lines ignore their digits ("Page 3 of 10"),
    anything else must repeat exactly (per-page values such as invoice numbers are facts)."""
    text = line.strip().lower()
    words = re.sub(r"[\d\W_]+", " ", text).split()
    if not words or any(w in _PAGE_WORDS for w in words):
        return _DIGITS.sub("#", text)
    return text


def _repeated_lines(pages: List[Dict]) -> set:
    """Header/footer-band lines present on at least 60% of pages of a 3+ page document.

    Only the page margins are considered: body lines that repeat with different numbers (for
    example "Balance: 1,204.50" on every page of a statement) are facts, not running headers.
    """
    if len(pages) < 3:
        return set()
    counts: Counter = Counter()
    for page in pages:
        counts.update({_normalize(line) for line in page["margin"] if line.strip()})
    threshold = max(3, int(len(pages) * 0.6))
    return {line for line, count in counts.items() if count >= threshold and len(line) < 200}


def _image_coverage(page) -> float:
    area = abs(page.rect)
    if not area:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        covered += abs(pymupdf.Rect(info["bbox"]) & page.rect)
    return min(1.0, covered / area)


def _mostly_inside(block, box, share: float = 0.6) -> bool:
    """True when most of the text block lies inside the table (a caption touching the border is not)."""
    x0, y0, x1, y1 = block
    area = max((x1 - x0) * (y1 - y0), 1e-6)
    ix = max(0.0, min(x1, box[2]) - max(x0, box[0]))
    iy = max(0.0, min(y1, box[3]) - max(y0, box[1]))
    return ix * iy / area >= share
