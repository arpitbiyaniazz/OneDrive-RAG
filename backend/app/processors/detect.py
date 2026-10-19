import io
import os
import re
import zipfile
from typing import Dict, Optional

from app.processors.base import UnsupportedDocumentError

TEXT_EXTENSIONS = {"txt", "text", "log", "md", "markdown", "csv", "tsv", "html", "htm", "json", "xml", "yaml", "yml", "rst"}
# Office variants (macro-enabled, templates, slide shows) are the same packages under other content types.
OFFICE_EXTENSIONS = {"docx", "docm", "dotx", "dotm", "xlsx", "xlsm", "xltx", "xltm", "pptx", "pptm", "ppsx", "ppsm",
                     "potx", "potm"}
SUPPORTED_EXTENSIONS = {"pdf"} | OFFICE_EXTENSIONS | TEXT_EXTENSIONS
# Indexed only with OCR (OCR_PROVIDER); detect_file_type returns these names for them.
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "tif", "tiff", "gif", "bmp", "webp"}
IMAGE_TYPES = {"png", "jpeg", "tiff", "gif", "bmp", "webp"}
GOOGLE_EXPORTABLE_MIME_PREFIX = "application/vnd.google-apps."
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")  # OLE stream name of password-protected OOXML


def is_supported(filename: str, mime_type: Optional[str] = None, allow_images: bool = False) -> bool:
    """Cheap pre-check used by connectors and the file browser (content is re-checked on parse)."""
    if mime_type and mime_type.startswith(GOOGLE_EXPORTABLE_MIME_PREFIX):
        return mime_type.rsplit(".", 1)[-1] in {"document", "spreadsheet", "presentation"}
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext in SUPPORTED_EXTENSIONS:
        return True
    if allow_images and (ext in IMAGE_EXTENSIONS or (mime_type or "").startswith("image/")):
        return True
    return bool(mime_type) and (mime_type.startswith("text/") or mime_type == "application/pdf")


def detect_file_type(data: bytes, filename: str, mime_type: Optional[str] = None) -> str:
    """Returns pdf | docx | xlsx | pptx | csv | md | html | txt, or an image type (png, jpeg, tiff,
    gif, bmp, webp), based on the bytes rather than the name."""
    if not data or not data.strip():
        raise UnsupportedDocumentError("The file is empty")

    head = data[:12]
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _detect_zip(data)
    if head.startswith(_OLE2):
        if _ENCRYPTED_PACKAGE in data:
            raise UnsupportedDocumentError("Password-protected Office file. Remove the password to index it.")
        raise UnsupportedDocumentError(
            "Legacy binary Office format (.doc/.xls/.ppt). Save it as .docx/.xlsx/.pptx to index it."
        )
    image = _image_type(head, ext)
    if image:
        return image
    if head.startswith(b"RIFF"):
        raise UnsupportedDocumentError("Audio and video files are not supported")
    if head.startswith((b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!")):
        raise UnsupportedDocumentError("Compressed archives are not supported")
    if head.startswith(b"{\\rtf"):
        raise UnsupportedDocumentError("RTF documents are not supported. Save as .docx or .txt.")

    if not _looks_like_text(data):
        raise UnsupportedDocumentError("Binary file format is not supported")
    if ext in {"csv", "tsv"} or (mime_type or "").endswith("csv"):
        return "csv"
    if ext in {"md", "markdown"}:
        return "md"
    if ext in {"html", "htm"} or (mime_type or "").startswith("text/html"):
        return "html"
    return "txt"


_OFFICE_DOCUMENT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
_MAIN_CONTENT_TYPES = {
    # Word: macro-enabled documents and templates
    "application/vnd.ms-word.document.macroEnabled.main+xml": "wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml": "wordprocessingml.document",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml": "wordprocessingml.document",
    # PowerPoint: macro-enabled, slide shows and templates
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml": "presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml": "presentationml.presentation",
    "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml": "presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml": "presentationml.presentation",
    "application/vnd.ms-powerpoint.template.macroEnabled.main+xml": "presentationml.presentation",
}


def _main_part(data: bytes) -> Optional[str]:
    """Path of the package's main part from _rels/.rels (it is not always word/document.xml)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            rels = package.read("_rels/.rels").decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile):
        return None
    for match in re.finditer(r"<Relationship\b[^>]*>", rels):
        tag = match.group(0)
        if _OFFICE_DOCUMENT_REL in tag:
            target = re.search(r'Target="([^"]+)"', tag)
            return target.group(1).lstrip("/") if target else None
    return None


def as_standard_office_package(data: bytes) -> bytes:
    """Rewrites the main content type of macro-enabled/template/slide-show packages so the
    .docx/.pptx readers accept them. Returns the bytes unchanged when nothing needs rewriting."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            types = package.read("[Content_Types].xml").decode("utf-8")
            if not any(variant in types for variant in _MAIN_CONTENT_TYPES):
                return data
            for variant, standard in _MAIN_CONTENT_TYPES.items():
                types = types.replace(variant, f"application/vnd.openxmlformats-officedocument.{standard}.main+xml")
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as rewritten:
                for info in package.infolist():
                    body = types.encode("utf-8") if info.filename == "[Content_Types].xml" else package.read(info)
                    rewritten.writestr(info, body)
            return out.getvalue()
    except (KeyError, zipfile.BadZipFile, UnicodeDecodeError):
        return data


def _image_type(head: bytes, ext: str) -> Optional[str]:
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if head.startswith(b"GIF8"):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"BM") and ext == "bmp":  # "BM" alone is too common to trust
        return "bmp"
    return None


def _detect_zip(data: bytes) -> str:
    try:
        infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocumentError("Corrupted ZIP/Office file") from exc
    # Office files are ZIP packages; refuse "zip bombs" before a parser inflates every part in memory.
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise UnsupportedDocumentError("The file is too large once uncompressed")
    names = {i.filename for i in infos}
    main = _main_part(data) or ""
    if "word/document.xml" in names or main.startswith("word/"):
        return "docx"
    if "xl/workbook.xml" in names or (main.startswith("xl/") and main.endswith(".xml")):
        return "xlsx"
    if "ppt/presentation.xml" in names or main.startswith("ppt/"):
        return "pptx"
    if "xl/workbook.bin" in names:
        raise UnsupportedDocumentError("Binary Excel workbooks (.xlsb) are not supported. Save it as .xlsx to index it.")
    if "content.xml" in names:
        raise UnsupportedDocumentError("OpenDocument files (.odt/.ods/.odp) are not supported")
    raise UnsupportedDocumentError("ZIP archives are not supported")


def _looks_like_text(data: bytes) -> bool:
    sample = data[:8192]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in sample:
        # UTF-16 without a BOM; anything else with NUL bytes is binary.
        return utf16_without_bom(data) is not None
    control = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
    return control <= len(sample) * 0.02


def _script(ch: str) -> int:
    code = ord(ch)
    for index, (low, high) in enumerate(_SCRIPT_RANGES):
        if low <= code <= high:
            return index
    return -1


# Latin, Greek/Cyrillic, Hebrew/Arabic, Indic, punctuation, CJK, Hangul, full-width forms.
_SCRIPT_RANGES = [(0x0000, 0x024F), (0x0370, 0x052F), (0x0590, 0x06FF), (0x0900, 0x0DFF), (0x2000, 0x206F),
                  (0x3000, 0x9FFF), (0xAC00, 0xD7AF), (0xFF00, 0xFFEF)]


def utf16_without_bom(data: bytes) -> Optional[str]:
    """'utf-16-le' or 'utf-16-be' when the bytes read as text in one or two scripts, else None.
    Binary data decoded as UTF-16 scatters over many unrelated blocks."""
    sample = data[:8192]
    sample = sample[: len(sample) - len(sample) % 2]
    if len(sample) < 4:
        return None
    for encoding in ("utf-16-le", "utf-16-be"):
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        if any((ord(c) < 32 and c not in "\t\n\r\f") for c in text):
            continue
        scripts: Dict[int, int] = {}
        for ch in text:
            scripts[_script(ch)] = scripts.get(_script(ch), 0) + 1
        top = sorted(scripts.values(), reverse=True)
        if -1 not in scripts and sum(top[:3]) >= len(text) * 0.98:
            return encoding
    return None
