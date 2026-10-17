import io
from datetime import date, datetime, time
from typing import Any, List, Optional

import openpyxl

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, table_elements

MAX_ROWS_PER_SHEET = 20000


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


def find_header(rows: List[List[str]]) -> int:
    """Index of the first row that looks like a header (2+ cells, mostly non-numeric)."""
    for i, row in enumerate(rows[:20]):
        filled = [c for c in row if c]
        if len(filled) >= 2 and sum(not _is_number(c) for c in filled) >= len(filled) * 0.6:
            return i
    return 0


def rows_to_elements(rows: List[List[str]], section: str, warnings: List[str]) -> List[DocumentElement]:
    numbered = [(i + 1, r) for i, r in enumerate(rows) if any(r)]  # keep 1-based sheet row numbers
    if not numbered:
        return []
    numbers = [n for n, _ in numbered]
    rows = [r for _, r in numbered]
    # Drop columns that are empty in every row.
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [i for i in range(width) if any(r[i] for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]

    h = find_header(rows)
    preamble = rows[:h]
    header = [c or f"Column {i + 1}" for i, c in enumerate(rows[h])]
    body = rows[h + 1:]
    elements: List[DocumentElement] = []
    if preamble:
        elements.append(DocumentElement(text="\n".join(" ".join(c for c in r if c) for r in preamble), section=section))
    if not body:
        elements.append(DocumentElement(text=" | ".join(header), section=section))
        return elements
    body_numbers = numbers[h + 1:]
    if len(body) > MAX_ROWS_PER_SHEET:
        warnings.append(f"{section}: only the first {MAX_ROWS_PER_SHEET} rows were indexed")
        body, body_numbers = body[:MAX_ROWS_PER_SHEET], body_numbers[:MAX_ROWS_PER_SHEET]
    elements.extend(table_elements(header, body, section, row_numbers=body_numbers))
    return elements


def parse_xlsx(data: bytes) -> ParsedDocument:
    try:
        values_wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        formulas_wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as exc:
        raise UnsupportedDocumentError(f"Unreadable spreadsheet: {exc}") from exc

    elements: List[DocumentElement] = []
    warnings: List[str] = []
    try:
        for sheet_name in values_wb.sheetnames:
            values_ws, formulas_ws = values_wb[sheet_name], formulas_wb[sheet_name]
            rows: List[List[str]] = []
            for value_row, formula_row in zip(values_ws.iter_rows(values_only=True), formulas_ws.iter_rows(values_only=True), strict=False):
                row = []
                for value, formula in zip(value_row, formula_row, strict=False):
                    # Files saved by some tools carry no cached formula results; show the formula instead.
                    row.append(format_cell(value) if value is not None else _formula_text(formula))
                rows.append(row)
                if len(rows) > MAX_ROWS_PER_SHEET + 50:
                    break
            elements.extend(rows_to_elements(rows, f"Sheet: {sheet_name}", warnings))
    finally:
        values_wb.close()
        formulas_wb.close()
    return ParsedDocument(elements=elements, file_type="xlsx", warnings=warnings)


def _formula_text(formula: Optional[Any]) -> str:
    return str(formula) if isinstance(formula, str) and formula.startswith("=") else format_cell(formula)


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", ""))
        return True
    except ValueError:
        return False
