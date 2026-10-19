"""Optional OCR for scanned PDFs and images (OCR_PROVIDER=mistral) through Mistral's /v1/ocr API.

Every request sends a PDF: images are converted first, and only the scanned pages of a PDF that
also has a text layer are sent. Pages come back as Markdown (tables included).
"""
import asyncio
import base64
import io
import logging
import re
from typing import List

import httpx
import pymupdf

from app.core.config import settings
from app.processors.base import DocumentElement, ParsedDocument
from app.services.connectors.base import http_client

logger = logging.getLogger(__name__)

_PAGES_PER_REQUEST = 30
_PARALLEL_REQUESTS = 2
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_PERMANENT_STATUSES = {400, 413, 415, 422}  # the document itself was rejected
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class OCRError(Exception):
    """OCR could not produce text; the message is safe to show to the user.

    `permanent` errors (unreadable image, no text, document rejected) are not retried; others
    (network, rate limits, credentials) are, on the next sync.
    """

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def ocr_enabled() -> bool:
    return settings.OCR_PROVIDER == "mistral"


def _image_to_pdf(data: bytes, image_type: str) -> bytes:
    try:
        with pymupdf.open(stream=data, filetype=image_type) as image:
            return image.convert_to_pdf()
    except Exception:
        pass
    # Formats MuPDF cannot read (e.g. some WebP/TIFF variants): re-encode every frame as PNG.
    from PIL import Image, ImageSequence

    out = pymupdf.open()
    try:
        with Image.open(io.BytesIO(data)) as image:
            for frame in ImageSequence.Iterator(image):
                buf = io.BytesIO()
                frame.convert("RGB").save(buf, format="PNG")
                with pymupdf.open(stream=buf.getvalue(), filetype="png") as png:
                    out.insert_pdf(pymupdf.open(stream=png.convert_to_pdf(), filetype="pdf"))
        return out.tobytes()
    except Exception as exc:
        raise OCRError("The image could not be read", permanent=True) from exc
    finally:
        out.close()


def _page_count(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


def _subset(pdf: bytes, pages: List[int]) -> bytes:
    with pymupdf.open(stream=pdf, filetype="pdf") as src, pymupdf.open() as out:
        for number in pages:
            out.insert_pdf(src, from_page=number - 1, to_page=number - 1)
        return out.tobytes()


def _clean(markdown: str) -> str:
    text = _IMAGE_REF.sub("", markdown)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def _request(pdf: bytes) -> List[str]:
    payload = {
        "model": settings.OCR_MODEL,
        "document": {"type": "document_url",
                     "document_url": "data:application/pdf;base64," + base64.b64encode(pdf).decode("ascii")},
        "include_image_base64": False,
    }
    headers = {"Authorization": f"Bearer {settings.MISTRAL_API_KEY}"}
    url = settings.MISTRAL_API_BASE.rstrip("/") + "/ocr"
    delay = 2.0
    for attempt in range(4):
        try:
            res = await http_client().post(url, json=payload, headers=headers, timeout=settings.OCR_TIMEOUT_SECONDS)
        except httpx.TransportError as exc:
            error = OCRError(f"OCR service unreachable ({type(exc).__name__})")
        else:
            if res.status_code == 200:
                pages = sorted(res.json().get("pages") or [], key=lambda p: p.get("index", 0))
                return [_clean(p.get("markdown") or "") for p in pages]
            if res.status_code not in _RETRY_STATUSES:
                logger.warning("OCR request rejected (%s): %s", res.status_code, res.text[:300])
                raise OCRError(f"OCR request rejected ({res.status_code})", permanent=res.status_code in _PERMANENT_STATUSES)
            error = OCRError(f"OCR service error ({res.status_code})")
        if attempt == 3:
            raise error
        logger.warning("%s; retrying in %.0fs", error, delay)
        await asyncio.sleep(delay)
        delay *= 2
    raise OCRError("OCR failed")


async def recognize(pdf: bytes, pages: List[int]) -> List[str]:
    """Markdown text of the given 1-based pages, in the same order."""
    batches = [pages[i:i + _PAGES_PER_REQUEST] for i in range(0, len(pages), _PAGES_PER_REQUEST)]
    gate = asyncio.Semaphore(_PARALLEL_REQUESTS)

    async def run(batch: List[int]) -> List[str]:
        async with gate:
            texts = await _request(await asyncio.to_thread(_subset, pdf, batch))
        return (texts + [""] * len(batch))[: len(batch)]

    results = await asyncio.gather(*(run(b) for b in batches))
    return [text for batch in results for text in batch]


async def ocr_document(data: bytes, file_type: str) -> ParsedDocument:
    """Full OCR of a scanned PDF or an image."""
    pdf = data if file_type == "pdf" else await asyncio.to_thread(_image_to_pdf, data, file_type)
    total = await asyncio.to_thread(_page_count, pdf)
    pages = list(range(1, min(total, settings.OCR_MAX_PAGES) + 1))
    texts = await recognize(pdf, pages)
    elements = [DocumentElement(text=t, page_number=n if file_type == "pdf" or total > 1 else None)
                for n, t in zip(pages, texts, strict=False) if t]
    if not elements:
        raise OCRError("OCR found no text", permanent=True)
    warnings = ["Text was recognized with OCR"]
    if total > len(pages):
        warnings.append(f"Only the first {len(pages)} of {total} pages were read (OCR_MAX_PAGES)")
    return ParsedDocument(elements=elements, file_type=file_type, warnings=warnings)


async def ocr_scanned_pages(data: bytes, parsed: ParsedDocument) -> ParsedDocument:
    """Replaces the (near-empty) text of a PDF's scanned pages with their OCR text."""
    pages = parsed.ocr_pages[: settings.OCR_MAX_PAGES]
    texts = await recognize(data, pages)
    recognized = {n: t for n, t in zip(pages, texts, strict=False) if t}
    if not recognized:
        return parsed
    elements: List[DocumentElement] = []
    inserted = set()
    for element in parsed.elements:
        number = element.page_number
        if number in recognized:
            if number not in inserted:
                elements.append(DocumentElement(text=recognized[number], section=element.section, page_number=number))
                inserted.add(number)
            continue
        elements.append(element)
    # Pages that had no text element at all: place them by page number.
    for number in sorted(set(recognized) - inserted):
        position = next((i for i, e in enumerate(elements) if e.page_number and e.page_number > number), len(elements))
        elements.insert(position, DocumentElement(text=recognized[number], page_number=number))
    warnings = parsed.warnings + [f"Pages {_ranges(sorted(recognized))} were read with OCR"]
    return ParsedDocument(elements=elements, file_type=parsed.file_type, warnings=warnings)


def _ranges(numbers: List[int]) -> str:
    parts, start, prev = [], None, None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            parts.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(parts)
