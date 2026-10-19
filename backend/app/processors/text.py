import csv
import io
import re
from html.parser import HTMLParser
from typing import List, Optional

from charset_normalizer import from_bytes

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, table_elements
from app.processors.detect import utf16_without_bom
from app.processors.xlsx import rows_to_elements

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_MD_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SETEXT = re.compile(r"^(=+|-+)\s*$")

# Code pages of the Latin alphabet: for short texts statistics cannot tell them apart, and cp1252
# is by far the most common. Characters that cp1252 produces from Central European text instead.
_LATIN_CODEPAGES = {
    "cp1250", "cp1252", "cp1254", "cp1257", "cp775", "cp850", "cp852", "cp858", "latin_1", "iso8859_1", "iso8859_2",
    "iso8859_3", "iso8859_4", "iso8859_9", "iso8859_10", "iso8859_13", "iso8859_14", "iso8859_15", "iso8859_16",
    "mac_latin2", "mac_roman", "mac_iceland", "mac_turkish",
}
_CP1252_MOJIBAKE = set("±³¹¶¼½¾¤¦¨¯´¸¬")


def decode_text(data: bytes) -> str:
    """BOM sniffing, then strict UTF-8, then statistical detection with a cp1252 preference."""
    for bom, encoding in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    if b"\x00" in data[:8192]:
        utf16 = utf16_without_bom(data)
        if utf16:
            return data.decode(utf16, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best = from_bytes(data[:200_000]).best()
    guess = best.encoding.replace("-", "_").lower() if best is not None and best.encoding else None
    if guess is None or guess in _LATIN_CODEPAGES:
        # Western text: prefer cp1252 unless it turns Central European letters into symbols (ł -> ³).
        try:
            text = data.decode("cp1252")
            if guess is None or not (set(text) & _CP1252_MOJIBAKE):
                return text
        except UnicodeDecodeError:
            pass
    if guess:
        return data.decode(guess, errors="replace")
    return data.decode("latin-1")


def parse_text(data: bytes, file_type: str) -> ParsedDocument:
    text = decode_text(data).replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if not text.strip():
        raise UnsupportedDocumentError("The file has no text")
    if file_type == "txt" and _looks_tab_separated(text):
        file_type = "csv"  # e.g. Excel's "Unicode Text" export: tab-separated values in a .txt file
    if file_type == "md":
        elements = _markdown_elements(text)
    elif file_type == "csv":
        warnings: List[str] = []
        elements = _csv_elements(text, warnings)
        return ParsedDocument(elements=elements, file_type="csv", warnings=warnings)
    elif file_type == "html":
        elements = _html_elements(text)
    else:
        elements = [DocumentElement(text=text.strip())]
    if not elements:
        raise UnsupportedDocumentError("The file has no text")
    return ParsedDocument(elements=elements, file_type=file_type)


def _looks_tab_separated(text: str) -> bool:
    lines = [line for line in text.split("\n")[:60] if line.strip()]
    if len(lines) < 3:
        return False
    counts = [line.count("\t") for line in lines]
    common = max(set(counts), key=counts.count)
    return common >= 1 and counts.count(common) >= len(lines) * 0.8


def _markdown_elements(text: str) -> List[DocumentElement]:
    elements: List[DocumentElement] = []
    headings: List[str] = []
    buffer: List[str] = []
    fence: Optional[str] = None

    def section() -> Optional[str]:
        return " > ".join(h for h in headings if h) or None

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            elements.append(DocumentElement(text=body, section=section()))
        buffer.clear()

    def set_heading(level: int, title: str) -> None:
        flush()
        del headings[level - 1:]
        headings.extend([""] * (level - 1 - len(headings)))
        headings.append(title.strip())

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        fence_match = _MD_FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            buffer.append(line)
            i += 1
            continue
        if fence is None:
            heading = _MD_HEADING.match(line)
            if heading:
                set_heading(len(heading.group(1)), heading.group(2))
                i += 1
                continue
            # Setext headings: a text line underlined with === (level 1) or --- (level 2).
            if buffer and buffer[-1].strip() and _SETEXT.match(line) and not buffer[-1].lstrip().startswith(("-", "*", "|", ">")):
                title = buffer.pop()
                set_heading(1 if line.strip().startswith("=") else 2, title)
                i += 1
                continue
            # Tables: header row, separator row, body rows -> row groups that repeat the header.
            if line.lstrip().startswith("|") and i + 1 < len(lines) and _MD_TABLE_SEPARATOR.match(lines[i + 1]):
                header = _md_cells(line)
                rows = []
                i += 2
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    rows.append(_md_cells(lines[i]))
                    i += 1
                caption = buffer.pop() if buffer and buffer[-1].strip() and len(buffer[-1]) < 200 else None
                flush()
                if caption:
                    elements.append(DocumentElement(text=caption.strip(), section=section()))
                if rows:
                    elements.extend(table_elements(header, rows, section()))
                else:
                    elements.append(DocumentElement(text=" | ".join(header), section=section()))
                continue
        buffer.append(line)
        i += 1
    flush()
    return elements


def _md_cells(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _csv_elements(text: str, warnings: List[str]) -> List[DocumentElement]:
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]
    return rows_to_elements(rows, "Table", warnings)


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "table", "dl", "blockquote", "pre",
             "header", "footer", "nav", "aside", "main", "figure", "figcaption", "caption", "ul", "ol", "hr",
             "details", "summary", "form", "fieldset", "address"}
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: List[DocumentElement] = []
        self.parts: List[str] = []
        self.headings: List[str] = []
        self.title: Optional[str] = None
        self._skip = 0
        self._in_head = False
        self._in_title = False
        self._heading: Optional[List[str]] = None
        self._heading_level = 0
        self._href: Optional[str] = None
        self._link_text: List[str] = []
        self._table: Optional[List[List[str]]] = None
        self._cell: Optional[List[str]] = None
        self._table_depth = 0

    # -- helpers
    def _section(self) -> Optional[str]:
        path = " > ".join(h for h in self.headings if h)
        return path or self.title

    def _flush(self) -> None:
        body = re.sub(r"[ \t]+", " ", "".join(self.parts))
        body = re.sub(r" *\n\s*\n+", "\n\n", re.sub(r" *\n *", "\n", body)).strip()
        if body:
            self.elements.append(DocumentElement(text=body, section=self._section()))
        self.parts.clear()

    def _write(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        elif self._heading is not None:
            self._heading.append(text)
        else:
            self.parts.append(text)

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "head":
            self._in_head = True
        elif tag == "title" and self._in_head:
            self._in_title = True
        elif tag in self.HEADINGS and self._table is None:
            self._flush()
            self._heading, self._heading_level = [], int(tag[1])
        elif tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif tag == "tr" and self._table is not None and self._table_depth == 1:
            self._table.append([])
        elif tag in ("td", "th") and self._table is not None and self._table_depth == 1:
            self._cell = []
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self._href = href if href.startswith(("http://", "https://")) else None
            self._link_text = []
        elif tag in self.BLOCK or tag == "dt":
            self._write("\n")
        if tag == "li":
            self._write("- ")
        elif tag == "dd":
            self._write(": ")  # "Gym allowance: 45 EUR per month"

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False
        elif tag in self.HEADINGS and self._heading is not None:
            title = " ".join("".join(self._heading).split())
            del self.headings[self._heading_level - 1:]
            self.headings.extend([""] * (self._heading_level - 1 - len(self.headings)))
            self.headings.append(title)
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._table:
                self._table[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
            if self._table_depth == 0 and self._table is not None:
                rows = [r for r in self._table if any(r)]
                self._table = None
                if len(rows) >= 2:
                    self._flush()
                    self.elements.extend(table_elements(rows[0], rows[1:], self._section()))
                elif rows:
                    self._write("\n" + " | ".join(rows[0]) + "\n")
        elif tag == "a":
            text = "".join(self._link_text).strip()
            if self._href and self._href not in text:
                self._write(f" ({self._href})")
            self._href = None
        elif tag in self.BLOCK or tag == "dd":
            self._write("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title = ((self.title or "") + data).strip()
            return
        if self._in_head:
            return
        if self._href is not None:
            self._link_text.append(data)
        self._write(data)


def _html_elements(text: str) -> List[DocumentElement]:
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    parser._flush()
    return parser.elements
