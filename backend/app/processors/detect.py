import io
import os
import zipfile
from typing import Optional

from app.processors.base import UnsupportedDocumentError

TEXT_EXTENSIONS = {"txt", "text", "log", "md", "markdown", "csv", "tsv", "html", "htm", "json", "xml", "yaml", "yml", "rst"}
SUPPORTED_EXTENSIONS = {"pdf", "docx", "xlsx", "xlsm", "pptx"} | TEXT_EXTENSIONS
GOOGLE_EXPORTABLE_MIME_PREFIX = "application/vnd.google-apps."


def is_supported(filename: str, mime_type: Optional[str] = None) -> bool:
    """Cheap pre-check used by connectors and the file browser (content is re-checked on parse)."""
    if mime_type and mime_type.startswith(GOOGLE_EXPORTABLE_MIME_PREFIX):
        return mime_type.rsplit(".", 1)[-1] in {"document", "spreadsheet", "presentation"}
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext in SUPPORTED_EXTENSIONS:
        return True
    return bool(mime_type) and (mime_type.startswith("text/") or mime_type == "application/pdf")


def detect_file_type(data: bytes, filename: str, mime_type: Optional[str] = None) -> str:
    """Returns pdf | docx | xlsx | pptx | csv | md | html | txt based on the bytes, not just the name."""
    if not data or not data.strip():
        raise UnsupportedDocumentError("The file is empty")

    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _detect_zip(data)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise UnsupportedDocumentError(
            "Legacy binary Office format (.doc/.xls/.ppt). Save it as .docx/.xlsx/.pptx to index it."
        )
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*", b"RIFF")):
        raise UnsupportedDocumentError("Images are not supported (no OCR)")
    if head.startswith((b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!")):
        raise UnsupportedDocumentError("Compressed archives are not supported")
    if head.startswith(b"{\\rtf"):
        raise UnsupportedDocumentError("RTF documents are not supported. Save as .docx or .txt.")

    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if not _looks_like_text(data):
        raise UnsupportedDocumentError("Binary file format is not supported")
    if ext in {"csv", "tsv"} or (mime_type or "").endswith("csv"):
        return "csv"
    if ext in {"md", "markdown"}:
        return "md"
    if ext in {"html", "htm"} or (mime_type or "").startswith("text/html"):
        return "html"
    return "txt"


def _detect_zip(data: bytes) -> str:
    try:
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocumentError("Corrupted ZIP/Office file") from exc
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "content.xml" in names:
        raise UnsupportedDocumentError("OpenDocument files (.odt/.ods/.odp) are not supported")
    raise UnsupportedDocumentError("ZIP archives are not supported")


def _looks_like_text(data: bytes) -> bool:
    sample = data[:8192]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in sample:
        # UTF-16 without BOM has NULs in every other byte; anything else with NULs is binary.
        even, odd = sample[0::2].count(0), sample[1::2].count(0)
        return max(even, odd) > len(sample) * 0.4 and min(even, odd) < len(sample) * 0.05
    control = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
    return control <= len(sample) * 0.02
