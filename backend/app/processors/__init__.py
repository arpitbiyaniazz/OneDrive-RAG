from typing import Optional

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError
from app.processors.detect import detect_file_type, is_supported
from app.processors.docx import parse_docx
from app.processors.pdf import parse_pdf
from app.processors.pptx import parse_pptx
from app.processors.text import parse_text
from app.processors.xlsx import parse_xlsx


def parse_document(data: bytes, filename: str, mime_type: Optional[str] = None) -> ParsedDocument:
    """Detects the real file type from its bytes and extracts structured text.

    Raises UnsupportedDocumentError with a user-facing reason when the file cannot be indexed.
    """
    file_type = detect_file_type(data, filename, mime_type)
    if file_type == "pdf":
        parsed = parse_pdf(data)
    elif file_type == "docx":
        parsed = parse_docx(data)
    elif file_type == "xlsx":
        parsed = parse_xlsx(data)
    elif file_type == "pptx":
        parsed = parse_pptx(data)
    else:
        parsed = parse_text(data, file_type)
    if not any(e.text.strip() for e in parsed.elements):
        raise UnsupportedDocumentError("No text could be extracted from the file")
    return parsed


__all__ = [
    "DocumentElement",
    "ParsedDocument",
    "UnsupportedDocumentError",
    "is_supported",
    "parse_document",
]
