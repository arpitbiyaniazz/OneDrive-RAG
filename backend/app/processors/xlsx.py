import io
import openpyxl
from typing import List
from app.processors.base import BaseDocumentParser, DocumentElement, ParsedDocument


class XLSXParser(BaseDocumentParser):
    """Parses Excel spreadsheets, converting each sheet into structured tabular text."""

    def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        elements: List[DocumentElement] = []
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)

        for sheet_idx, sheet_name in enumerate(wb.sheetnames):
            ws = wb[sheet_name]
            rows: List[List[str]] = []
            for row in ws.iter_rows(values_only=True):
                # Clean row values
                str_row = [str(cell).strip() if cell is not None else "" for cell in row]
                if any(str_row):  # Skip completely empty rows
                    rows.append(str_row)

            if not rows:
                continue

            # Format as Markdown table
            headers = rows[0]
            header_line = "| " + " | ".join(headers) + " |"
            sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
            table_lines = [header_line, sep_line]

            for data_row in rows[1:]:
                # Pad or trim data_row to header length
                padded = data_row + [""] * max(0, len(headers) - len(data_row))
                table_lines.append("| " + " | ".join(padded[:len(headers)]) + " |")

            sheet_text = f"### Sheet: {sheet_name}\n" + "\n".join(table_lines)
            elements.append(
                DocumentElement(
                    text=sheet_text,
                    section=f"Sheet: {sheet_name}",
                    metadata={
                        "sheet_name": sheet_name,
                        "row_count": len(rows),
                        "column_count": len(headers),
                        "sheet_index": sheet_idx + 1,
                    }
                )
            )

        return ParsedDocument(
            elements=elements,
            file_type="xlsx",
            total_pages_or_slides=len(elements),
            metadata={"filename": filename, "format": "xlsx", "sheet_count": len(elements)}
        )
