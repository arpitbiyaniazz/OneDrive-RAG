from typing import Optional

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError
from app.processors.detect import IMAGE_TYPES, as_standard_office_package, detect_file_type, is_supported
from app.processors.docx import parse_docx
from app.processors.pdf import parse_pdf
from app.processors.pptx import parse_pptx
from app.processors.text import parse_text
from app.processors.xlsx import parse_xlsx

_PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "xlsx": parse_xlsx, "pptx": parse_pptx}


def parse_document(data: bytes, filename: str, mime_type: Optional[str] = None) -> ParsedDocument:
    """Detects the real file type from its bytes and extracts structured text.

    Raises UnsupportedDocumentError with a user-facing reason when the file cannot be indexed;
    `needs_ocr` is set on it for scans and images.
    """
    file_type = detect_file_type(data, filename, mime_type)
    if file_type in IMAGE_TYPES:
        raise UnsupportedDocumentError(
            "Images can only be indexed with OCR, which is turned off", needs_ocr=True, file_type=file_type
        )
    if file_type in ("docx", "pptx"):
        data = as_standard_office_package(data)
    parser = _PARSERS.get(file_type)
    parsed = parser(data) if parser else parse_text(data, file_type)
    if not any(e.text.strip() for e in parsed.elements):
        if parsed.ocr_pages:
            raise UnsupportedDocumentError("Scanned PDF without a text layer (OCR is turned off)",
                                           needs_ocr=True, file_type=file_type)
        raise UnsupportedDocumentError("No text could be extracted from the file")
    return parsed


__all__ = [
    "DocumentElement",
    "IMAGE_TYPES",
    "ParsedDocument",
    "UnsupportedDocumentError",
    "is_supported",
    "parse_document",
]
