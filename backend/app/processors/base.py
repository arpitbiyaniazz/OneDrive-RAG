from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class UnsupportedDocumentError(Exception):
    """The file cannot be indexed (unsupported format, empty, encrypted, or no text layer).

    `needs_ocr` marks files whose text is only in images (scans, photos); `file_type` is then the
    detected format so OCR can take over.
    """

    def __init__(self, reason: str, needs_ocr: bool = False, file_type: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.needs_ocr = needs_ocr
        self.file_type = file_type


@dataclass
class DocumentElement:
    """A structural unit of a document in reading order.

    kind="text" elements may be merged with neighbours by the chunker; kind="table" elements are
    already sized by the parser (row groups with the header repeated) and are kept intact.
    """

    text: str
    section: Optional[str] = None
    page_number: Optional[int] = None
    kind: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    elements: List[DocumentElement]
    file_type: str
    warnings: List[str] = field(default_factory=list)
    ocr_pages: List[int] = field(default_factory=list)  # PDF pages whose text exists only as images


def markdown_table(rows: List[List[str]]) -> str:
    """Renders rows (first row = header) as a Markdown table."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [[_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


def _cell(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def table_elements(
    header: List[str],
    rows: List[List[str]],
    section: Optional[str],
    page_number: Optional[int] = None,
    max_chars: int = 1600,
    row_numbers: Optional[List[int]] = None,
) -> List[DocumentElement]:
    """Splits a table into row groups that each repeat the header, so every chunk is self-describing.

    Narrow tables become Markdown tables; wide ones (more than 8 columns) become one
    "Header: value" record per row, which stays readable when most cells are empty.
    """
    elements: List[DocumentElement] = []
    numbers = row_numbers or list(range(2, len(rows) + 2))
    wide = len(header) > 8
    group: List[List[str]] = []
    group_start = numbers[0] if numbers else 2
    size = 0

    def render(rows_: List[List[str]]) -> str:
        if not wide:
            return markdown_table([header] + rows_)
        lines = []
        for r in rows_:
            pairs = [f"{h or f'Column {i + 1}'}: {_cell(v)}" for i, (h, v) in enumerate(zip(header, r, strict=False)) if _cell(v)]
            lines.append("; ".join(pairs))
        return "\n".join(lines)

    def flush(end_row: int) -> None:
        if group:
            label = f"{section}, rows {group_start}-{end_row}" if section else f"Rows {group_start}-{end_row}"
            elements.append(
                DocumentElement(text=render(group), section=section, page_number=page_number, kind="table",
                                metadata={"rows": f"{group_start}-{end_row}", "location": label})
            )

    for offset, row in enumerate(rows):
        if wide:  # records repeat the column name before every value
            row_len = sum(len(h or "") + len(_cell(v)) + 4 for h, v in zip(header, row, strict=False) if _cell(v)) + 1
        else:
            row_len = sum(len(_cell(c)) + 3 for c in row)
        if group and size + row_len > max_chars:
            flush(numbers[offset - 1])
            group, size, group_start = [], 0, numbers[offset]
        group.append(row)
        size += row_len
    if rows:
        flush(numbers[len(rows) - 1])
    return elements
