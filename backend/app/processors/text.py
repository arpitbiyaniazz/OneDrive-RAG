import csv
import io
import re
from html.parser import HTMLParser
from typing import List, Optional

from charset_normalizer import from_bytes

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError
from app.processors.xlsx import rows_to_elements

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def decode_text(data: bytes) -> str:
    """BOM sniffing, then strict UTF-8, then statistical detection, then cp1252."""
    for bom, encoding in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Statistical detection is unreliable on short samples; cp1252 is the most common legacy encoding.
    if len(data) >= 2000:
        best = from_bytes(data[:200_000]).best()
        if best is not None and best.encoding:
            return data.decode(best.encoding, errors="replace")
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_text(data: bytes, file_type: str) -> ParsedDocument:
    text = decode_text(data).replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if not text.strip():
        raise UnsupportedDocumentError("The file has no text")
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


def _markdown_elements(text: str) -> List[DocumentElement]:
    elements: List[DocumentElement] = []
    headings: List[str] = []
    buffer: List[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            elements.append(DocumentElement(text=body, section=" > ".join(h for h in headings if h) or None))
        buffer.clear()

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _MD_HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            del headings[level - 1:]
            headings.extend([""] * (level - 1 - len(headings)))
            headings.append(match.group(2))
        else:
            buffer.append(line)
    flush()
    return elements


def _csv_elements(text: str, warnings: List[str]) -> List[DocumentElement]:
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]
    return rows_to_elements(rows, "Table", warnings)


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0
        self.title: Optional[str] = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data.strip()
        elif not self._skip:
            self.parts.append(data)


def _html_elements(text: str) -> List[DocumentElement]:
    parser = _HTMLText()
    parser.feed(text)
    body = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return [DocumentElement(text=body, section=parser.title)] if body else []
